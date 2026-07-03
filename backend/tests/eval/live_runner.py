"""Live-model eval runner for Pilot's public-demo scenario (gate 7/8).

Where ``runner.py`` replays scenarios with EVERYTHING stubbed (no Ollama, no
network — the deterministic pytest suite), this runner drives the **real** agent
against a **live local Ollama model** and measures it. It is the "live-model
mode" the public-demo scope (docs/public-demo-scope.md §4) requires so the demo
is measured, not just shown.

One turn is executed through the same public functions ``api/ws.py`` uses for a
non-offloaded turn:

    build_task_context -> classify_turn -> build_routing_decision
        -> run_coordinator -> compose_reply

Nothing about the model or its tool decisions is stubbed; only the OUTER harness
differs (a fresh temp workspace per task instead of a live WebSocket session, and
no delegation to the external Codex/Claude CLIs). Each task carries a
deterministic checker, so the pass/fail signal never depends on a model grading
itself.

Deliberate divergence from ``api/ws.py``: for a file-output turn, ws.py has a
safety net — when the model fails to write/verify the file it writes a fallback
report and records a verified artifact so the *user* still succeeds. This runner
does NOT reproduce that fallback: it measures the model's UNAIDED
research→write→verify ability, so a file task fails here exactly when the model
itself fails (production would still cover the user). That is intentional — the
eval is a capability measurement, not a product-success measurement.

Run it (one command, from the ``backend`` directory)::

    uv run python -m tests.eval.live_runner

It writes two reports into ``tests/eval/results/``: ``latest.json`` (machine
readable, diff-friendly) and ``latest.md`` (human readable). Exit code is 0 when
every safety gate passed AND the primary-scenario tasks passed; non-zero
otherwise, so CI or a pre-submit check can gate on it.

Fail-closed: if Ollama is unreachable or the default coordinator model is not
installed, the runner reports that and exits non-zero WITHOUT inventing results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import httpx  # noqa: E402

from agents import providers  # noqa: E402
from agents.coordinator import run_coordinator  # noqa: E402
from agents.model_inventory import ModelInventory, get_model_inventory  # noqa: E402
from agents.orchestrator import classify_turn, compose_reply  # noqa: E402
from agents.routing import build_routing_decision  # noqa: E402
from agents.turn_policy import (  # noqa: E402
    build_task_context,
    resolve_task_contract_intent,
    select_agent_for_intent,
    tool_task,
)
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OPENAI_MODEL  # noqa: E402


# Approximate USD price per 1K tokens (input, output) for cost estimates on the
# OpenAI path. Local Ollama is $0. Rough public list prices; override as needed.
# Only used to annotate the report — not a billing source of truth.
_PRICES_PER_1K = {
    "gpt-4o-mini": (0.00015, 0.00060),
    "gpt-4o": (0.0025, 0.010),
    "gpt-4.1": (0.002, 0.008),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1-nano": (0.0001, 0.0004),
}


def _cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = _PRICES_PER_1K.get(model)
    if not price:
        return 0.0
    return round(prompt_tokens / 1000 * price[0] + completion_tokens / 1000 * price[1], 6)


# --------------------------------------------------------------------------- #
# Failure taxonomy (docs/public-demo-scope.md §4)
# --------------------------------------------------------------------------- #

# Each failing task is labelled with exactly one of these so the report can show
# *why* the suite failed, not just that it did. "safety_breach" is the worst — a
# safety gate that let something through — and is called out separately.
WRONG_TOOL = "wrong_tool"
UNGROUNDED = "ungrounded_answer"
MISSING_VERIFICATION = "missing_verification"
SAFETY_OVER_BLOCK = "safety_over_block"
SAFETY_BREACH = "safety_breach"
WRONG_ANSWER = "wrong_answer"
MODEL_ERROR = "model_error"
TIMEOUT = "timeout"

FAILURE_LABELS = (
    WRONG_TOOL,
    UNGROUNDED,
    MISSING_VERIFICATION,
    SAFETY_OVER_BLOCK,
    SAFETY_BREACH,
    WRONG_ANSWER,
    MODEL_ERROR,
    TIMEOUT,
)


# --------------------------------------------------------------------------- #
# Declarative task + result types
# --------------------------------------------------------------------------- #


@dataclass
class LiveTurn:
    """The observable outcome of one real agent turn — the surface checkers read.

    This is the live analogue of ``ScenarioResult``: it is built from the real
    ``LoopOutcome`` + emitted events + composed reply, not from stubs.
    """

    route: str = ""
    coordinator_model: str = ""
    status: str = ""  # LoopOutcome.status: done|needs_input|max_steps|error|aborted
    tools_called: list[str] = field(default_factory=list)  # from "action" events, in order
    evidence_tools: list[str] = field(default_factory=list)  # from runtime_state.evidence_items
    contract_satisfied: bool | None = None
    verified_artifacts: list[str] = field(default_factory=list)  # verified file paths
    needs_input: bool = False  # a confirmation/clarify halted the turn
    final_text: str = ""
    action_log: str = ""
    cwd: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    escalated: bool = False  # a coder specialist was escalated to this turn
    error: str | None = None  # transport/model error, if the turn could not run


@dataclass
class CheckResult:
    passed: bool
    failure_label: str | None = None
    detail: str = ""


# A checker takes the observed turn plus its task and returns a CheckResult.
Checker = Callable[["LiveTurn", "LiveTask"], CheckResult]
# Optional workspace setup: write fixture files into the task's cwd before it runs.
Setup = Callable[[Path], None]


@dataclass
class LiveTask:
    """One live task: a real user turn plus a deterministic checker.

    ``workspace`` is "fresh" (an isolated temp dir, optionally seeded by
    ``setup``) or "repo" (run against the real Pilot repo root, read-only tasks
    only). ``requires_network`` tasks are skipped (not failed) when the machine is
    offline. ``safety_gate`` tasks are pass/fail gates — a single failure fails
    the whole suite regardless of the averaged solve rate.
    """

    name: str
    category: str
    message: str
    check: Checker
    route_mode: str = "auto"
    model_mode: str = "auto"
    workspace: str = "fresh"  # "fresh" | "repo"
    setup: Setup | None = None
    memories: str = ""
    requires_network: bool = False
    safety_gate: bool = False
    primary: bool = False  # part of the scope's primary scenario (must pass)
    # Verification-escalation (code_task) support:
    contract_intent: str | None = None  # force a contract intent (e.g. "code_task")
    code_task_spec: dict | None = None  # {solution_path, verify_command, spec}
    escalation: bool = True  # escalate to a coder specialist on a verified failure


@dataclass
class LiveResult:
    name: str
    category: str
    status: str  # "pass" | "fail" | "skip"
    passed: bool
    skipped: bool
    latency_s: float
    failure_label: str | None
    detail: str
    turn_status: str
    tools_called: list[str]
    safety_gate: bool
    primary: bool
    final_excerpt: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    escalated: bool = False


# --------------------------------------------------------------------------- #
# Real turn execution (mirrors api/ws.py's non-offload path)
# --------------------------------------------------------------------------- #


def _intent_hint(route: str, task_context) -> tuple[str, dict | None]:
    """Reproduce the intent hint + required-first-tool that api/ws.py builds.

    Kept in lockstep with ws.py's non-offload branch: the coordinator's behaviour
    (which grounding it is nudged toward, and the forced first list_dir for a
    project analysis) materially depends on these, so an honest eval must pass the
    same ones. The exact prose is not asserted on — only observable behaviour is.
    """
    if route == "computer":
        intent = (
            "The user wants you to do something on this computer or find something "
            "out; act or consult when it helps."
        )
        if task_context.requires_current_sources:
            intent += (
                " This task needs current or externally verified information: use "
                "web_research with a standalone query and ground the answer in the "
                "sources you fetched."
            )
        if task_context.creates_file:
            intent += (
                " The user expects a local output file: gather the needed data, "
                "write the requested file with the write_file tool, and report "
                "the exact path."
            )
    elif route == "code":
        intent = (
            "The user wants to work on the active project's code/files. Inspect with "
            "read_file/list_dir/search_files, use run_command for tests, git and "
            "builds, and consult the coder model for code. Work locally."
        )
    else:
        intent = (
            "The user's message looks conversational. If they ask you to find "
            "something out, look something up, or do something on the computer, USE "
            "the right tool rather than just describing it; otherwise just answer."
        )
    required_first_tool = None
    if task_context.intent == "project_analysis":
        intent += (
            " This is a project/backend flow analysis: inspect local files with "
            "list_dir/search_files/read_file before answering. Do not answer from "
            "general knowledge or claim no project data is available until you have "
            "tried the file tools."
        )
        required_first_tool = {"tool": "list_dir", "args": {"path": "."}}
    if task_context.creates_file:
        intent += (
            " This task requires a local output file. Do not answer as complete "
            "until you have written the file with the write_file tool (which verifies it)."
        )
    return intent, required_first_tool


async def run_live_turn(
    task: LiveTask, cwd: str, inventory: ModelInventory,
    escalation: bool | None = None,
) -> LiveTurn:
    """Execute one real turn end to end and return its observable outcome.

    ``escalation`` overrides the task's own setting (used by the runner's
    with/without-escalation comparison); None keeps the task default.
    """
    prior: list[dict] = []
    conversation = [{"role": "user", "content": task.message}]
    task_context = build_task_context(prior, task.message)
    effective_task = tool_task(task.message, task_context)
    project = os.path.basename(cwd.rstrip("\\/")) if cwd else None

    events: list[dict] = []
    turn = LiveTurn(cwd=cwd, coordinator_model="")
    providers.reset_usage()  # accumulate token usage across every model call this turn

    try:
        if task.route_mode == "auto":
            decision = await classify_turn(
                prior, task.message, project=project, model_mode=task.model_mode
            )
        else:
            decision = {"route": task.route_mode, "thinking": f"forced route {task.route_mode}"}
        route = decision["route"]
        turn.route = route

        agent_selection = select_agent_for_intent(
            task.model_mode, task_context, available_models=set(inventory.healthy)
        )
        coordinator_model = agent_selection.model
        turn.coordinator_model = coordinator_model

        routing = build_routing_decision(
            route_mode=task.route_mode,
            classified_route=route,
            agent="claude",
            text=task.message,
            project=project,
            cwd=cwd,
        )
        if routing.is_offload():
            # The live runner never delegates to an external CLI; a task that
            # offloads is a routing/task-design error, surfaced not silently run.
            turn.error = f"unexpected offload to {routing.execution_engine}"
            return turn

        intent, required_first_tool = _intent_hint(route, task_context)
        contract_intent = task.contract_intent or resolve_task_contract_intent(task_context)
        outcome = await run_coordinator(
            effective_task,
            events.append,
            asyncio.Event(),
            prior,
            project_cwd=cwd,
            coordinator_model=coordinator_model,
            intent_hint=intent,
            memories=task.memories,
            session_id="live-eval",
            required_first_tool=required_first_tool,
            require_file_output=task_context.creates_file,
            task_contract_intent=contract_intent,
            inventory=inventory,
            code_task_spec=task.code_task_spec,
            escalation_enabled=escalation if escalation is not None else task.escalation,
        )
    except Exception as exc:  # noqa: BLE001 — a transport/model error is a task failure, not a crash
        turn.error = f"{type(exc).__name__}: {exc}"
        return turn

    turn.status = outcome.status
    turn.action_log = outcome.action_log or ""
    turn.tools_called = [
        e.get("tool", "") for e in events if e.get("type") == "action" and e.get("tool")
    ]
    turn.escalated = any(e.get("type") == "escalation" for e in events)
    turn.needs_input = outcome.status == "needs_input"

    rs = outcome.runtime_state
    if rs is not None:
        turn.evidence_tools = [item.get("tool") for item in rs.evidence_items]
        turn.contract_satisfied = (rs.requirements or {}).get("satisfied")
        turn.verified_artifacts = [
            str(a.get("path") or "").strip()
            for a in getattr(rs, "artifacts", [])
            if a.get("verified") and str(a.get("path") or "").strip()
        ]

    if turn.needs_input:
        turn.final_text = outcome.detail or ""
    else:
        try:
            grounding = outcome if (outcome.action_log or task_context.needs_tools) else None
            parts: list[str] = []
            async for chunk in compose_reply(
                conversation, grounding, OLLAMA_MODEL, task.memories
            ):
                if chunk:
                    parts.append(chunk)
            turn.final_text = "".join(parts).strip()
        except Exception as exc:  # noqa: BLE001
            turn.error = f"compose_reply failed: {type(exc).__name__}: {exc}"

    usage = providers.get_usage()
    turn.prompt_tokens = int(usage.get("prompt_tokens", 0))
    turn.completion_tokens = int(usage.get("completion_tokens", 0))
    return turn


# --------------------------------------------------------------------------- #
# Task execution + workspace management
# --------------------------------------------------------------------------- #


def _repo_root() -> Path:
    # backend/tests/eval/live_runner.py -> repo root is three parents up from backend
    return Path(__file__).resolve().parents[3]


async def run_task(
    task: LiveTask, inventory: ModelInventory, network_ok: bool,
    escalation: bool | None = None,
) -> LiveResult:
    """Set up the workspace, run the turn, time it, and apply the checker."""
    if task.requires_network and not network_ok:
        return LiveResult(
            name=task.name, category=task.category, status="skip", passed=False,
            skipped=True, latency_s=0.0, failure_label=None,
            detail="network unavailable; task skipped", turn_status="",
            tools_called=[], safety_gate=task.safety_gate, primary=task.primary,
            final_excerpt="",
        )

    tmp: str | None = None
    if task.workspace == "repo":
        cwd = str(_repo_root())
    else:
        tmp = tempfile.mkdtemp(prefix=f"pilot_eval_{task.name}_")
        cwd = tmp
        if task.setup:
            task.setup(Path(cwd))

    start = time.monotonic()
    try:
        turn = await run_live_turn(task, cwd, inventory, escalation=escalation)
    finally:
        elapsed = time.monotonic() - start

    cost = _cost_usd(providers.answer_model(), turn.prompt_tokens, turn.completion_tokens)
    tokens = {
        "prompt_tokens": turn.prompt_tokens,
        "completion_tokens": turn.completion_tokens,
        "cost_usd": cost,
        "escalated": turn.escalated,
    }
    if turn.error:
        label = TIMEOUT if "timeout" in turn.error.lower() else MODEL_ERROR
        result = LiveResult(
            name=task.name, category=task.category, status="fail", passed=False,
            skipped=False, latency_s=round(elapsed, 2), failure_label=label,
            detail=turn.error, turn_status=turn.status, tools_called=turn.tools_called,
            safety_gate=task.safety_gate, primary=task.primary,
            final_excerpt=turn.final_text[:280], **tokens,
        )
    else:
        check = task.check(turn, task)
        result = LiveResult(
            name=task.name, category=task.category,
            status="pass" if check.passed else "fail",
            passed=check.passed, skipped=False, latency_s=round(elapsed, 2),
            failure_label=None if check.passed else (check.failure_label or WRONG_ANSWER),
            detail=check.detail, turn_status=turn.status,
            tools_called=turn.tools_called, safety_gate=task.safety_gate,
            primary=task.primary, final_excerpt=turn.final_text[:280], **tokens,
        )

    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return result


# --------------------------------------------------------------------------- #
# Aggregation + reporting (pure — unit-tested without Ollama)
# --------------------------------------------------------------------------- #


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in [0, 100]); 0.0 for an empty list.

    Uses ceil, not round(): Python's round() is round-half-to-even, so
    round(0.50*9)=round(4.5)=4 would understate the median of 9 values (the true
    median is the 5th). ceil(pct/100 * n) is the standard nearest-rank rank.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), math.ceil(pct / 100 * len(ordered))))
    return ordered[rank - 1]


def _redact(text: str) -> str:
    """Strip the user's home path / username from any text written to the repo.

    Model answers and tool output can echo the real workspace path
    (e.g. ``C:\\Users\\<name>\\...``); the committed report is public, so redact
    the home directory and the bare username to ``<HOME>`` / ``<user>``.
    """
    if not text:
        return text
    home = os.path.expanduser("~")
    out = text
    for variant in (home, home.replace("\\", "\\\\"), home.replace("\\", "/")):
        out = out.replace(variant, "<HOME>")
    user = os.path.basename(home)
    if user and user.lower() not in ("", "user"):
        out = re.sub(re.escape(user), "<user>", out, flags=re.IGNORECASE)
    return out


def aggregate(
    results: list[LiveResult], *, model: str, timestamp: str, backend: str = "ollama"
) -> dict:
    """Reduce per-task results into the report structure (metrics from §4)."""
    scored = [r for r in results if not r.skipped]
    passed = [r for r in scored if r.passed]
    latencies = [r.latency_s for r in scored]
    total_prompt = sum(r.prompt_tokens for r in scored)
    total_completion = sum(r.completion_tokens for r in scored)
    total_cost = round(sum(r.cost_usd for r in scored), 6)

    by_category: dict[str, dict] = {}
    for r in results:
        cat = by_category.setdefault(
            r.category, {"total": 0, "passed": 0, "skipped": 0}
        )
        if r.skipped:
            cat["skipped"] += 1
        else:
            cat["total"] += 1
            if r.passed:
                cat["passed"] += 1
    for cat in by_category.values():
        cat["solve_rate"] = (
            round(cat["passed"] / cat["total"], 3) if cat["total"] else None
        )

    taxonomy: dict[str, int] = {}
    for r in scored:
        if not r.passed and r.failure_label:
            taxonomy[r.failure_label] = taxonomy.get(r.failure_label, 0) + 1

    safety = [r for r in scored if r.safety_gate]
    safety_failures = [r for r in safety if not r.passed]
    primary = [r for r in results if r.primary]
    primary_failures = [r for r in primary if not r.passed and not r.skipped]
    primary_skipped = [r for r in primary if r.skipped]

    # The suite passes only if every safety gate held AND no primary task failed.
    # A skipped primary task (offline) is a non-pass for gating: the primary
    # scenario has not been demonstrated, so we do not claim success.
    suite_passed = (
        not safety_failures and not primary_failures and not primary_skipped
    )

    return {
        "backend": backend,
        "model": model,
        "timestamp": timestamp,
        "totals": {
            "tasks": len(results),
            "scored": len(scored),
            "passed": len(passed),
            "failed": len(scored) - len(passed),
            "skipped": len(results) - len(scored),
            "solve_rate": round(len(passed) / len(scored), 3) if scored else None,
        },
        "latency_s": {
            "median": round(percentile(latencies, 50), 2),
            "p90": round(percentile(latencies, 90), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "cost": {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "usd": total_cost,
        },
        "by_category": by_category,
        "failure_taxonomy": taxonomy,
        "safety_gates": {
            "total": len(safety),
            "passed": len(safety) - len(safety_failures),
            "failed": [r.name for r in safety_failures],
        },
        "primary_scenario": {
            "total": len(primary),
            "passed": len([r for r in primary if r.passed]),
            "failed": [r.name for r in primary_failures],
            "skipped": [r.name for r in primary_skipped],
        },
        "suite_passed": suite_passed,
        "results": [
            {
                "name": r.name,
                "category": r.category,
                "status": r.status,
                "latency_s": r.latency_s,
                "failure_label": r.failure_label,
                "detail": _redact(r.detail),
                "turn_status": r.turn_status,
                "tools_called": r.tools_called,
                "safety_gate": r.safety_gate,
                "primary": r.primary,
                "final_excerpt": _redact(r.final_excerpt),
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "cost_usd": r.cost_usd,
                "escalated": r.escalated,
            }
            for r in results
        ],
    }


def render_markdown(report: dict) -> str:
    """Render the aggregate report as a human-readable Markdown document."""
    t = report["totals"]
    lat = report["latency_s"]
    lines: list[str] = []
    lines.append("# Pilot live-eval results")
    lines.append("")
    verdict = "✅ PASS" if report["suite_passed"] else "❌ FAIL"
    lines.append(f"**Suite:** {verdict}  ")
    lines.append(f"**Backend:** `{report.get('backend', 'ollama')}`  ")
    lines.append(f"**Model:** `{report['model']}`  ")
    lines.append(f"**Run:** {report['timestamp']}")
    lines.append("")
    solve = t["solve_rate"]
    solve_txt = f"{solve:.0%}" if solve is not None else "n/a"
    lines.append(
        f"Solved **{t['passed']}/{t['scored']}** scored tasks ({solve_txt}); "
        f"{t['skipped']} skipped. "
        f"Latency median **{lat['median']}s**, p90 **{lat['p90']}s**."
    )
    cost = report.get("cost")
    if cost and cost.get("total_tokens"):
        usd = cost.get("usd") or 0.0
        cost_txt = f"~${usd:.4f}" if usd else "$0 (local)"
        lines.append(
            f"Tokens: **{cost['total_tokens']:,}** "
            f"({cost['prompt_tokens']:,} in / {cost['completion_tokens']:,} out); "
            f"cost {cost_txt}."
        )
    lines.append("")

    lines.append("## By category")
    lines.append("")
    lines.append("| Category | Solved | Skipped | Solve rate |")
    lines.append("|---|---|---|---|")
    for cat, c in sorted(report["by_category"].items()):
        rate = c["solve_rate"]
        rate_txt = f"{rate:.0%}" if rate is not None else "—"
        lines.append(
            f"| {cat} | {c['passed']}/{c['total']} | {c['skipped']} | {rate_txt} |"
        )
    lines.append("")

    sg = report["safety_gates"]
    lines.append("## Safety gates (pass/fail)")
    lines.append("")
    if sg["total"] == 0:
        lines.append("_No safety-gate tasks in this run._")
    elif not sg["failed"]:
        lines.append(f"All **{sg['passed']}/{sg['total']}** safety gates held. ✅")
    else:
        lines.append(
            f"**{len(sg['failed'])} safety gate(s) BREACHED:** "
            + ", ".join(f"`{n}`" for n in sg["failed"])
            + " ❌"
        )
    lines.append("")

    if report["failure_taxonomy"]:
        lines.append("## Failure taxonomy")
        lines.append("")
        for label, count in sorted(
            report["failure_taxonomy"].items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"- `{label}`: {count}")
        lines.append("")

    lines.append("## Tasks")
    lines.append("")
    lines.append("| Task | Category | Result | Latency | Failure |")
    lines.append("|---|---|---|---|---|")
    icon = {"pass": "✅", "fail": "❌", "skip": "⏭️"}
    for r in report["results"]:
        flag = " 🔒" if r["safety_gate"] else (" ⭐" if r["primary"] else "")
        fail = f"`{r['failure_label']}`" if r["failure_label"] else ""
        lines.append(
            f"| {r['name']}{flag} | {r['category']} | {icon.get(r['status'], r['status'])} "
            f"| {r['latency_s']}s | {fail} |"
        )
    lines.append("")
    lines.append("Legend: ⭐ primary-scenario task, 🔒 safety gate (pass/fail).")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Health gate + orchestration
# --------------------------------------------------------------------------- #


async def _network_reachable() -> bool:
    """Cheap connectivity probe for network-dependent tasks (web research)."""
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get("https://duckduckgo.com/", follow_redirects=True)
            return resp.status_code < 500
    except Exception:  # noqa: BLE001
        return False


async def _health_gate(backend: str) -> tuple[ModelInventory | None, str | None]:
    """Fail-closed readiness check for the chosen backend.

    Returns (inventory, None) when ready, or (None, message) when not. The Ollama
    inventory is still returned for the openai backend so agent selection has a
    healthy-set to reason over (empty is fine — the openai path ignores it).
    """
    if backend == providers.OPENAI:
        if not providers.openai_configured():
            return None, (
                "OPENAI_API_KEY is not set; cannot run the openai backend. "
                "Add it to backend/.env or the environment."
            )
        # Cheap auth probe (lists models, no token cost).
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{providers.OPENAI_BASE_URL}/models",
                    headers={"Authorization": f"Bearer {providers.OPENAI_API_KEY}"},
                )
            if resp.status_code >= 400:
                return None, f"OpenAI auth failed (HTTP {resp.status_code}); check OPENAI_API_KEY."
        except Exception as exc:  # noqa: BLE001
            return None, f"OpenAI endpoint unreachable: {exc}"
        # Discovery may still fail if Ollama is down; that's fine on this path.
        inventory = await get_model_inventory()
        return inventory, None

    inventory = await get_model_inventory()
    if not inventory.discovery_ok:
        return None, (
            f"Ollama model discovery failed at {OLLAMA_BASE_URL}. Is Ollama running? "
            "Cannot run a live eval without a model."
        )
    if not inventory.is_healthy(OLLAMA_MODEL):
        return None, (
            f"Default coordinator model {OLLAMA_MODEL!r} is not installed "
            f"(healthy models: {sorted(inventory.healthy) or 'none'}). "
            f"Pull it with `ollama pull {OLLAMA_MODEL}` or set OLLAMA_MODEL."
        )
    return inventory, None


async def run_suite(
    tasks: list[LiveTask], backend: str = "ollama", escalation: bool | None = None
) -> tuple[dict | None, str]:
    """Run the whole suite on the chosen backend. Returns (report | None, message).

    Returns ``(None, msg)`` when the health gate fails (Ollama down / model
    missing / OpenAI key invalid) — the caller exits non-zero without fabricating
    results. ``escalation`` (None|True|False) overrides every task's escalation
    setting, for the with/without-escalation comparison.
    """
    backend = providers.resolve_backend(backend)
    providers.set_backend(backend)
    model = providers.answer_model(backend)

    inventory, error = await _health_gate(backend)
    if error:
        return None, error

    network_ok = await _network_reachable()
    results: list[LiveResult] = []
    for i, task in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {task.name} ({task.category})...", flush=True)
        result = await run_task(task, inventory, network_ok, escalation=escalation)
        status = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[result.status]
        extra = f" [{result.failure_label}]" if result.failure_label else ""
        cost = f" ~${result.cost_usd:.4f}" if result.cost_usd else ""
        print(f"        -> {status} in {result.latency_s}s{extra}{cost}", flush=True)
        results.append(result)

    # timestamp is captured by the caller (Date.now is fine outside the harness).
    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    report = aggregate(results, model=model, timestamp=timestamp, backend=backend)
    return report, "ok"


def write_reports(report: dict, out_dir: Path) -> tuple[Path, Path]:
    """Write latest.json/md (most recent run) plus backend-suffixed copies (for
    cross-backend comparison), all with UTF-8."""
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = report.get("backend", "ollama")
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    md = render_markdown(report)
    json_path = out_dir / "latest.json"
    md_path = out_dir / "latest.md"
    for path, text in (
        (json_path, payload),
        (md_path, md),
        (out_dir / f"latest-{backend}.json", payload),
        (out_dir / f"latest-{backend}.md", md),
    ):
        path.write_text(text, encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pilot live-model eval runner")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "results"),
        help="output directory for latest.json / latest.md",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated task-name substrings to run (default: all)",
    )
    parser.add_argument(
        "--backend",
        default=None,
        choices=["ollama", "openai"],
        help="model backend for this run (default: PILOT_ANSWER_BACKEND env, else ollama)",
    )
    parser.add_argument(
        "--escalate",
        default="auto",
        choices=["auto", "on", "off"],
        help="verification-escalation to a coder specialist on code tasks: auto (task "
        "default), on (force), off (disable — for the with/without comparison)",
    )
    args = parser.parse_args(argv)
    escalation = {"auto": None, "on": True, "off": False}[args.escalate]

    # The report uses ✅/❌ icons; a Windows console defaults to cp1252 and would
    # raise on them. Force UTF-8 (replace on any residual) so printing never crashes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    from tests.eval.live_tasks import LIVE_TASKS

    tasks = LIVE_TASKS
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        tasks = [t for t in LIVE_TASKS if any(w in t.name for w in wanted)]
        if not tasks:
            print(f"No tasks matched --only={args.only!r}", file=sys.stderr)
            return 2

    report, message = asyncio.run(run_suite(
        tasks, backend=providers.resolve_backend(args.backend), escalation=escalation
    ))
    if report is None:
        print(f"\nHEALTH GATE FAILED: {message}", file=sys.stderr)
        return 3

    json_path, md_path = write_reports(report, Path(args.out))
    print("\n" + render_markdown(report))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    return 0 if report["suite_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

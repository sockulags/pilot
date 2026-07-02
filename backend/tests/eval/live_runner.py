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
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import httpx  # noqa: E402

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
from config import OLLAMA_BASE_URL, OLLAMA_MODEL  # noqa: E402


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
                "write the requested file with an appropriate command, and report "
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
            "until you have written the file with run_command and verified that it exists."
        )
    return intent, required_first_tool


async def run_live_turn(
    task: LiveTask, cwd: str, inventory: ModelInventory
) -> LiveTurn:
    """Execute one real turn end to end and return its observable outcome."""
    prior: list[dict] = []
    conversation = [{"role": "user", "content": task.message}]
    task_context = build_task_context(prior, task.message)
    effective_task = tool_task(task.message, task_context)
    project = os.path.basename(cwd.rstrip("\\/")) if cwd else None

    events: list[dict] = []
    turn = LiveTurn(cwd=cwd, coordinator_model="")

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
            task_contract_intent=resolve_task_contract_intent(task_context),
            inventory=inventory,
        )
    except Exception as exc:  # noqa: BLE001 — a transport/model error is a task failure, not a crash
        turn.error = f"{type(exc).__name__}: {exc}"
        return turn

    turn.status = outcome.status
    turn.action_log = outcome.action_log or ""
    turn.tools_called = [
        e.get("tool", "") for e in events if e.get("type") == "action" and e.get("tool")
    ]
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

    return turn


# --------------------------------------------------------------------------- #
# Task execution + workspace management
# --------------------------------------------------------------------------- #


def _repo_root() -> Path:
    # backend/tests/eval/live_runner.py -> repo root is three parents up from backend
    return Path(__file__).resolve().parents[3]


async def run_task(
    task: LiveTask, inventory: ModelInventory, network_ok: bool
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
        turn = await run_live_turn(task, cwd, inventory)
    finally:
        elapsed = time.monotonic() - start

    if turn.error:
        label = TIMEOUT if "timeout" in turn.error.lower() else MODEL_ERROR
        result = LiveResult(
            name=task.name, category=task.category, status="fail", passed=False,
            skipped=False, latency_s=round(elapsed, 2), failure_label=label,
            detail=turn.error, turn_status=turn.status, tools_called=turn.tools_called,
            safety_gate=task.safety_gate, primary=task.primary,
            final_excerpt=turn.final_text[:280],
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
            primary=task.primary, final_excerpt=turn.final_text[:280],
        )

    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return result


# --------------------------------------------------------------------------- #
# Aggregation + reporting (pure — unit-tested without Ollama)
# --------------------------------------------------------------------------- #


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in [0, 100]); 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), round(pct / 100 * len(ordered))))
    return ordered[rank - 1]


def aggregate(results: list[LiveResult], *, model: str, timestamp: str) -> dict:
    """Reduce per-task results into the report structure (metrics from §4)."""
    scored = [r for r in results if not r.skipped]
    passed = [r for r in scored if r.passed]
    latencies = [r.latency_s for r in scored]

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
                "detail": r.detail,
                "turn_status": r.turn_status,
                "tools_called": r.tools_called,
                "safety_gate": r.safety_gate,
                "primary": r.primary,
                "final_excerpt": r.final_excerpt,
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


async def run_suite(tasks: list[LiveTask]) -> tuple[dict | None, str]:
    """Run the whole suite. Returns (report | None, human message).

    Returns ``(None, msg)`` when the health gate fails (Ollama down / model
    missing) — the caller exits non-zero without fabricating results.
    """
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

    network_ok = await _network_reachable()
    results: list[LiveResult] = []
    for i, task in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {task.name} ({task.category})...", flush=True)
        result = await run_task(task, inventory, network_ok)
        status = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[result.status]
        extra = f" [{result.failure_label}]" if result.failure_label else ""
        print(f"        -> {status} in {result.latency_s}s{extra}", flush=True)
        results.append(result)

    # timestamp is captured by the caller (Date.now is fine outside the harness).
    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    report = aggregate(results, model=OLLAMA_MODEL, timestamp=timestamp)
    return report, "ok"


def write_reports(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "latest.json"
    md_path = out_dir / "latest.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
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
    args = parser.parse_args(argv)

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

    report, message = asyncio.run(run_suite(tasks))
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

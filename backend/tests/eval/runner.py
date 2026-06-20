"""Deterministic scenario runner for agent-flow + adversarial evals (issue #44).

A :class:`Scenario` is a declarative description of one turn: the input message
and session state, the stubbed model/tool responses, and the expected
assertions. :func:`run_scenario` executes the relevant agent path with EVERYTHING
stubbed (no Ollama, no real OS, no network) and returns a structured
:class:`ScenarioResult` the test asserts on.

Two execution paths, picked by ``Scenario.path``:

- ``"routing"`` drives the pure router (``agents.routing.build_routing_decision``
  + ``agents.orchestrator.should_offload_code``) with the classifier route given
  declaratively. It asserts the chosen route / execution_engine — i.e. whether a
  code task is kept local or offloaded ("använd codex"). No model is called.

- ``"coordinator"`` drives ``agents.coordinator.run_coordinator`` with the
  decision stream and tool outputs stubbed exactly like ``tests/test_coordinator``
  does (``_decide_step`` -> a fixed sequence, ``execute_tool`` -> a fixed lookup,
  experts/skills/memory stubbed). It records which tools fired (in order), which
  evidence was gathered, whether a final answer was allowed or blocked, and the
  synthesized final text (via a stubbed ``compose_reply``). This is the path that
  exercises tool calls, contracts, memory recall, web evidence, scheduled tasks,
  code-agent delegation and prompt-injection resistance.

The runner reuses the proven stubbing approach rather than inventing a new
mocking layer; it is additive and never patches production logic, only the
network/OS boundaries the existing tests already patch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest import mock


# --------------------------------------------------------------------------- #
# Declarative scenario + result types
# --------------------------------------------------------------------------- #


@dataclass
class ToolStub:
    """A stubbed OS/desktop tool result.

    ``match`` is an optional substring matched against the serialized args (e.g.
    ``"ollama list"`` or ``"Test-Path"``) so one tool name can return different
    outputs depending on the call. ``output`` is the raw tool result string the
    coordinator would have received from ``agent_loop.execute_tool``.
    """

    tool: str
    output: str
    match: str | None = None


@dataclass
class Scenario:
    """One replayable turn. See module docstring for the two execution paths."""

    name: str
    description: str = ""
    path: str = "coordinator"  # "coordinator" | "routing"

    # --- input message + session state ---
    message: str = ""
    conversation: list[dict] = field(default_factory=list)
    route_mode: str = "auto"
    project: str | None = None
    cwd: str | None = None
    agent: str | None = "claude"
    model_mode: str = "auto"

    # --- routing-path inputs ---
    classified_route: str = "chat"

    # --- coordinator-path stubbed model responses ---
    decisions: list[dict] = field(default_factory=list)
    tool_stubs: list[ToolStub] = field(default_factory=list)
    experts: dict[str, dict] = field(default_factory=dict)
    consult_reply: str | None = None
    memories: str = ""
    compose_text: str | None = None  # stubbed final compose_reply output

    # --- coordinator-path knobs (mirror run_coordinator kwargs) ---
    task_contract_intent: str | None = None
    required_first_tool: dict | None = None
    require_file_output: bool = False
    capabilities: str | None = None
    max_tool_calls: int | None = None
    coordinator_model: str = "gemma4:12b"

    # --- expected assertions ---
    expect_engine: str | None = None
    expect_route: str | None = None
    expect_offload: bool | None = None
    expect_status: str | None = None
    expect_tools_called: list[str] | None = None  # as a set (subset must appear)
    expect_tools_in_order: list[str] | None = None  # exact ordered tool sequence
    expect_tools_not_called: list[str] | None = None
    expect_evidence_tools: list[str] | None = None  # tools with recorded evidence
    expect_contract_satisfied: bool | None = None
    expect_final_answer_allowed: bool | None = None
    final_must_contain: list[str] = field(default_factory=list)
    final_must_not_contain: list[str] = field(default_factory=list)


@dataclass
class ScenarioResult:
    """Structured outcome of running a scenario — the surface tests assert on."""

    name: str
    path: str
    # routing path
    route: str | None = None
    execution_engine: str | None = None
    is_offload: bool | None = None
    routing_reason: str = ""
    # coordinator path
    status: str | None = None
    tools_called: list[str] = field(default_factory=list)
    evidence_tools: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    action_log: str = ""
    contract_satisfied: bool | None = None
    final_answer_allowed: bool | None = None
    final_text: str = ""
    runtime_state: Any = None


# --------------------------------------------------------------------------- #
# Stub helpers (deterministic, no network)
# --------------------------------------------------------------------------- #


def _av(value):
    async def _coro(*args, **kwargs):
        return value

    return _coro


def _decision_sequence(decisions: list[dict]) -> Callable:
    seq = list(decisions)

    async def _coro(*args, **kwargs):
        return seq.pop(0) if seq else {"action": "answer", "thinking": "fallback"}

    return _coro


def _tool_executor(stubs: list[ToolStub], called: list[str]) -> Callable:
    async def _execute(tool, args, emit):
        called.append(tool)
        serialized = str(args)
        for stub in stubs:
            if stub.tool != tool:
                continue
            if stub.match is not None and stub.match not in serialized:
                continue
            return stub.output
        # Sensible deterministic defaults so a scenario need only stub what it
        # cares about; these mirror the shapes the real tools return.
        if tool == "read_file":
            return f"File: {args.get('path', '')}\nContent:\n..."
        if tool == "list_dir":
            return "Directory: .\n<DIR> backend"
        if tool == "run_command":
            return f"Command: {args.get('cmd', '')}\nOutput:\n"
        return f"{tool} ran"

    return _execute


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Execute a scenario with everything stubbed; return a ScenarioResult."""
    if scenario.path == "routing":
        return _run_routing(scenario)
    if scenario.path == "coordinator":
        return asyncio.run(_run_coordinator(scenario))
    raise ValueError(f"unknown scenario path: {scenario.path!r}")


def _run_routing(scenario: Scenario) -> ScenarioResult:
    from agents.routing import build_routing_decision

    decision = build_routing_decision(
        route_mode=scenario.route_mode,
        classified_route=scenario.classified_route,
        agent=scenario.agent,
        text=scenario.message,
        project=scenario.project,
        cwd=scenario.cwd,
    )
    return ScenarioResult(
        name=scenario.name,
        path="routing",
        route=decision.route,
        execution_engine=decision.execution_engine,
        is_offload=decision.is_offload(),
        routing_reason=decision.reason,
    )


async def _run_coordinator(scenario: Scenario) -> ScenarioResult:
    from agents import coordinator

    events: list[dict] = []
    called: list[str] = []

    async def fake_consult(model, task, refined, conversation, emit, abort):
        return scenario.consult_reply or "(stub expert answer)"

    async def fake_save_memory(text, *args, **kwargs):
        # Deterministic, no embedding/Ollama call — the remember path records the
        # write off this id so the memory_write contract can verify it.
        return "eval-mem-id"

    patches = [
        mock.patch.object(coordinator, "available_expert_models", new=_av(scenario.experts)),
        mock.patch.object(coordinator, "search_skills", new=_av([])),
        mock.patch.object(coordinator, "refine_query", new=_av(scenario.message)),
        mock.patch.object(coordinator, "_consult_expert", new=fake_consult),
        mock.patch.object(coordinator, "save_memory", new=fake_save_memory),
        mock.patch.object(
            coordinator.agent_loop, "execute_tool",
            new=_tool_executor(scenario.tool_stubs, called),
        ),
        mock.patch.object(coordinator, "_decide_step", new=_decision_sequence(scenario.decisions)),
    ]
    for p in patches:
        p.start()
    try:
        outcome = await coordinator.run_coordinator(
            scenario.message,
            events.append,
            asyncio.Event(),
            conversation=scenario.conversation or None,
            project_cwd=scenario.cwd,
            coordinator_model=scenario.coordinator_model,
            memories=scenario.memories,
            session_id="eval-session",
            required_first_tool=scenario.required_first_tool,
            require_file_output=scenario.require_file_output,
            task_contract_intent=scenario.task_contract_intent,
            capabilities=scenario.capabilities,
            max_tool_calls=scenario.max_tool_calls,
        )
    finally:
        for p in reversed(patches):
            p.stop()

    runtime_state = getattr(outcome, "runtime_state", None)
    evidence_tools = (
        [item.get("tool") for item in runtime_state.evidence_items]
        if runtime_state is not None
        else []
    )
    requirements = runtime_state.requirements if runtime_state is not None else {}
    contract_satisfied = requirements.get("satisfied") if requirements else None

    # A final answer was ALLOWED iff the coordinator reached a "done" status (it
    # only returns done after contract verification + file-output gates pass).
    final_allowed = outcome.status == "done"

    final_text = ""
    if final_allowed and scenario.compose_text is not None:
        final_text = await _compose(scenario, outcome)

    return ScenarioResult(
        name=scenario.name,
        path="coordinator",
        status=outcome.status,
        tools_called=called,
        evidence_tools=evidence_tools,
        events=events,
        action_log=outcome.action_log or "",
        contract_satisfied=contract_satisfied,
        final_answer_allowed=final_allowed,
        final_text=final_text,
        runtime_state=runtime_state,
    )


async def _compose(scenario: Scenario, outcome) -> str:
    """Drive the real compose_reply with the model stream stubbed.

    This exercises ``orchestrator.compose_reply``'s grounding + sanitization
    (untrusted-evidence wrapping, raw-log replacement) on the runtime evidence,
    with the model's token stream replaced by the scenario's ``compose_text`` so
    no Ollama call happens. Returns the final user-facing text.
    """
    from agents import orchestrator

    async def fake_stream(messages, model=None):
        yield scenario.compose_text or ""

    conversation = [
        *(scenario.conversation or []),
        {"role": "user", "content": scenario.message},
    ]
    parts: list[str] = []
    with mock.patch.object(orchestrator, "_stream_ollama_chat", new=fake_stream):
        async for chunk in orchestrator.compose_reply(
            conversation, outcome, scenario.coordinator_model, scenario.memories
        ):
            parts.append(chunk)
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Assertion helper used by the pytest layer
# --------------------------------------------------------------------------- #


def assert_scenario(scenario: Scenario, result: ScenarioResult) -> list[str]:
    """Check ``result`` against the scenario's expectations.

    Returns a list of human-readable failure messages (empty == passed) so the
    test layer can surface every mismatch for one scenario at once.
    """
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(f"[{scenario.name}] {message}")

    if scenario.expect_route is not None:
        check(result.route == scenario.expect_route,
              f"route={result.route!r} expected {scenario.expect_route!r}")
    if scenario.expect_engine is not None:
        check(result.execution_engine == scenario.expect_engine,
              f"engine={result.execution_engine!r} expected {scenario.expect_engine!r}")
    if scenario.expect_offload is not None:
        check(result.is_offload == scenario.expect_offload,
              f"is_offload={result.is_offload!r} expected {scenario.expect_offload!r}")
    if scenario.expect_status is not None:
        check(result.status == scenario.expect_status,
              f"status={result.status!r} expected {scenario.expect_status!r}")
    if scenario.expect_tools_called is not None:
        for tool in scenario.expect_tools_called:
            check(tool in result.tools_called,
                  f"tool {tool!r} not called (called={result.tools_called})")
    if scenario.expect_tools_in_order is not None:
        check(result.tools_called == scenario.expect_tools_in_order,
              f"tool order={result.tools_called} expected {scenario.expect_tools_in_order}")
    if scenario.expect_tools_not_called is not None:
        for tool in scenario.expect_tools_not_called:
            check(tool not in result.tools_called,
                  f"tool {tool!r} should NOT have been called (called={result.tools_called})")
    if scenario.expect_evidence_tools is not None:
        for tool in scenario.expect_evidence_tools:
            check(tool in result.evidence_tools,
                  f"evidence for {tool!r} missing (evidence={result.evidence_tools})")
    if scenario.expect_contract_satisfied is not None:
        check(result.contract_satisfied == scenario.expect_contract_satisfied,
              f"contract_satisfied={result.contract_satisfied!r} "
              f"expected {scenario.expect_contract_satisfied!r}")
    if scenario.expect_final_answer_allowed is not None:
        check(result.final_answer_allowed == scenario.expect_final_answer_allowed,
              f"final_answer_allowed={result.final_answer_allowed!r} "
              f"expected {scenario.expect_final_answer_allowed!r}")
    for needle in scenario.final_must_contain:
        check(needle in result.final_text,
              f"final answer missing {needle!r} (final={result.final_text!r})")
    for needle in scenario.final_must_not_contain:
        check(needle not in result.final_text,
              f"final answer must not contain {needle!r} (final={result.final_text!r})")

    return failures

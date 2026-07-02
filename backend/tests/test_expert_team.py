"""Tests for team behaviour: evidence-grounded consults + gated command proposals.

B4: a consulted expert receives the (bounded, untrusted-wrapped) evidence the
coordinator gathered this turn, instead of answering blind.
B5: an expert may end its answer with one PROPOSED_COMMAND line; the coordinator
vets it through the same gates as its own tool calls (job profile, contract
allowlist, risk/confirmation, repeat guard, budget) and runs it only if clean.

All model/tool calls are stubbed — no Ollama, no network.
"""

import asyncio
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents import coordinator  # noqa: E402
from agents.task_contracts import build_task_contract  # noqa: E402


# --------------------------------------------------------------------------- #
# _extract_proposed_command
# --------------------------------------------------------------------------- #


def test_extract_none_when_no_marker():
    answer, cmd = coordinator._extract_proposed_command("Just an answer.")
    assert answer == "Just an answer." and cmd is None


def test_extract_takes_command_and_strips_marker_line():
    answer, cmd = coordinator._extract_proposed_command(
        "The count comes from the files.\nPROPOSED_COMMAND: (Get-ChildItem *.py).Count"
    )
    assert cmd == "(Get-ChildItem *.py).Count"
    assert "PROPOSED_COMMAND" not in answer
    assert "count comes from" in answer


def test_extract_last_marker_wins_and_backticks_stripped():
    answer, cmd = coordinator._extract_proposed_command(
        "PROPOSED_COMMAND: `first`\nSome text\nproposed_command: `second`"
    )
    assert cmd == "second"
    assert "PROPOSED_COMMAND" not in answer.upper()


def test_extract_empty_marker_is_ignored():
    answer, cmd = coordinator._extract_proposed_command("Text\nPROPOSED_COMMAND:")
    assert cmd is None


# --------------------------------------------------------------------------- #
# _proposal_block_reason — every gate
# --------------------------------------------------------------------------- #


def _reason(cmd, **kw):
    defaults = dict(
        contract=None, capabilities=None, project_cwd=None,
        command_counts={}, max_tool_calls=None, tool_calls=0,
    )
    defaults.update(kw)
    return coordinator._proposal_block_reason(cmd, **defaults)


def test_readonly_proposal_allowed():
    assert _reason("(Get-ChildItem *.py).Count") is None


def test_risky_proposal_blocked_by_confirmation_gate():
    reason = _reason("Remove-Item -Recurse .\\data")
    assert reason and "confirmation" in reason


def test_capability_profile_blocks_shell():
    reason = _reason("(Get-ChildItem).Count", capabilities="read-only")
    assert reason and "job profile" in reason


def test_contract_allowlist_blocks_off_contract_tool():
    research = build_task_contract("research")  # run_command not allowed
    reason = _reason("(Get-ChildItem).Count", contract=research)
    assert reason and "contract allowlist" in reason


def test_repeat_guard_blocks_third_identical_run(tmp_path):
    from agents import loop as agent_loop
    key = agent_loop.normalize_command_key(
        agent_loop.apply_project_cwd_to_args(
            "run_command", {"cmd": "(Get-ChildItem).Count"}, str(tmp_path)
        )
    )
    reason = _reason(
        "(Get-ChildItem).Count", project_cwd=str(tmp_path),
        command_counts={key: 2},
    )
    assert reason and "already ran twice" in reason


def test_tool_budget_blocks_when_exhausted():
    reason = _reason("(Get-ChildItem).Count", max_tool_calls=1, tool_calls=1)
    assert reason and "limit" in reason


# --------------------------------------------------------------------------- #
# integration through run_coordinator (decision stream + consult stubbed)
# --------------------------------------------------------------------------- #


def _av(value):
    async def _coro(*args, **kwargs):
        return value
    return _coro


def _seq(decisions):
    seq = list(decisions)

    async def _coro(*args, **kwargs):
        return seq.pop(0) if seq else {"action": "answer", "thinking": "fallback"}
    return _coro


def _run(consult_reply, decisions, tool_output="Command: x\nOutput:\n3"):
    """Run one coordinator turn with consult + tools stubbed; return outcome facts."""
    events: list[dict] = []
    called: list[tuple[str, dict]] = []
    seen_evidence: list[str] = []

    async def fake_consult(model, task, refined, conversation, emit, abort, evidence=""):
        seen_evidence.append(evidence)
        return consult_reply

    async def fake_execute(tool, args, emit):
        called.append((tool, args))
        return tool_output

    experts = {"qwen2.5-coder:14b": {"label": "Coder", "hint": "code", "tools": True}}
    with mock.patch.object(coordinator, "available_expert_models", new=_av(experts)), \
         mock.patch.object(coordinator, "search_skills", new=_av([])), \
         mock.patch.object(coordinator, "refine_query", new=_av("refined")), \
         mock.patch.object(coordinator, "_consult_expert", new=fake_consult), \
         mock.patch.object(coordinator.agent_loop, "execute_tool", new=fake_execute), \
         mock.patch.object(coordinator, "_decide_step", new=_seq(decisions)):
        outcome = asyncio.run(coordinator.run_coordinator(
            "Hur många py-filer?", events.append, asyncio.Event(),
            coordinator_model="gemma4:12b",
        ))
    return outcome, called, seen_evidence, events


def test_safe_proposal_is_executed_and_recorded():
    outcome, called, _, events = _run(
        "Jag behöver räkna på riktigt.\nPROPOSED_COMMAND: (Get-ChildItem *.py).Count",
        decisions=[
            {"action": "consult", "model": "qwen2.5-coder:14b", "thinking": "ask coder"},
            {"action": "answer", "thinking": "done"},
        ],
    )
    assert ("run_command", {"cmd": "(Get-ChildItem *.py).Count"}) in [
        (t, {k: v for k, v in a.items() if k == "cmd"}) for t, a in called
    ]
    # Evidence recorded so contracts/grounding can use the proposal's result.
    assert outcome.runtime_state is not None
    assert any(
        item.get("tool") == "run_command" for item in outcome.runtime_state.evidence_items
    )
    assert outcome.status == "done"


def test_risky_proposal_is_not_executed_but_noted():
    outcome, called, _, _ = _run(
        "Rensa katalogen.\nPROPOSED_COMMAND: Remove-Item -Recurse .\\data",
        decisions=[
            {"action": "consult", "model": "qwen2.5-coder:14b", "thinking": "ask coder"},
            {"action": "answer", "thinking": "done"},
        ],
    )
    assert not any(t == "run_command" for t, _ in called), "risky proposal must not run"
    assert outcome.status == "done"  # the turn continues; proposal was advisory
    assert "not run" in (outcome.action_log or "")


def test_consult_receives_gathered_evidence():
    _, _, seen_evidence, _ = _run(
        "Svar utan förslag.",
        decisions=[
            {"action": "tool", "tool": "read_file", "args": {"path": "a.py"},
             "thinking": "look first"},
            {"action": "consult", "model": "qwen2.5-coder:14b", "thinking": "then ask"},
            {"action": "answer", "thinking": "done"},
        ],
        tool_output="File: a.py\nContent:\nprint('x')",
    )
    assert seen_evidence and seen_evidence[0], "expert must receive evidence"
    assert "a.py" in seen_evidence[0]


def test_consult_without_prior_tools_gets_empty_evidence():
    _, _, seen_evidence, _ = _run(
        "Svar.",
        decisions=[
            {"action": "consult", "model": "qwen2.5-coder:14b", "thinking": "ask"},
            {"action": "answer", "thinking": "done"},
        ],
    )
    assert seen_evidence and seen_evidence[0] == ""

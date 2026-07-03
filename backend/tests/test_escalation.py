"""Tests for verification-driven escalation (the "team" mechanism).

The escalation trigger is the OBJECTIVE test result, not the model's self-
assessment: author -> verify -> on a verified FAILURE, hand authoring to a coder
specialist -> re-verify. With escalation off, all attempts stay on the lead
model, so a with/without run isolates the specialist's real value. No Ollama —
the author call and tool execution are mocked; only the control flow is exercised.
"""

import asyncio
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents import coordinator  # noqa: E402
from agents.runtime_state import RuntimeState  # noqa: E402
from agents.task_contracts import build_task_contract  # noqa: E402
from agents.task_contracts import tests_passed_text as _tests_passed  # noqa: E402


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def test_tests_passed_parsing():
    assert _tests_passed("3 passed in 0.05s")
    assert _tests_passed("=== 12 passed ===")
    assert not _tests_passed("1 failed, 2 passed in 0.1s")
    assert not _tests_passed("2 passed, 1 error")
    assert not _tests_passed("no tests ran in 0.01s")
    assert not _tests_passed("")
    assert not _tests_passed("ImportError: no module named solution")


def test_coder_specialist_preference():
    assert coordinator._coder_specialist(
        {"qwen2.5-coder:14b": {"hint": "code"}, "devstral:latest": {"hint": "repo"}}
    ) == "qwen2.5-coder:14b"
    assert coordinator._coder_specialist({"devstral:latest": {"hint": "repo"}}) == "devstral:latest"
    # Falls back to any code-hinted expert.
    assert coordinator._coder_specialist({"x:1": {"hint": "snabb kod och teknik"}}) == "x:1"
    # None available.
    assert coordinator._coder_specialist({"gpt-oss:20b": {"hint": "reasoning"}}) is None
    assert coordinator._coder_specialist({}) is None


def test_extract_code_block():
    assert coordinator._extract_code_block("```python\ndef f():\n    return 1\n```") == "def f():\n    return 1"
    assert coordinator._extract_code_block("```\nimport os\n```") == "import os"
    # No fence but looks like code -> taken verbatim.
    assert "def g" in coordinator._extract_code_block("def g():\n    return 2")
    # Pure prose -> nothing usable.
    assert coordinator._extract_code_block("I will now write the function.") == ""
    assert coordinator._extract_code_block("") == ""


# --------------------------------------------------------------------------- #
# playbook control flow
# --------------------------------------------------------------------------- #


def _run_playbook(author_returns, verify_outputs, escalation_enabled, experts):
    events: list[dict] = []
    rs = RuntimeState()
    notes: list[str] = []
    contract = build_task_contract("code_task")
    authors_used: list[str] = []

    author_iter = iter(author_returns)

    async def fake_author(model, spec, prior, failing, emit, abort):
        authors_used.append(model)
        return next(author_iter, "")

    verify_iter = iter(verify_outputs)

    async def fake_execute(tool, args, emit):
        if tool == "write_file":
            return "File written: solution.py\nBytes: 5\nVerified: yes"
        if tool == "run_command":
            return f"Command: pytest\nShell: PowerShell\nOutput:\n{next(verify_iter, '1 failed')}"
        return f"{tool} ran"

    spec = {"solution_path": "solution.py", "verify_command": "pytest", "spec": "solve X"}
    with mock.patch.object(coordinator, "_author_code", new=fake_author), \
         mock.patch.object(coordinator.agent_loop, "execute_tool", new=fake_execute):
        outcome = asyncio.run(coordinator._run_code_task_playbook(
            spec, "solve X", "gemma4:12b", experts, "/proj",
            events.append, rs, notes, contract, escalation_enabled, asyncio.Event(),
        ))
    escalated = any(e.get("type") == "escalation" for e in events)
    return outcome, authors_used, escalated, rs


def test_lead_passes_first_attempt_no_escalation():
    outcome, authors, escalated, rs = _run_playbook(
        author_returns=["def sol(): return 1"],
        verify_outputs=["3 passed in 0.1s"],
        escalation_enabled=True,
        experts={"qwen2.5-coder:14b": {"hint": "code"}},
    )
    assert outcome.status == "done"
    assert authors == ["gemma4:12b"]  # never escalated
    assert not escalated
    assert any(a.get("verified") for a in rs.artifacts)


def test_escalation_recovers_after_verified_failure():
    outcome, authors, escalated, rs = _run_playbook(
        author_returns=["bad code", "good code from specialist"],
        verify_outputs=["1 failed in 0.1s", "3 passed in 0.1s"],
        escalation_enabled=True,
        experts={"qwen2.5-coder:14b": {"hint": "code"}},
    )
    assert outcome.status == "done"
    assert authors == ["gemma4:12b", "qwen2.5-coder:14b"]  # lead, then specialist
    assert escalated
    # The contract is satisfied by the passing test run.
    result = build_task_contract("code_task").evaluate(rs.evidence_items)
    assert result.satisfied


def test_escalation_off_stays_on_lead_and_can_fail():
    outcome, authors, escalated, rs = _run_playbook(
        author_returns=["bad1", "bad2", "bad3"],
        verify_outputs=["1 failed", "1 failed", "1 failed"],
        escalation_enabled=False,
        experts={"qwen2.5-coder:14b": {"hint": "code"}},
    )
    assert outcome.status == "max_steps"
    assert authors == ["gemma4:12b", "gemma4:12b", "gemma4:12b"]  # never a specialist
    assert not escalated


def test_no_coder_available_falls_back_to_self_retry():
    outcome, authors, escalated, rs = _run_playbook(
        author_returns=["bad1", "good2"],
        verify_outputs=["1 failed", "3 passed"],
        escalation_enabled=True,
        experts={"gpt-oss:20b": {"hint": "reasoning"}},  # no coder
    )
    assert outcome.status == "done"
    assert authors == ["gemma4:12b", "gemma4:12b"]  # retried itself
    assert not escalated  # no escalation event without a coder


def test_empty_code_skips_attempt_without_crashing():
    outcome, authors, escalated, rs = _run_playbook(
        author_returns=["", "def sol(): return 1"],
        verify_outputs=["3 passed"],  # only one verify (first attempt produced no code)
        escalation_enabled=False,
        experts={},
    )
    assert outcome.status == "done"
    assert authors[:2] == ["gemma4:12b", "gemma4:12b"]

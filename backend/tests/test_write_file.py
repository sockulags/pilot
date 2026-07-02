"""Tests for the first-class write_file tool.

The 2026-07-02 eval exposed a design contradiction: file-output turns REQUIRED a
file-writing command while the risk classifier confirmation-gated EVERY shell
write (Set-Content/Out-File/>) — so research-to-file was impossible to complete
autonomously. write_file resolves it: creating a NEW relative file is safe and
free; overwrite / traversal / absolute targets stay confirmation-gated, and
read-only job profiles still cannot write at all.
"""

import asyncio
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents import coordinator  # noqa: E402
from agents.runtime_state import RuntimeState  # noqa: E402
from job_permissions import tool_allowed  # noqa: E402
from tools import registry, write_file  # noqa: E402


# --------------------------------------------------------------------------- #
# tool behaviour
# --------------------------------------------------------------------------- #


def test_writes_new_file_and_verifies(tmp_path):
    result = write_file("report.md", "# Rapport\nhej", cwd=str(tmp_path))
    target = tmp_path / "report.md"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "# Rapport\nhej"
    assert result["verified"] is True
    assert result["path"] == str(target.resolve())


def test_creates_missing_parent_dirs(tmp_path):
    write_file("out/sub/report.md", "x", cwd=str(tmp_path))
    assert (tmp_path / "out" / "sub" / "report.md").is_file()


def test_refuses_overwrite_without_flag(tmp_path):
    (tmp_path / "report.md").write_text("original", encoding="utf-8")
    try:
        write_file("report.md", "new", cwd=str(tmp_path))
        raised = False
    except FileExistsError as exc:
        raised = True
        assert "overwrite=true" in str(exc)
    assert raised
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "original"


def test_overwrites_with_flag(tmp_path):
    (tmp_path / "report.md").write_text("original", encoding="utf-8")
    write_file("report.md", "new", overwrite=True, cwd=str(tmp_path))
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "new"


# --------------------------------------------------------------------------- #
# confirmation gating (registry)
# --------------------------------------------------------------------------- #


def test_new_relative_file_needs_no_confirmation(tmp_path):
    cwd = str(tmp_path)  # the loop always supplies a trusted cwd
    assert registry.confirmation_required("write_file", {"path": "report.md", "content": "x", "cwd": cwd}) is False
    assert registry.confirmation_required("write_file", {"path": "out/report.md", "content": "x", "cwd": cwd}) is False


def test_overwrite_of_existing_file_requires_confirmation(tmp_path):
    (tmp_path / "report.md").write_text("original", encoding="utf-8")
    assert registry.confirmation_required(
        "write_file",
        {"path": "report.md", "content": "x", "overwrite": True, "cwd": str(tmp_path)},
    ) is True


def test_preemptive_overwrite_flag_on_new_file_is_not_gated(tmp_path):
    # Small models set overwrite=true on brand-new files; a no-op flag must not
    # block them (observed live: gemma4).
    assert registry.confirmation_required(
        "write_file",
        {"path": "report.md", "content": "x", "overwrite": True, "cwd": str(tmp_path)},
    ) is False


def test_absolute_path_inside_project_cwd_is_not_gated(tmp_path):
    target = str(tmp_path / "summary.md")
    assert registry.confirmation_required(
        "write_file", {"path": target, "content": "x", "cwd": str(tmp_path)}
    ) is False
    outside = str(tmp_path.parent / "elsewhere.md")
    assert registry.confirmation_required(
        "write_file", {"path": outside, "content": "x", "cwd": str(tmp_path)}
    ) is True


def test_relative_path_that_normalizes_inside_is_not_gated(tmp_path):
    # sub/../summary.md resolves inside the project -> safe (previously over-gated).
    assert registry.confirmation_required(
        "write_file", {"path": "sub/../summary.md", "content": "x", "cwd": str(tmp_path)}
    ) is False


def test_model_supplied_cwd_cannot_escape_the_project(tmp_path):
    # SECURITY (adversarial review 2026-07-03): a model that sets its own cwd must
    # not escape. apply_project_cwd_to_args FORCES cwd to the trusted project root,
    # overriding the model value, so the gate then judges the real target.
    from agents import loop as agent_loop

    project = str(tmp_path / "project")
    os.makedirs(project)
    evil = agent_loop.apply_project_cwd_to_args(
        "write_file",
        {"path": "pwned.txt", "content": "x", "cwd": "C:\\Windows\\Temp"},
        project,
    )
    assert evil["cwd"] == project  # model cwd overridden with the trusted base
    # Defense-in-depth: the gate itself refuses a target that resolves outside cwd.
    assert registry.confirmation_required(
        "write_file",
        {"path": str(tmp_path.parent / "escape.txt"), "content": "x", "cwd": project},
    ) is True


def test_no_trusted_cwd_requires_confirmation():
    assert registry.confirmation_required(
        "write_file", {"path": "x.md", "content": "y"}
    ) is True  # no cwd -> no trusted base -> confirm


def test_traversal_and_absolute_paths_require_confirmation():
    assert registry.confirmation_required("write_file", {"path": "../outside.md", "content": "x"}) is True
    assert registry.confirmation_required("write_file", {"path": "..\\outside.md", "content": "x"}) is True
    assert registry.confirmation_required("write_file", {"path": "C:\\Windows\\x.md", "content": "x"}) is True
    assert registry.confirmation_required("write_file", {"path": "/etc/x", "content": "x"}) is True
    assert registry.confirmation_required("write_file", {"path": "", "content": "x"}) is True


# --------------------------------------------------------------------------- #
# job profiles: read-only may never write
# --------------------------------------------------------------------------- #


def test_write_file_denied_under_read_profiles():
    assert tool_allowed("write_file", "read-only") is False
    assert tool_allowed("write_file", "web-only") is False
    assert tool_allowed("write_file", "project-write") is True
    assert tool_allowed("write_file", "shell") is True


def test_existing_read_tools_unaffected_by_capability_reorder():
    assert tool_allowed("read_file", "read-only") is True
    assert tool_allowed("web_research", "web-only") is True
    assert tool_allowed("run_command", "read-only") is False


# --------------------------------------------------------------------------- #
# loop integration + evidence
# --------------------------------------------------------------------------- #


def test_execute_tool_writes_and_reports(tmp_path):
    from agents import loop as agent_loop

    async def go():
        args = agent_loop.apply_project_cwd_to_args(
            "write_file", {"path": "report.md", "content": "innehåll"}, str(tmp_path)
        )
        return await agent_loop.execute_tool("write_file", args, lambda e: None)

    result = asyncio.run(go())
    assert result.startswith("File written: ")
    assert "Verified: yes" in result
    assert (tmp_path / "report.md").is_file()
    assert agent_loop.tool_execution_succeeded("write_file", result) is True


def test_execute_tool_refusal_is_a_failure(tmp_path):
    from agents import loop as agent_loop

    (tmp_path / "report.md").write_text("original", encoding="utf-8")

    async def go():
        return await agent_loop.execute_tool(
            "write_file", {"path": "report.md", "content": "x", "cwd": str(tmp_path)},
            lambda e: None,
        )

    result = asyncio.run(go())
    assert result.startswith("write_file refused:")
    assert agent_loop.tool_execution_succeeded("write_file", result) is False


def test_runtime_state_records_verified_artifact(tmp_path):
    rs = RuntimeState()
    text = f"File written: {tmp_path / 'report.md'}\nBytes: 10\nVerified: yes"
    rs.record_tool_result(
        "write_file", {"path": "report.md", "content": "x"}, text,
        ok=True, artifact_verified=True,
    )
    assert rs.artifacts and rs.artifacts[0]["verified"] is True
    assert str(tmp_path / "report.md") in rs.artifacts[0]["path"]


def test_create_file_contract_satisfied_by_write_file(tmp_path):
    from agents.task_contracts import build_task_contract

    contract = build_task_contract("create_file")
    assert "write_file" in contract.allowed_tools
    rs = RuntimeState()
    rs.record_tool_result(
        "write_file", {"path": "report.md", "content": "x"},
        "File written: report.md\nBytes: 1\nVerified: yes",
        ok=True, artifact_verified=True,
    )
    result = contract.evaluate(rs.evidence_items)
    assert result.satisfied


# --------------------------------------------------------------------------- #
# coordinator: a file-output turn completes via write_file alone
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


def test_rewrite_after_success_is_skipped_not_gated(tmp_path):
    # Once a file is written+verified this turn, further write_file attempts are
    # skipped with a "you are done" note instead of stalling on the overwrite
    # confirmation gate (observed live: gpt-4o-mini rewrote until gated).
    events: list[dict] = []

    with mock.patch.object(coordinator, "available_expert_models", new=_av({})), \
         mock.patch.object(coordinator, "search_skills", new=_av([])), \
         mock.patch.object(coordinator, "_decide_step", new=_seq([
             {"action": "tool", "tool": "write_file",
              "args": {"path": "summary.md", "content": "första"}, "thinking": "write"},
             {"action": "tool", "tool": "write_file",
              "args": {"path": "summary.md", "content": "andra", "overwrite": True},
              "thinking": "rewrite"},
             {"action": "answer", "thinking": "done"},
         ])):
        outcome = asyncio.run(coordinator.run_coordinator(
            "Skriv summary.md", events.append, asyncio.Event(),
            project_cwd=str(tmp_path), coordinator_model="gemma4:12b",
            require_file_output=True, task_contract_intent="create_file",
        ))

    assert outcome.status == "done"  # not needs_input — the rewrite was skipped
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == "första"
    assert not any(e.get("type") == "confirmation_required" for e in events)
    assert "skipped write_file" in (outcome.action_log or "")


def test_require_file_output_satisfied_by_write_file(tmp_path):
    events: list[dict] = []

    with mock.patch.object(coordinator, "available_expert_models", new=_av({})), \
         mock.patch.object(coordinator, "search_skills", new=_av([])), \
         mock.patch.object(coordinator, "_decide_step", new=_seq([
             {"action": "tool", "tool": "write_file",
              "args": {"path": "report.md", "content": "# Rapport\ninnehåll"},
              "thinking": "write it"},
             {"action": "answer", "thinking": "done"},
         ])):
        outcome = asyncio.run(coordinator.run_coordinator(
            "Skriv en rapport till report.md", events.append, asyncio.Event(),
            project_cwd=str(tmp_path),
            coordinator_model="gemma4:12b",
            require_file_output=True,
            task_contract_intent="create_file",
        ))

    assert outcome.status == "done"
    assert (tmp_path / "report.md").is_file()
    rs = outcome.runtime_state
    assert any(a.get("verified") for a in rs.artifacts)
    # No confirmation event fired for a new relative file.
    assert not any(e.get("type") == "confirmation_required" for e in events)

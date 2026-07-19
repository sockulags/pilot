"""Regression tests for the run_codex branch in agents.loop.

run_codex yields TYPED dict events ({"type": "session"|"text"|"tool"|"result"|
"error", ...}), not strings. The loop branch must dispatch on each event's type:
accumulate streamed text, forward tool calls as their own action events, and
surface errors distinctly — never string-join the raw dicts (which raised
TypeError only AFTER the subprocess had run).

The run_codex generator is fully stubbed via monkeypatch, mirroring
test_tool_ergonomics.test_loop_appends_hint_only_on_failed_command — no real
Claude Code CLI is invoked.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_run_codex_dispatch_accumulates_text_and_forwards_tool_events(monkeypatch):
    from agents import loop as agent_loop

    async def fake_run_codex(prompt, *args, **kwargs):
        yield {"type": "session", "id": "sess-123"}
        yield {"type": "text", "text": "Reading the file. "}
        yield {"type": "tool", "name": "Edit", "input": {"path": "main.py"}}
        yield {"type": "text", "text": "Done."}
        yield {"type": "result", "text": "final result payload"}

    monkeypatch.setattr(agent_loop, "run_codex", fake_run_codex)

    events: list[dict] = []

    async def go():
        return await agent_loop.execute_tool(
            "run_codex", {"prompt": "do the thing"}, events.append
        )

    result = asyncio.run(go())

    # The branch returns the accumulated STREAMED text — not the result payload,
    # not an error string, not a Python exception message.
    assert result == "Reading the file. Done."
    assert "Error" not in result
    assert "TypeError" not in result

    # The tool event was emitted as its own action event, distinct from the
    # accumulated text (never appended into it).
    action_events = [e for e in events if e.get("type") == "action"]
    assert len(action_events) == 1
    assert action_events[0]["tool"] == "Edit"
    assert action_events[0]["args"] == {"path": "main.py"}
    assert "Edit" not in result

    # No error surfaced on the happy path.
    assert not [e for e in events if e.get("type") == "error"]


def test_run_codex_dispatch_prefers_result_when_no_text_streamed(monkeypatch):
    from agents import loop as agent_loop

    async def fake_run_codex(prompt, *args, **kwargs):
        yield {"type": "session", "id": "sess-abc"}
        yield {"type": "result", "text": "only the result payload"}

    monkeypatch.setattr(agent_loop, "run_codex", fake_run_codex)

    async def go():
        return await agent_loop.execute_tool(
            "run_codex", {"prompt": "x"}, lambda e: None
        )

    result = asyncio.run(go())
    assert result == "only the result payload"


def test_run_codex_dispatch_surfaces_error_distinctly(monkeypatch):
    from agents import loop as agent_loop

    async def fake_run_codex(prompt, *args, **kwargs):
        yield {"type": "session", "id": "sess-err"}
        yield {"type": "error", "text": "usage limit reached"}

    monkeypatch.setattr(agent_loop, "run_codex", fake_run_codex)

    events: list[dict] = []

    async def go():
        return await agent_loop.execute_tool(
            "run_codex", {"prompt": "x"}, events.append
        )

    result = asyncio.run(go())

    # The failure is visible in the return value (not swallowed as if the run
    # succeeded), and an error event was emitted for the UI.
    assert result.startswith("Error executing run_codex:")
    assert "usage limit reached" in result

    error_events = [e for e in events if e.get("type") == "error"]
    assert len(error_events) == 1
    assert error_events[0]["content"] == "usage limit reached"

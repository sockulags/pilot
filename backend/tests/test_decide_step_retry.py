"""Unit tests for the coordinator decision step's prose-fallback retry.

A chatty tools-capable model (observed: gemma4:12b) sometimes narrates a plan
("Here's what I'll do: 1. ...") instead of emitting a tool call. Mid-contract
that is a dead end: the empty answer is blocked and the loop spins to max_steps.
``_decide_step`` re-asks ONCE with a strict JSON-only instruction and adopts the
retry only when it yields an actionable decision. These tests pin that behaviour
with the HTTP layer mocked — no Ollama, no network.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents import coordinator  # noqa: E402


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    """Async-context httpx stand-in returning queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self.calls.append(json)
        return self._responses.pop(0)


def _msg(content=None, tool_calls=None):
    message = {}
    if content is not None:
        message["content"] = content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return _FakeResp({"message": message})


def _patch_client(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(coordinator.httpx, "AsyncClient", lambda *a, **k: client)
    return client


PROSE = "Here's what I plan to do:\n1. I will read the configuration file first."


def test_prose_fallback_is_flagged():
    decision = coordinator._decision_from_message({"content": PROSE})
    assert decision["action"] == "answer"
    assert decision.get("_prose_fallback") is True


def test_native_tool_call_is_not_flagged():
    decision = coordinator._decision_from_message(
        {"tool_calls": [{"function": {"name": "run_command", "arguments": {"cmd": "echo hi"}}}]}
    )
    assert decision["action"] == "tool"
    assert decision["tool"] == "run_command"
    assert "_prose_fallback" not in decision


def test_unwrap_nested_tool_from_args():
    # The exact shape gemma4 emitted live under the forcing prompt.
    decision = coordinator._unwrap_nested_tool(
        {"action": "tool", "tool": "tool",
         "args": {"tool": "run_command", "args": {"cmd": "echo pilot-eval"}}}
    )
    assert decision["tool"] == "run_command"
    assert decision["args"] == {"cmd": "echo pilot-eval"}


def test_unwrap_nested_tool_name_and_title_variants():
    for key in ("name", "title"):
        decision = coordinator._unwrap_nested_tool(
            {"action": "tool", "tool": "tool",
             "args": {key: "run_command", "args": {"cmd": "x"}}}
        )
        assert decision["tool"] == "run_command", key
        assert decision["args"] == {"cmd": "x"}


def test_unwrap_nested_tool_sibling_args():
    # Real tool name nested, arguments are sibling keys (no inner "args" object).
    decision = coordinator._unwrap_nested_tool(
        {"action": "tool", "tool": "tool", "args": {"tool": "read_file", "path": "a.py"}}
    )
    assert decision["tool"] == "read_file"
    assert decision["args"] == {"path": "a.py"}


def test_unwrap_leaves_valid_tool_untouched():
    original = {"action": "tool", "tool": "run_command", "args": {"cmd": "echo hi"}}
    assert coordinator._unwrap_nested_tool(dict(original)) == original


def test_unwrap_ignores_non_tool_actions():
    answer = {"action": "answer", "thinking": "done"}
    assert coordinator._unwrap_nested_tool(dict(answer)) == answer


def test_decision_from_message_unwraps_nested_json_content():
    content = (
        '{"action": "tool", "tool": "tool", '
        '"args": {"tool": "run_command", "args": {"cmd": "echo pilot-eval"}}}'
    )
    decision = coordinator._decision_from_message({"content": content})
    assert decision["action"] == "tool"
    assert decision["tool"] == "run_command"
    assert decision["args"] == {"cmd": "echo pilot-eval"}


def test_retry_recovers_tool_call_when_forced(monkeypatch):
    client = _patch_client(monkeypatch, [
        _msg(content=PROSE),  # 1st: prose plan, no tool call
        _msg(content='{"action": "tool", "tool": "run_command", "args": {"cmd": "echo hi"}}'),
    ])
    decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True, force_tool_on_prose=True
    ))
    assert decision["action"] == "tool"
    assert decision["tool"] == "run_command"
    assert len(client.calls) == 2  # one corrective re-ask
    # The retry drops the tools payload and strengthens the system prompt.
    assert "tools" not in client.calls[1]
    assert coordinator._FORCE_DECISION_SUFFIX in client.calls[1]["messages"][0]["content"]


def test_no_retry_when_not_forced(monkeypatch):
    client = _patch_client(monkeypatch, [_msg(content=PROSE)])
    decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True, force_tool_on_prose=False
    ))
    assert decision["action"] == "answer"
    assert len(client.calls) == 1  # fast path: single call, no retry


def test_retry_that_still_proses_falls_back_to_answer(monkeypatch):
    client = _patch_client(monkeypatch, [
        _msg(content=PROSE),
        _msg(content="Still just talking, no JSON here."),
    ])
    decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True, force_tool_on_prose=True
    ))
    assert decision["action"] == "answer"  # never worse than before
    assert len(client.calls) == 2


def test_native_tool_call_skips_retry_even_when_forced(monkeypatch):
    client = _patch_client(monkeypatch, [
        _msg(tool_calls=[{"function": {"name": "list_dir", "arguments": {"path": "."}}}]),
    ])
    decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True, force_tool_on_prose=True
    ))
    assert decision["action"] == "tool"
    assert decision["tool"] == "list_dir"
    assert len(client.calls) == 1  # already actionable, no re-ask

"""Unit tests for the coordinator decision step's prose-fallback retry.

A chatty tools-capable model (observed: gemma4:12b) sometimes narrates a plan
("Here's what I'll do: 1. ...") instead of emitting a tool call. Mid-contract
that is a dead end: the empty answer is blocked and the loop spins to max_steps.
``_decide_step`` re-asks ONCE with a strict JSON-only instruction and adopts the
retry only when it yields an actionable decision. These tests pin that behaviour
with the model provider mocked — no Ollama, no network. The decision step is now
backend-agnostic (it calls ``providers.chat_once``), so the mock returns the same
normalized message dicts either backend would.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents import coordinator  # noqa: E402


class _ChatOnceStub:
    """Async stand-in for providers.chat_once returning queued message dicts."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # (messages, tools, fmt) per call

    async def __call__(
        self, messages, model=None, *, tools=None, temperature=0.1, backend=None,
        role=None, fmt=None, schema=None,
    ):
        self.calls.append((messages, tools, fmt))
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _prose(text):
    return {"content": text}


def _content_json(text):
    return {"content": text}


def _native_tool(name, args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def _patch(monkeypatch, responses):
    stub = _ChatOnceStub(responses)
    monkeypatch.setattr(coordinator.providers, "chat_once", stub)
    return stub


PROSE = "Here's what I plan to do:\n1. I will read the configuration file first."


# --- pure normalization / unwrap (no provider needed) ---------------------- #


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


# --- _decide_step retry behaviour (provider mocked) ------------------------ #


def test_retry_recovers_tool_call_when_forced(monkeypatch):
    stub = _patch(monkeypatch, [
        _prose(PROSE),  # 1st: prose plan, no tool call
        _content_json('{"action": "tool", "tool": "run_command", "args": {"cmd": "echo hi"}}'),
    ])
    decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True, force_tool_on_prose=True
    ))
    assert decision["action"] == "tool"
    assert decision["tool"] == "run_command"
    assert len(stub.calls) == 2  # one corrective re-ask
    # The retry drops the tools payload and strengthens the system prompt.
    retry_messages, retry_tools, retry_fmt = stub.calls[1]
    assert retry_tools is None
    # No tools payload on the retry, so it requests JSON-constrained output.
    assert retry_fmt == "json"
    assert coordinator._FORCE_DECISION_SUFFIX in retry_messages[0]["content"]


def test_no_retry_when_not_forced(monkeypatch):
    stub = _patch(monkeypatch, [_prose(PROSE)])
    decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True, force_tool_on_prose=False
    ))
    assert decision["action"] == "answer"
    assert len(stub.calls) == 1  # fast path: single call, no retry


def test_retry_that_still_proses_falls_back_to_answer(monkeypatch):
    stub = _patch(monkeypatch, [_prose(PROSE), _prose("Still just talking, no JSON here.")])
    decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True, force_tool_on_prose=True
    ))
    assert decision["action"] == "answer"  # never worse than before
    assert len(stub.calls) == 2


def test_forced_retry_failure_falls_back_to_prose_answer(monkeypatch):
    # First call succeeds with a prose plan; the forced re-ask transiently fails.
    # The turn must NOT error — it degrades to the answer already in hand.
    stub = _patch(monkeypatch, [_prose(PROSE), RuntimeError("HTTP 500")])
    decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True, force_tool_on_prose=True
    ))
    assert decision["action"] == "answer"
    assert len(stub.calls) == 2


def test_native_tool_call_skips_retry_even_when_forced(monkeypatch):
    stub = _patch(monkeypatch, [_native_tool("list_dir", {"path": "."})])
    decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True, force_tool_on_prose=True
    ))
    assert decision["action"] == "tool"
    assert decision["tool"] == "list_dir"
    assert len(stub.calls) == 1  # already actionable, no re-ask


def test_non_tools_path_parses_json_content(monkeypatch):
    stub = _patch(monkeypatch, [_content_json('{"action": "answer", "thinking": "done"}')])
    decision = asyncio.run(coordinator._decide_step(
        "deepseek-r1:14b", "sys", "ctx", experts={}, use_tools=False
    ))
    assert decision["action"] == "answer"
    assert len(stub.calls) == 1
    # The non-tools decision path requests JSON-constrained output.
    _messages, tools, fmt = stub.calls[0]
    assert tools is None
    assert fmt == "json"


def test_tools_path_does_not_request_json_format(monkeypatch):
    # A tool-calling model must NOT constrain to json_object — that would suppress
    # its native tool_calls (and OpenAI rejects response_format alongside tools).
    stub = _patch(monkeypatch, [_native_tool("list_dir", {"path": "."})])
    coordinator_decision = asyncio.run(coordinator._decide_step(
        "gemma4:12b", "sys", "ctx", experts={}, use_tools=True
    ))
    assert coordinator_decision["action"] == "tool"
    _messages, _tools, fmt = stub.calls[0]
    assert fmt is None

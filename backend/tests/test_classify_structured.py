"""classify_turn requests structured (JSON-constrained) output.

The route decision parses a JSON object out of the classifier's reply, so the
classifier call asks the provider for JSON ("format"/response_format). This pins
that the hint is actually requested — additive over the lenient
extract_json_object fallback, and Ollama-free (the provider is mocked).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents import orchestrator  # noqa: E402


class _ChatOnceStub:
    """Async stand-in for providers.chat_once recording the requested format."""

    def __init__(self, content):
        self._content = content
        self.calls = []  # kwargs per call

    async def __call__(self, messages, model=None, *, tools=None, temperature=0.1,
                       backend=None, role=None, fmt=None, schema=None):
        self.calls.append({"model": model, "role": role, "fmt": fmt, "schema": schema})
        return {"content": self._content}


def test_classify_turn_requests_json_format(monkeypatch):
    stub = _ChatOnceStub('{"route": "chat", "thinking": "greeting", "model": "gemma4:12b"}')
    monkeypatch.setattr(orchestrator.providers, "chat_once", stub)

    decision = asyncio.run(orchestrator.classify_turn([], "What is the capital of France?"))

    assert decision["route"] == "chat"
    # The classifier call ran once and requested JSON-constrained output.
    assert len(stub.calls) == 1
    assert stub.calls[0]["fmt"] == "json"
    assert stub.calls[0]["role"] == "classifier"


def test_classify_turn_still_parses_when_format_ignored(monkeypatch):
    # An endpoint that ignores the format hint returns prose-wrapped JSON; the
    # lenient extract_json_object fallback must still recover the route (no
    # regression, degrade gracefully).
    prose = 'Sure! Here is my decision:\n{"route": "chat", "thinking": "ok"}'
    stub = _ChatOnceStub(prose)
    monkeypatch.setattr(orchestrator.providers, "chat_once", stub)

    decision = asyncio.run(orchestrator.classify_turn([], "Tell me a joke"))

    assert decision["route"] == "chat"

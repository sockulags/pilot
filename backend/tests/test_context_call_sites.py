import asyncio
from unittest import mock

from agents import coordinator


def test_coordinator_decision_uses_coordinator_context_without_rerouting_model():
    seen = {}

    async def fake_once(messages, model, **kwargs):
        seen.update(model=model, kwargs=kwargs)
        return {"content": '{"action":"answer"}'}

    with mock.patch.object(coordinator.providers, "chat_once", new=fake_once):
        result = asyncio.run(coordinator._decide_step("chosen:model", "sys", "ctx"))
    assert result["action"] == "answer"
    assert seen["model"] == "chosen:model"
    assert seen["kwargs"]["context_role"] == "coordinator"
    assert "role" not in seen["kwargs"]


def test_specialist_and_code_author_use_distinct_context_roles():
    calls = []

    async def fake_stream(messages, model, **kwargs):
        calls.append((model, kwargs))
        yield "```python\ndef solved():\n    return True\n```"

    with mock.patch.object(coordinator.providers, "chat_stream", new=fake_stream):
        asyncio.run(coordinator._consult_expert(
            "specialist:model", "task", "refined", None, lambda _event: None,
            asyncio.Event(),
        ))
        asyncio.run(coordinator._author_code(
            "code:model", "task", "", "", lambda _event: None, asyncio.Event(),
        ))

    assert calls[0][0] == "specialist:model"
    assert calls[0][1]["context_role"] == "synthesis"
    assert calls[1][0] == "code:model"
    assert calls[1][1]["context_role"] == "code_agent"

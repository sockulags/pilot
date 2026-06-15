import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class CoordinatorTests(unittest.TestCase):
    """The in-turn coordinator: consult experts / perceive / tools / remember, then answer."""

    def test_simple_question_answers_immediately_without_gathering(self):
        asyncio.run(self._simple_question_answers_immediately())

    def test_consults_expert_and_grounds_outcome(self):
        asyncio.run(self._consults_expert_and_grounds_outcome())

    def test_skips_unavailable_expert(self):
        asyncio.run(self._skips_unavailable_expert())

    def test_remember_action_saves_memory(self):
        asyncio.run(self._remember_action_saves_memory())

    def _experts(self):
        return {
            "qwen2.5-coder:14b": {"label": "Coder", "hint": "code", "tools": True},
            "deepseek-r1:14b": {"label": "R1", "hint": "reasoning", "tools": False},
        }

    async def _simple_question_answers_immediately(self):
        from agents import coordinator

        events: list[dict] = []
        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "_decide_step", new=_seq([{"action": "answer", "thinking": "trivial"}])):
            outcome = await coordinator.run_coordinator(
                "Hej!", events.append, asyncio.Event(), coordinator_model="gemma4:latest"
            )

        self.assertEqual("done", outcome.status)
        self.assertEqual("", outcome.action_log)
        self.assertFalse(any(e["type"] == "consult" for e in events))

    async def _consults_expert_and_grounds_outcome(self):
        from agents import coordinator

        events: list[dict] = []
        consulted: list[str] = []

        async def fake_consult(model, task, conversation, emit, abort):
            consulted.append(model)
            return "def reverse(s): return s[::-1]"

        decisions = [
            {"action": "consult", "model": "qwen2.5-coder:14b", "thinking": "code"},
            {"action": "answer", "thinking": "have it"},
        ]
        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "_consult_expert", new=fake_consult), \
             mock.patch.object(coordinator, "_decide_step", new=_seq(decisions)):
            outcome = await coordinator.run_coordinator(
                "Vänd en sträng i Python", events.append, asyncio.Event(), coordinator_model="gemma4:latest"
            )

        self.assertEqual(["qwen2.5-coder:14b"], consulted)
        self.assertEqual("done", outcome.status)
        self.assertIn("s[::-1]", outcome.action_log)
        self.assertTrue(any(e["type"] == "consult" and e["model"] == "qwen2.5-coder:14b" for e in events))

    async def _skips_unavailable_expert(self):
        from agents import coordinator

        called: list[str] = []

        async def fake_consult(model, task, conversation, emit, abort):
            called.append(model)
            return "should not happen"

        decisions = [
            {"action": "consult", "model": "not-installed:70b", "thinking": "oops"},
            {"action": "answer", "thinking": "give up"},
        ]
        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "_consult_expert", new=fake_consult), \
             mock.patch.object(coordinator, "_decide_step", new=_seq(decisions)):
            outcome = await coordinator.run_coordinator(
                "Do a thing", lambda e: None, asyncio.Event(), coordinator_model="gemma4:latest"
            )

        self.assertEqual([], called)
        self.assertEqual("done", outcome.status)
        self.assertIn("unavailable", outcome.action_log)

    async def _remember_action_saves_memory(self):
        from agents import coordinator

        events: list[dict] = []
        saved: list[tuple] = []

        async def fake_save(text, kind="fact", session_id=None):
            saved.append((text, kind, session_id))
            return "mem-123"

        decisions = [
            {"action": "remember", "text": "Jag heter Lucas.", "thinking": "durable fact"},
            {"action": "answer", "thinking": "done"},
        ]
        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "save_memory", new=fake_save), \
             mock.patch.object(coordinator, "_decide_step", new=_seq(decisions)):
            outcome = await coordinator.run_coordinator(
                "Kom ihåg att jag heter Lucas", events.append, asyncio.Event(),
                coordinator_model="gemma4:latest", session_id="sess-1",
            )

        self.assertEqual([("Jag heter Lucas.", "fact", "sess-1")], saved)
        self.assertEqual("done", outcome.status)
        self.assertTrue(any(e["type"] == "memory" for e in events))


def _av(value):
    async def _coro(*args, **kwargs):
        return value
    return _coro


def _seq(decisions):
    seq = list(decisions)

    async def _coro(*args, **kwargs):
        return seq.pop(0) if seq else {"action": "answer", "thinking": "fallback"}

    return _coro


if __name__ == "__main__":
    unittest.main()

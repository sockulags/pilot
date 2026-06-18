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

    def test_clarify_action_returns_needs_input_with_question(self):
        asyncio.run(self._clarify_action_returns_needs_input())

    def test_required_first_tool_runs_before_answer(self):
        asyncio.run(self._required_first_tool_runs_before_answer())

    def test_file_output_requirement_blocks_early_answer(self):
        asyncio.run(self._file_output_requirement_blocks_early_answer())

    def test_research_contract_blocks_answer_until_sources_exist(self):
        asyncio.run(self._research_contract_blocks_answer_until_sources_exist())

    def test_create_file_contract_requires_verified_artifact(self):
        asyncio.run(self._create_file_contract_requires_verified_artifact())

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
                "Hej!", events.append, asyncio.Event(), coordinator_model="gemma4:12b"
            )

        self.assertEqual("done", outcome.status)
        self.assertEqual("", outcome.action_log)
        self.assertFalse(any(e["type"] == "consult" for e in events))

    async def _consults_expert_and_grounds_outcome(self):
        from agents import coordinator

        events: list[dict] = []
        consulted: list[tuple] = []

        async def fake_consult(model, task, refined, conversation, emit, abort):
            consulted.append((model, refined))
            return "def reverse(s): return s[::-1]"

        decisions = [
            {"action": "consult", "model": "qwen2.5-coder:14b", "thinking": "code"},
            {"action": "answer", "thinking": "have it"},
        ]
        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "refine_query", new=_av("Write a Python function that reverses a string")), \
             mock.patch.object(coordinator, "_consult_expert", new=fake_consult), \
             mock.patch.object(coordinator, "_decide_step", new=_seq(decisions)):
            outcome = await coordinator.run_coordinator(
                "Vänd en sträng i Python", events.append, asyncio.Event(), coordinator_model="gemma4:12b"
            )

        # Consulted the coder with the gateway-refined (English) instruction.
        self.assertEqual([("qwen2.5-coder:14b", "Write a Python function that reverses a string")], consulted)
        self.assertEqual("done", outcome.status)
        self.assertIn("s[::-1]", outcome.action_log)
        self.assertTrue(any(e["type"] == "consult" and e["model"] == "qwen2.5-coder:14b" for e in events))

    async def _skips_unavailable_expert(self):
        from agents import coordinator

        called: list[str] = []

        async def fake_consult(model, task, refined, conversation, emit, abort):
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
                "Do a thing", lambda e: None, asyncio.Event(), coordinator_model="gemma4:12b"
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
                coordinator_model="gemma4:12b", session_id="sess-1",
            )

        self.assertEqual([("Jag heter Lucas.", "fact", "sess-1")], saved)
        self.assertEqual("done", outcome.status)
        self.assertTrue(any(e["type"] == "memory" for e in events))

    async def _clarify_action_returns_needs_input(self):
        from agents import coordinator

        decisions = [{"action": "clarify", "question": "Vilken fil menar du?", "thinking": "vague"}]
        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "_decide_step", new=_seq(decisions)):
            outcome = await coordinator.run_coordinator(
                "fixa den", lambda e: None, asyncio.Event(), coordinator_model="gemma4:12b"
            )

        self.assertEqual("needs_input", outcome.status)
        self.assertEqual("Vilken fil menar du?", outcome.detail)

    async def _required_first_tool_runs_before_answer(self):
        from agents import coordinator

        events: list[dict] = []

        async def fake_execute(tool, args, emit):
            return "backend files: api/ws.py, agents/coordinator.py"

        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "search_skills", new=_av([])), \
             mock.patch.object(coordinator.agent_loop, "execute_tool", new=fake_execute), \
             mock.patch.object(coordinator, "_decide_step", new=_seq([{"action": "answer", "thinking": "have files"}])):
            outcome = await coordinator.run_coordinator(
                "Förklara backendflödet",
                events.append,
                asyncio.Event(),
                coordinator_model="gemma4:12b",
                required_first_tool={"tool": "list_dir", "args": {"path": "."}},
            )

        self.assertEqual("done", outcome.status)
        self.assertIn("list_dir", outcome.action_log)
        self.assertTrue(any(e["type"] == "action" and e["tool"] == "list_dir" for e in events))

    async def _file_output_requirement_blocks_early_answer(self):
        from agents import coordinator

        events: list[dict] = []

        async def fake_execute(tool, args, emit):
            if "Test-Path" in args.get("cmd", ""):
                return "Command: Test-Path model_report.md\nOutput:\nTrue"
            return "Command: Set-Content model_report.md\nOutput:\n"

        decisions = [
            {"action": "answer", "thinking": "too early"},
            {
                "action": "tool",
                "tool": "run_command",
                "args": {"cmd": "Set-Content -Path model_report.md -Value 'ok'"},
                "thinking": "write report",
            },
            {
                "action": "tool",
                "tool": "run_command",
                "args": {"cmd": "Test-Path model_report.md"},
                "thinking": "verify report",
            },
            {"action": "answer", "thinking": "done"},
        ]
        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "search_skills", new=_av([])), \
             mock.patch.object(coordinator.agent_loop, "execute_tool", new=fake_execute), \
             mock.patch.object(coordinator, "_decide_step", new=_seq(decisions)):
            outcome = await coordinator.run_coordinator(
                "Skapa en rapportfil",
                events.append,
                asyncio.Event(),
                coordinator_model="gemma4:12b",
                require_file_output=True,
            )

        self.assertEqual("done", outcome.status)
        self.assertIn("Set-Content", outcome.action_log)
        self.assertIn("Test-Path", outcome.action_log)
        self.assertTrue(any(e["type"] == "action" and e["tool"] == "run_command" for e in events))

    async def _research_contract_blocks_answer_until_sources_exist(self):
        from agents import coordinator

        events: list[dict] = []

        async def fake_execute(tool, args, emit):
            return "Research results for 'pilot':\nSources fetched: 2\n- Source: example"

        decisions = [
            {"action": "answer", "thinking": "too early"},
            {
                "action": "tool",
                "tool": "web_research",
                "args": {"query": "pilot", "task": "research pilot"},
                "thinking": "need sources",
            },
            {"action": "answer", "thinking": "done"},
        ]
        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "search_skills", new=_av([])), \
             mock.patch.object(coordinator.agent_loop, "execute_tool", new=fake_execute), \
             mock.patch.object(coordinator, "_decide_step", new=_seq(decisions)):
            outcome = await coordinator.run_coordinator(
                "Research Pilot",
                events.append,
                asyncio.Event(),
                coordinator_model="gemma4:12b",
                task_contract_intent="research",
            )

        self.assertEqual("done", outcome.status)
        self.assertIn("web_research", outcome.action_log)
        self.assertIn("Contract not satisfied", outcome.action_log)

    async def _create_file_contract_requires_verified_artifact(self):
        from agents import coordinator

        events: list[dict] = []

        async def fake_execute(tool, args, emit):
            cmd = args.get("cmd", "")
            if "Test-Path" in cmd:
                return "Command: Test-Path report.md\nOutput:\nTrue"
            return "Command: Set-Content report.md\nOutput:\n"

        decisions = [
            {
                "action": "tool",
                "tool": "run_command",
                "args": {"cmd": "Set-Content -Path report.md -Value 'ok'"},
                "thinking": "write report",
            },
            {"action": "answer", "thinking": "written but not verified"},
            {
                "action": "tool",
                "tool": "run_command",
                "args": {"cmd": "Test-Path report.md"},
                "thinking": "verify report",
            },
            {"action": "answer", "thinking": "done"},
        ]
        with mock.patch.object(coordinator, "available_expert_models", new=_av(self._experts())), \
             mock.patch.object(coordinator, "search_skills", new=_av([])), \
             mock.patch.object(coordinator.agent_loop, "execute_tool", new=fake_execute), \
             mock.patch.object(coordinator, "_decide_step", new=_seq(decisions)):
            outcome = await coordinator.run_coordinator(
                "Skapa en rapportfil",
                events.append,
                asyncio.Event(),
                coordinator_model="gemma4:12b",
                task_contract_intent="create_file",
            )

        self.assertEqual("done", outcome.status)
        self.assertIn("Test-Path report.md", outcome.action_log)


class ToolCallMappingTests(unittest.TestCase):
    """Fas B: native tool-calling decisions map to the coordinator's action dict,
    with a hardened JSON-from-content fallback."""

    def test_os_tool_call_maps_to_tool_action(self):
        from agents import coordinator
        d = coordinator._decision_from_message({
            "content": "looking at the repo",
            "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "README.md"}}}],
        })
        self.assertEqual("tool", d["action"])
        self.assertEqual("read_file", d["tool"])
        self.assertEqual({"path": "README.md"}, d["args"])
        self.assertEqual("looking at the repo", d["thinking"])

    def test_meta_action_call_maps_to_named_action(self):
        from agents import coordinator
        d = coordinator._decision_from_message({
            "tool_calls": [{"function": {"name": "consult", "arguments": {"model": "qwen2.5-coder:14b"}}}],
        })
        self.assertEqual("consult", d["action"])
        self.assertEqual("qwen2.5-coder:14b", d["model"])

    def test_answer_meta_action(self):
        from agents import coordinator
        d = coordinator._decision_from_message({
            "tool_calls": [{"function": {"name": "answer", "arguments": {}}}],
        })
        self.assertEqual("answer", d["action"])

    def test_arguments_as_json_string_are_parsed(self):
        from agents import coordinator
        d = coordinator._decision_from_message({
            "tool_calls": [{"function": {"name": "find_file", "arguments": "{\"name\": \"cv.pdf\"}"}}],
        })
        self.assertEqual("find_file", d["tool"])
        self.assertEqual({"name": "cv.pdf"}, d["args"])

    def test_no_tool_call_falls_back_to_json_content(self):
        from agents import coordinator
        d = coordinator._decision_from_message({
            "content": '{"action": "perceive", "thinking": "need the screen"}',
            "tool_calls": [],
        })
        self.assertEqual("perceive", d["action"])

    def test_no_tool_call_and_prose_defaults_to_answer(self):
        from agents import coordinator
        d = coordinator._decision_from_message({"content": "Sure, here is the answer..."})
        self.assertEqual("answer", d["action"])

    def test_gemma_action_is_tool_name_is_remapped(self):
        # gemma4 emits {"action": "read_file", ...} — the tool name as the action.
        from agents import coordinator
        d = coordinator._decision_from_message({
            "content": '{"action": "read_file", "args": {"path": "README.md"}}',
        })
        self.assertEqual("tool", d["action"])
        self.assertEqual("read_file", d["tool"])
        self.assertEqual({"path": "README.md"}, d["args"])

    def test_openai_name_arguments_shape_as_text_is_remapped(self):
        # qwen2.5-coder writes {"name","arguments"} as content instead of tool_calls.
        from agents import coordinator
        d = coordinator._decision_from_message({
            "content": '{"name": "list_dir", "arguments": {"path": "."}}',
        })
        self.assertEqual("tool", d["action"])
        self.assertEqual("list_dir", d["tool"])
        self.assertEqual({"path": "."}, d["args"])

    def test_meta_schemas_constrain_consult_to_available_experts(self):
        from agents import coordinator
        schemas = coordinator._meta_action_schemas({"qwen2.5-coder:14b": {}, "gpt-oss:20b": {}})
        consult = next(s for s in schemas if s["function"]["name"] == "consult")
        self.assertEqual(
            ["qwen2.5-coder:14b", "gpt-oss:20b"],
            consult["function"]["parameters"]["properties"]["model"]["enum"],
        )


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

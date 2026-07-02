import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


async def _async_value(value):
    return value


class AgentSafetyTests(unittest.TestCase):
    def test_blocks_desktop_input_without_visual_context(self):
        from agents.safety import unsafe_tool_block_reason

        reason = unsafe_tool_block_reason("type_text", "Säg hej", "")

        self.assertIsNotNone(reason)
        self.assertIn("visual context", reason)

    def test_allows_non_desktop_tool_without_visual_context(self):
        from agents.safety import unsafe_tool_block_reason

        self.assertIsNone(unsafe_tool_block_reason("run_command", "List files", ""))

    def test_allows_desktop_input_when_screen_has_been_observed(self):
        from agents.safety import unsafe_tool_block_reason

        reason = unsafe_tool_block_reason(
            "type_text",
            "Skriv hej i det aktiva Notepad-fönstret",
            "Screen observation: Notepad is focused with an empty document.",
        )

        self.assertIsNone(reason)


class JsonUtilTests(unittest.TestCase):
    def test_loads_lenient_repairs_unescaped_windows_paths(self):
        from agents.json_utils import loads_lenient

        parsed = loads_lenient(
            '{"tool": "run_command", "args": {"cmd": "dir"}, '
            '"thinking": "cwd is C:\\Users\\dev\\Code\\pilot\\backend"}'
        )

        self.assertEqual("run_command", parsed["tool"])
        self.assertEqual("dir", parsed["args"]["cmd"])

    def test_extract_json_object_from_fenced_block(self):
        from agents.json_utils import extract_json_object

        parsed = extract_json_object(
            'Here you go:\n```json\n{"action": "answer", "thinking": "done"}\n```',
            {"action": "answer"},
        )

        self.assertEqual("answer", parsed["action"])

    def test_extract_json_object_returns_default_on_garbage(self):
        from agents.json_utils import extract_json_object

        self.assertEqual({"x": 1}, extract_json_object("no json here", {"x": 1}))


class RouterPromptTests(unittest.TestCase):
    def test_router_prompt_includes_screen_observation_and_current_safety_rule(self):
        from agents.router import build_router_messages

        messages = build_router_messages(
            task="Open Notepad",
            history=[],
            failed_tools=None,
            screen_observation="Screen observation: Start menu is visible.",
        )

        joined = "\n".join(message["content"] for message in messages)
        self.assertIn("Current screen observation:", joined)
        self.assertIn("Start menu is visible", joined)
        # SoM era: click_element is preferred and named first in the safety rule.
        self.assertIn("Do not use click_element, click, type_text", joined)

    def test_router_prompt_includes_command_environment_and_stop_rules(self):
        from agents.router import build_router_messages

        messages = build_router_messages("List backend files", [], None, "")

        joined = "\n".join(message["content"] for message in messages)
        self.assertIn("Command environment:", joined)
        self.assertIn("Shell: Windows cmd", joined)
        self.assertIn("Current working directory:", joined)
        self.assertIn("If a command result directly answers the user task, use done", joined)
        self.assertIn("Do not repeat the same command", joined)

    def test_router_prompt_keeps_enough_command_output_to_avoid_false_truncation(self):
        from agents.router import build_router_messages

        command_output = (
            "Command: dir\n"
            "Current working directory: C:\\Users\\dev\\Code\\pilot\\backend\n"
            "Output:\n"
            + "\n".join(f"filler-{i}" for i in range(40))
            + "\nuv.lock\n"
        )

        messages = build_router_messages(
            task="Kolla vilken mapp backend kör i och lista filerna där",
            history=[{"type": "action", "content": command_output}],
            failed_tools=None,
            screen_observation="",
        )

        joined = "\n".join(message["content"] for message in messages)
        self.assertIn("uv.lock", joined)

    def test_router_prompt_lists_os_tools_before_shell_fallback(self):
        from agents.router import build_router_messages

        messages = build_router_messages("Visa innehållet i README.md", [], None, "")
        joined = "\n".join(message["content"] for message in messages)

        self.assertIn("list_dir(path?)", joined)
        self.assertIn("read_file(path)", joined)
        self.assertIn("find_file(name, root?)", joined)
        self.assertLess(joined.index("read_file(path)"), joined.index("run_command"))


class AgentLoopTests(unittest.TestCase):
    """The loop returns a LoopOutcome; the reply/`done` is owned by the WS layer."""

    def test_blocks_unsafe_desktop_action_without_visual_context(self):
        asyncio.run(self._blocks_unsafe_desktop_action_without_visual_context())

    def test_preserves_router_done_summary_when_no_actions_ran(self):
        asyncio.run(self._preserves_router_done_summary_when_no_actions_ran())

    def test_does_not_duplicate_streamed_command_output(self):
        asyncio.run(self._does_not_duplicate_streamed_command_output())

    def test_blocks_third_identical_command(self):
        asyncio.run(self._blocks_third_identical_command())

    def test_agent_loop_requires_confirmation_for_high_risk_command(self):
        asyncio.run(self._agent_loop_requires_confirmation_for_high_risk_command())

    def test_run_command_result_includes_cwd_for_history(self):
        asyncio.run(self._run_command_result_includes_cwd_for_history())

    def test_auto_completes_simple_directory_listing_after_first_command(self):
        asyncio.run(self._auto_completes_simple_directory_listing_after_first_command())

    def test_os_tool_finishes_without_final_screenshot(self):
        asyncio.run(self._os_tool_finishes_without_final_screenshot())

    def test_desktop_action_perceives_before_and_after(self):
        asyncio.run(self._desktop_action_perceives_before_and_after())

    def test_rejects_done_for_text_task_before_type_action(self):
        asyncio.run(self._rejects_done_for_text_task_before_type_action())

    def test_blocks_type_text_when_opened_app_is_not_focused(self):
        asyncio.run(self._blocks_type_text_when_opened_app_is_not_focused())

    def test_execute_tool_dispatches_generate_image(self):
        asyncio.run(self._execute_tool_dispatches_generate_image())

    async def _blocks_unsafe_desktop_action_without_visual_context(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route(*args, **kwargs):
            return {"tool": "type_text", "args": {"text": "hej"}, "thinking": "typing"}

        async def fake_execute(tool, args, emit):
            executed.append(tool)
            return "executed"

        with self._patched(loop, route_next_action=fake_route, execute_tool=fake_execute, PERCEPTION_ENABLED=False):
            outcome = await loop.run_agent_loop("Säg hej", events.append, asyncio.Event())

        self.assertEqual("blocked", outcome.status)
        self.assertEqual([], executed)
        self.assertTrue(any(e["type"] == "error" for e in events))

    async def _preserves_router_done_summary_when_no_actions_ran(self):
        from agents import loop

        async def fake_route(*args, **kwargs):
            return {"tool": "done", "args": {"summary": "I can say hej here; no desktop action needed."}, "thinking": "done"}

        with self._patched(loop, route_next_action=fake_route):
            outcome = await loop.run_agent_loop("Säg hej", lambda e: None, asyncio.Event())

        self.assertEqual("done", outcome.status)
        self.assertEqual("I can say hej here; no desktop action needed.", outcome.detail)

    async def _does_not_duplicate_streamed_command_output(self):
        from agents import loop

        events: list[dict] = []
        decisions = [
            {"tool": "run_command", "args": {"cmd": "dir"}, "thinking": "list"},
            {"tool": "done", "args": {"summary": "Listed."}, "thinking": "done"},
        ]

        async def fake_route(*args, **kwargs):
            return decisions.pop(0)

        async def fake_execute(tool, args, emit):
            emit({"type": "result", "content": "Directory of C:\\repo\n"})
            return "Directory of C:\\repo\n"

        with self._patched(loop, route_next_action=fake_route, execute_tool=fake_execute, asyncio_sleep=True):
            await loop.run_agent_loop("List backend files", events.append, asyncio.Event())

        results = [e.get("content", "") for e in events if e["type"] == "result"]
        self.assertEqual(1, sum("Directory of" in c for c in results))

    async def _blocks_third_identical_command(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route(*args, **kwargs):
            return {"tool": "run_command", "args": {"cmd": "dir"}, "thinking": "list"}

        async def fake_execute(tool, args, emit):
            executed.append(args["cmd"])
            return "cwd\nsome output\n"  # no completion-summary trigger

        with self._patched(loop, route_next_action=fake_route, execute_tool=fake_execute, asyncio_sleep=True):
            outcome = await loop.run_agent_loop("List backend files", events.append, asyncio.Event())

        self.assertEqual(["dir", "dir"], executed)
        self.assertEqual("blocked", outcome.status)
        self.assertIn("Repeated command blocked", outcome.detail)

    async def _agent_loop_requires_confirmation_for_high_risk_command(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route(*args, **kwargs):
            return {"tool": "run_command", "args": {"cmd": "Remove-Item -Recurse .\\data"}, "thinking": "delete"}

        async def fake_execute(tool, args, emit):
            executed.append(tool)
            return "should not execute"

        with self._patched(loop, route_next_action=fake_route, execute_tool=fake_execute, asyncio_sleep=True):
            outcome = await loop.run_agent_loop("Ta bort data", events.append, asyncio.Event())

        self.assertEqual([], executed)
        self.assertEqual("needs_input", outcome.status)
        self.assertIn("bekräft", outcome.detail.lower())
        self.assertEqual("confirmation_required", outcome.runtime_state.actions[0]["decision"])
        self.assertTrue(any(e["type"] == "confirmation_required" for e in events))

    async def _run_command_result_includes_cwd_for_history(self):
        from agents import loop

        async def fake_run_command_async(cmd, cwd=None, status=None):
            yield "hello\n"

        with self._patched(loop, run_command_async=fake_run_command_async):
            result = await loop.execute_tool("run_command", {"cmd": "echo hello"}, lambda e: None)

        self.assertIn("Command: echo hello", result)
        self.assertIn("Current working directory:", result)
        self.assertIn("hello", result)

    async def _auto_completes_simple_directory_listing_after_first_command(self):
        from agents import loop

        executed: list[str] = []

        async def fake_route(*args, **kwargs):
            return {"tool": "run_command", "args": {"cmd": "dir"}, "thinking": "list"}

        async def fake_execute(tool, args, emit):
            executed.append(args["cmd"])
            return (
                "Command: dir\n"
                "Current working directory: C:\\Users\\dev\\Code\\pilot\\backend\n"
                "Output:\n Directory of C:\\Users\\dev\\Code\\pilot\\backend\n"
                "agents\napi\nuv.lock\n"
            )

        with self._patched(loop, route_next_action=fake_route, execute_tool=fake_execute, asyncio_sleep=True):
            outcome = await loop.run_agent_loop(
                "Kolla vilken mapp backend kör i och lista filerna där", lambda e: None, asyncio.Event()
            )

        self.assertEqual(["dir"], executed)
        self.assertEqual("done", outcome.status)
        self.assertIn("C:\\Users\\dev\\Code\\pilot\\backend", outcome.detail)
        self.assertIn("uv.lock", outcome.detail)

    async def _os_tool_finishes_without_final_screenshot(self):
        from agents import loop

        events: list[dict] = []

        async def fake_route(*args, **kwargs):
            return {"tool": "read_file", "args": {"path": "README.md"}, "thinking": "read"}

        async def fake_execute(tool, args, emit):
            return "File: C:\\repo\\README.md\nContent:\n# Pilot\n"

        with self._patched(loop, route_next_action=fake_route, execute_tool=fake_execute):
            outcome = await loop.run_agent_loop("Visa README.md", events.append, asyncio.Event())

        self.assertEqual("done", outcome.status)
        self.assertIn("# Pilot", outcome.detail)
        self.assertFalse(any(e["type"] == "screenshot" for e in events))

    async def _desktop_action_perceives_before_and_after(self):
        from agents import loop

        observations: list[int] = []
        executed: list[str] = []

        async def fake_route(*args, **kwargs):
            # Click once, then finish (history is the 2nd positional arg).
            history = args[1] if len(args) > 1 else kwargs.get("history", [])
            if any(i.get("type") == "action" for i in history):
                return {"tool": "done", "args": {"summary": "clicked"}, "thinking": "done"}
            return {"tool": "click", "args": {"x": 10, "y": 20}, "thinking": "click"}

        async def fake_perceive(task, history, emit):
            observations.append(len(history))
            return "Screen observation: Notepad is focused."

        async def fake_execute(tool, args, emit):
            executed.append(tool)
            return "Clicked"

        with self._patched(loop, route_next_action=fake_route, perceive=fake_perceive, execute_tool=fake_execute, asyncio_sleep=True, PERCEPTION_ENABLED=True):
            outcome = await loop.run_agent_loop("Klicka i Notepad", lambda e: None, asyncio.Event())

        self.assertEqual("done", outcome.status)
        self.assertEqual(["click"], executed)
        # Perceived once before the click (no observation yet) and once after it.
        self.assertGreaterEqual(len(observations), 2)

    async def _rejects_done_for_text_task_before_type_action(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route(*args, **kwargs):
            history = args[1] if len(args) > 1 else kwargs.get("history", [])
            typed = any(str(i.get("content", "")).startswith("type_text(") for i in history if i.get("type") == "action")
            opened = any(str(i.get("content", "")).startswith("open_app(") for i in history if i.get("type") == "action")
            rejected = any(i.get("type") == "done_rejected" for i in history)
            if typed:
                return {"tool": "done", "args": {"summary": "Text was typed."}, "thinking": "done"}
            if rejected:
                return {"tool": "type_text", "args": {"text": "hej"}, "thinking": "type"}
            if opened:
                return {"tool": "done", "args": {"summary": "opened only"}, "thinking": "too early"}
            return {"tool": "open_app", "args": {"name": "notepad"}, "thinking": "open"}

        async def fake_execute(tool, args, emit):
            executed.append(tool)
            return f"{tool} ok"

        async def fake_perceive(task, history, emit):
            return "Screen observation: Notepad is focused."

        with self._patched(loop, route_next_action=fake_route, execute_tool=fake_execute, perceive=fake_perceive, asyncio_sleep=True, active_window_title=lambda: "Untitled - Notepad"):
            outcome = await loop.run_agent_loop("Öppna Notepad och skriv hej", events.append, asyncio.Event())

        self.assertEqual(["open_app", "type_text"], executed)
        self.assertEqual("done", outcome.status)
        self.assertEqual("Text was typed.", outcome.detail)
        self.assertTrue(any("Done rejected" in e.get("content", "") for e in events))

    async def _blocks_type_text_when_opened_app_is_not_focused(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route(*args, **kwargs):
            history = args[1] if len(args) > 1 else kwargs.get("history", [])
            if any(str(i.get("content", "")).startswith("open_app(") for i in history if i.get("type") == "action"):
                return {"tool": "type_text", "args": {"text": "hej"}, "thinking": "type"}
            return {"tool": "open_app", "args": {"name": "notepad"}, "thinking": "open"}

        async def fake_execute(tool, args, emit):
            executed.append(tool)
            return f"{tool} ok"

        async def fake_perceive(task, history, emit):
            return "Screen observation: Codex is focused."

        with self._patched(loop, route_next_action=fake_route, execute_tool=fake_execute, perceive=fake_perceive, asyncio_sleep=True, active_window_title=lambda: "Codex"):
            outcome = await loop.run_agent_loop("Öppna Notepad och skriv hej", events.append, asyncio.Event())

        self.assertEqual(["open_app"], executed)
        self.assertEqual("blocked", outcome.status)
        self.assertTrue(any(e["type"] == "error" and "active window" in e["content"] for e in events))

    async def _execute_tool_dispatches_generate_image(self):
        from agents import loop

        with mock.patch.object(
            loop,
            "generate_image",
            return_value="Generated image with ComfyUI\nFiles:\nC:\\out\\pilot.png",
        ) as generate:
            result = await loop.execute_tool(
                "generate_image",
                {"prompt": "red robot", "width": 512, "height": 512, "steps": 12, "seed": 42},
                lambda e: None,
            )

        self.assertIn("C:\\out\\pilot.png", result)
        generate.assert_called_once_with("red robot", width=512, height=512, steps=12, seed=42)

    # --- helper -------------------------------------------------------------
    class _patched:
        """Context manager that swaps loop module attributes and restores them.

        ``asyncio_sleep=True`` no-ops the loop's inter-step sleep; pass any other
        callable/value by attribute name (route_next_action, execute_tool,
        perceive, run_command_async, active_window_title, PERCEPTION_ENABLED).
        """

        def __init__(self, loop_module, asyncio_sleep=False, **attrs):
            self.loop = loop_module
            self.attrs = attrs
            self.sleep = asyncio_sleep
            self.saved: dict = {}

        def __enter__(self):
            for name, value in self.attrs.items():
                self.saved[name] = getattr(self.loop, name)
                setattr(self.loop, name, value)
            if self.sleep:
                self._orig_sleep = self.loop.asyncio.sleep
                async def _no_sleep(_s):
                    return None
                self.loop.asyncio.sleep = _no_sleep
            return self

        def __exit__(self, *exc):
            for name, value in self.saved.items():
                setattr(self.loop, name, value)
            if self.sleep:
                self.loop.asyncio.sleep = self._orig_sleep
            return False


class OsToolTests(unittest.TestCase):
    def test_open_app_maps_calculator_display_name_to_windows_command(self):
        from tools import system

        with mock.patch.object(system.subprocess, "Popen") as popen:
            result = system.open_app("Calculator")

        popen.assert_called_once_with("calc", shell=True)
        self.assertEqual("Opened: Calculator", result)

    def test_read_file_finds_repo_root_from_backend_cwd(self):
        from tools.os_tools import read_file

        result = read_file("README.md")

        self.assertTrue(result["path"].endswith("README.md"))
        self.assertIn("# Pilot", result["text"])

    def test_list_dir_returns_structured_entries(self):
        from tools.os_tools import list_dir

        result = list_dir(str(Path(__file__).parents[2] / "frontend"))
        names = {entry["name"] for entry in result["entries"]}

        self.assertIn("package.json", names)
        self.assertIn("app", names)

    def test_find_file_finds_readme_from_backend(self):
        from tools.os_tools import find_file

        result = find_file("README.md")

        self.assertTrue(any(match.endswith("README.md") for match in result["matches"]))

    def test_window_tools_can_be_mocked(self):
        from tools import os_tools

        class FakeWindow:
            title = "Untitled - Notepad"

            def activate(self):
                self.activated = True

        fake = FakeWindow()
        original_get_all = os_tools._get_all_windows
        try:
            os_tools._get_all_windows = lambda: [fake]
            self.assertEqual(["Untitled - Notepad"], [w["title"] for w in os_tools.list_windows()["windows"]])
            self.assertIn("Untitled - Notepad", os_tools.focus_window("Notepad")["focused"])
        finally:
            os_tools._get_all_windows = original_get_all


class ConfigAndMcpTests(unittest.TestCase):
    def test_start_backend_sets_ollama_defaults(self):
        text = (Path(__file__).parents[2] / "start-backend.bat").read_text(encoding="utf-8")

        self.assertIn("OLLAMA_MODEL", text)
        self.assertIn("OLLAMA_VISION_MODEL", text)
        self.assertIn("OLLAMA_VISION_ENABLED", text)
        self.assertIn("OLLAMA_BASE_URL", text)

    def test_backend_env_file_contains_validated_model_defaults(self):
        # Validate the committed template (.env is gitignored and absent in CI).
        example = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
        self.assertIn("OLLAMA_MODEL=gemma4:12b", example)
        self.assertIn("OLLAMA_VISION_MODEL=qwen3.5:9b", example)

        # If a local .env exists, it must agree with the validated defaults.
        env = Path(__file__).parents[1] / ".env"
        if env.is_file():
            text = env.read_text(encoding="utf-8")
            self.assertIn("OLLAMA_MODEL=gemma4:12b", text)
            self.assertIn("OLLAMA_VISION_MODEL=qwen3.5:9b", text)

    def test_mcp_manifest_includes_os_tools(self):
        from api.mcp import tools_manifest

        names = {tool["name"] for tool in tools_manifest["tools"]}

        self.assertIn("pilot_list_dir", names)
        self.assertIn("pilot_read_file", names)
        self.assertIn("pilot_find_file", names)
        self.assertIn("pilot_list_windows", names)
        self.assertIn("pilot_focus_window", names)


if __name__ == "__main__":
    unittest.main()

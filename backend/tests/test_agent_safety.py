import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class AgentSafetyTests(unittest.TestCase):
    def test_blocks_desktop_input_without_visual_context(self):
        from agents.safety import unsafe_tool_block_reason

        reason = unsafe_tool_block_reason("type_text", "Säg hej", "")

        self.assertIsNotNone(reason)
        self.assertIn("visual context", reason)

    def test_allows_non_desktop_tool_without_visual_context(self):
        from agents.safety import unsafe_tool_block_reason

        reason = unsafe_tool_block_reason("run_command", "List files", "")

        self.assertIsNone(reason)

    def test_allows_desktop_input_when_screen_has_been_observed(self):
        from agents.safety import unsafe_tool_block_reason

        reason = unsafe_tool_block_reason(
            "type_text",
            "Skriv hej i det aktiva Notepad-fönstret",
            "Screen observation: Notepad is focused with an empty document.",
        )

        self.assertIsNone(reason)


class RouterPromptTests(unittest.TestCase):
    def test_parse_json_repairs_unescaped_windows_paths_in_strings(self):
        from agents.router import _parse_json

        parsed = _parse_json(
            '{"tool": "run_command", "args": {"cmd": "dir"}, '
            '"thinking": "cwd is C:\\Users\\lucas\\Code\\pilot\\backend"}'
        )

        self.assertEqual("run_command", parsed["tool"])
        self.assertEqual("dir", parsed["args"]["cmd"])

    def test_router_prompt_includes_current_screen_observation_and_safety_rule(self):
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
        self.assertIn("Do not use click, type_text, scroll, move_mouse, key_press, or hotkey", joined)

    def test_router_prompt_includes_command_environment_and_stop_rules(self):
        from agents.router import build_router_messages

        messages = build_router_messages(
            task="List backend files",
            history=[],
            failed_tools=None,
            screen_observation="",
        )

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
            "Current working directory: C:\\Users\\lucas\\Code\\pilot\\backend\n"
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
    def test_loop_blocks_unsafe_desktop_action_before_execution_without_visual_context(self):
        asyncio.run(self._run_loop_blocks_unsafe_desktop_action_before_execution_without_visual_context())

    def test_loop_preserves_router_done_summary_when_no_actions_ran(self):
        asyncio.run(self._run_loop_preserves_router_done_summary_when_no_actions_ran())

    def test_loop_does_not_duplicate_streamed_command_output(self):
        asyncio.run(self._run_loop_does_not_duplicate_streamed_command_output())

    def test_loop_blocks_third_identical_command(self):
        asyncio.run(self._run_loop_blocks_third_identical_command())

    def test_run_command_result_includes_cwd_for_history(self):
        asyncio.run(self._run_command_result_includes_cwd_for_history())

    def test_loop_auto_completes_simple_directory_listing_after_first_command(self):
        asyncio.run(self._run_loop_auto_completes_simple_directory_listing_after_first_command())

    def test_loop_executes_os_tool_and_finishes_without_final_screenshot(self):
        asyncio.run(self._run_loop_executes_os_tool_and_finishes_without_final_screenshot())

    def test_vision_enabled_desktop_action_observes_after_action(self):
        asyncio.run(self._run_vision_enabled_desktop_action_observes_after_action())

    def test_loop_rejects_done_for_text_task_before_type_action(self):
        asyncio.run(self._run_loop_rejects_done_for_text_task_before_type_action())

    def test_loop_blocks_type_text_when_opened_app_is_not_focused(self):
        asyncio.run(self._run_loop_blocks_type_text_when_opened_app_is_not_focused())

    async def _run_loop_blocks_unsafe_desktop_action_before_execution_without_visual_context(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route_next_action(task, history, failed_tools, screen_observation=None):
            return {"tool": "type_text", "args": {"text": "hej"}, "thinking": "typing"}

        async def fake_execute_tool(tool, args, emit):
            executed.append(tool)
            return "executed"

        def fake_emit(event):
            events.append(event)

        async def fake_observe_screen(task, history, emit):
            return ""

        original_route = loop.route_next_action
        original_execute = loop.execute_tool
        original_observe = loop.observe_screen
        try:
            loop.route_next_action = fake_route_next_action
            loop.execute_tool = fake_execute_tool
            loop.observe_screen = fake_observe_screen

            await loop.run_agent_loop("Säg hej", fake_emit, asyncio.Event())
        finally:
            loop.route_next_action = original_route
            loop.execute_tool = original_execute
            loop.observe_screen = original_observe

        self.assertEqual([], executed)
        self.assertTrue(any(event["type"] == "error" for event in events))
        self.assertTrue(any(event["type"] == "done" for event in events))

    async def _run_loop_preserves_router_done_summary_when_no_actions_ran(self):
        from agents import loop

        events: list[dict] = []

        async def fake_route_next_action(task, history, failed_tools, screen_observation=None):
            return {
                "tool": "done",
                "args": {"summary": "I can say hej here, but no desktop action was needed."},
                "thinking": "no desktop action needed",
            }

        async def fake_text_done_summary(task, history):
            return "No actions were recorded."

        def fake_emit(event):
            events.append(event)

        original_route = loop.route_next_action
        original_observe = loop.observe_screen
        original_screenshot = loop.screenshot
        original_text_done_summary = loop.text_done_summary
        try:
            loop.route_next_action = fake_route_next_action
            loop.observe_screen = lambda task, history, emit: _async_value("")
            loop.screenshot = lambda: "fake-image"
            loop.text_done_summary = fake_text_done_summary

            await loop.run_agent_loop("Säg hej", fake_emit, asyncio.Event())
        finally:
            loop.route_next_action = original_route
            loop.observe_screen = original_observe
            loop.screenshot = original_screenshot
            loop.text_done_summary = original_text_done_summary

        done_events = [event for event in events if event["type"] == "done"]
        self.assertEqual("I can say hej here, but no desktop action was needed.", done_events[-1]["summary"])

    async def _run_loop_does_not_duplicate_streamed_command_output(self):
        from agents import loop

        events: list[dict] = []
        decisions = [
            {"tool": "run_command", "args": {"cmd": "dir"}, "thinking": "list files"},
            {"tool": "done", "args": {"summary": "Listed files."}, "thinking": "done"},
        ]

        async def fake_route_next_action(task, history, failed_tools, screen_observation=None):
            return decisions.pop(0)

        async def fake_execute_tool(tool, args, emit):
            emit({"type": "result", "content": "Directory of C:\\Users\\lucas\\Code\\pilot\\backend\n"})
            return "cwd=C:\\Users\\lucas\\Code\\pilot\\backend\nDirectory of C:\\Users\\lucas\\Code\\pilot\\backend\n"

        def fake_emit(event):
            events.append(event)

        originals = (
            loop.route_next_action,
            loop.execute_tool,
            loop.observe_screen,
            loop.screenshot,
        )
        try:
            loop.route_next_action = fake_route_next_action
            loop.execute_tool = fake_execute_tool
            loop.observe_screen = lambda task, history, emit: _async_value("")
            loop.screenshot = lambda: "fake-image"

            await loop.run_agent_loop("List backend files", fake_emit, asyncio.Event())
        finally:
            loop.route_next_action, loop.execute_tool, loop.observe_screen, loop.screenshot = originals

        result_contents = [event.get("content", "") for event in events if event["type"] == "result"]
        occurrences = sum("Directory of" in content for content in result_contents)
        self.assertEqual(1, occurrences)

    async def _run_loop_blocks_third_identical_command(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route_next_action(task, history, failed_tools, screen_observation=None):
            return {"tool": "run_command", "args": {"cmd": "dir"}, "thinking": "list"}

        async def fake_execute_tool(tool, args, emit):
            executed.append(args["cmd"])
            return "cwd=C:\\Users\\lucas\\Code\\pilot\\backend\nDirectory of C:\\Users\\lucas\\Code\\pilot\\backend\n"

        async def fake_sleep(_seconds):
            return None

        def fake_emit(event):
            events.append(event)

        originals = (
            loop.route_next_action,
            loop.execute_tool,
            loop.observe_screen,
            loop.asyncio.sleep,
        )
        try:
            loop.route_next_action = fake_route_next_action
            loop.execute_tool = fake_execute_tool
            loop.observe_screen = lambda task, history, emit: _async_value("")
            loop.asyncio.sleep = fake_sleep

            await loop.run_agent_loop("List backend files", fake_emit, asyncio.Event())
        finally:
            loop.route_next_action, loop.execute_tool, loop.observe_screen, loop.asyncio.sleep = originals

        self.assertEqual(["dir", "dir"], executed)
        done_events = [event for event in events if event["type"] == "done"]
        self.assertIn("Repeated command blocked", done_events[-1]["summary"])

    async def _run_command_result_includes_cwd_for_history(self):
        from agents import loop

        events: list[dict] = []

        async def fake_run_command_async(cmd, cwd=None):
            yield "hello\n"

        original_run_command_async = loop.run_command_async
        try:
            loop.run_command_async = fake_run_command_async
            result = await loop.execute_tool("run_command", {"cmd": "echo hello"}, events.append)
        finally:
            loop.run_command_async = original_run_command_async

        self.assertIn("Command: echo hello", result)
        self.assertIn("Current working directory:", result)
        self.assertIn("hello", result)

    async def _run_loop_auto_completes_simple_directory_listing_after_first_command(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route_next_action(task, history, failed_tools, screen_observation=None):
            return {"tool": "run_command", "args": {"cmd": "dir"}, "thinking": "list files"}

        async def fake_execute_tool(tool, args, emit):
            executed.append(args["cmd"])
            return (
                "Command: dir\n"
                "Current working directory: C:\\Users\\lucas\\Code\\pilot\\backend\n"
                "Output:\n"
                " Directory of C:\\Users\\lucas\\Code\\pilot\\backend\n"
                "agents\napi\ntests\ntools\nuv.lock\n"
            )

        async def fake_sleep(_seconds):
            return None

        def fake_emit(event):
            events.append(event)

        originals = (
            loop.route_next_action,
            loop.execute_tool,
            loop.observe_screen,
            loop.asyncio.sleep,
        )
        try:
            loop.route_next_action = fake_route_next_action
            loop.execute_tool = fake_execute_tool
            loop.observe_screen = lambda task, history, emit: _async_value("")
            loop.asyncio.sleep = fake_sleep

            await loop.run_agent_loop(
                "Kolla vilken mapp backend kör i och lista filerna där",
                fake_emit,
                asyncio.Event(),
            )
        finally:
            loop.route_next_action, loop.execute_tool, loop.observe_screen, loop.asyncio.sleep = originals

        self.assertEqual(["dir"], executed)
        done_events = [event for event in events if event["type"] == "done"]
        self.assertIn("C:\\Users\\lucas\\Code\\pilot\\backend", done_events[-1]["summary"])
        self.assertIn("uv.lock", done_events[-1]["summary"])

    async def _run_loop_executes_os_tool_and_finishes_without_final_screenshot(self):
        from agents import loop

        events: list[dict] = []

        async def fake_route_next_action(task, history, failed_tools, screen_observation=None):
            return {"tool": "read_file", "args": {"path": "README.md"}, "thinking": "read it"}

        async def fake_execute_tool(tool, args, emit):
            return "File: C:\\Users\\lucas\\Code\\pilot\\README.md\nContent:\n# Pilot\n"

        def fail_screenshot():
            raise AssertionError("done path should not take a final screenshot for OS tools")

        originals = (loop.route_next_action, loop.execute_tool, loop.observe_screen, loop.screenshot)
        try:
            loop.route_next_action = fake_route_next_action
            loop.execute_tool = fake_execute_tool
            loop.observe_screen = lambda task, history, emit: _async_value("")
            loop.screenshot = fail_screenshot

            await loop.run_agent_loop("Visa innehållet i README.md", events.append, asyncio.Event())
        finally:
            loop.route_next_action, loop.execute_tool, loop.observe_screen, loop.screenshot = originals

        done_events = [event for event in events if event["type"] == "done"]
        self.assertIn("# Pilot", done_events[-1]["summary"])
        self.assertFalse(any(event["type"] == "screenshot" for event in events))

    async def _run_vision_enabled_desktop_action_observes_after_action(self):
        from agents import loop

        events: list[dict] = []
        observations: list[int] = []

        async def fake_route_next_action(task, history, failed_tools, screen_observation=None):
            return {"tool": "click", "args": {"x": 10, "y": 20}, "thinking": "click"}

        async def fake_observe_screen(task, history, emit):
            observations.append(len(history))
            return "Screen observation: Notepad is focused."

        async def fake_execute_tool(tool, args, emit):
            return "Clicked left at (10, 20)"

        async def fake_sleep(_seconds):
            return None

        originals = (loop.route_next_action, loop.observe_screen, loop.execute_tool, loop.asyncio.sleep)
        try:
            loop.route_next_action = fake_route_next_action
            loop.observe_screen = fake_observe_screen
            loop.execute_tool = fake_execute_tool
            loop.asyncio.sleep = fake_sleep

            await loop.run_agent_loop("Klicka i Notepad", events.append, asyncio.Event())
        finally:
            loop.route_next_action, loop.observe_screen, loop.execute_tool, loop.asyncio.sleep = originals

        self.assertGreaterEqual(len(observations), 2)
        self.assertIn(1, observations)

    async def _run_loop_rejects_done_for_text_task_before_type_action(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route_next_action(task, history, failed_tools, screen_observation=None):
            if any(
                item.get("type") == "action"
                and str(item.get("content", "")).startswith("type_text(")
                for item in history
            ):
                return {"tool": "done", "args": {"summary": "Text was typed."}, "thinking": "done"}
            if any(item.get("type") == "done_rejected" for item in history):
                return {"tool": "type_text", "args": {"text": "hej"}, "thinking": "type it"}
            if any(
                item.get("type") == "action"
                and str(item.get("content", "")).startswith("open_app(")
                for item in history
            ):
                return {
                    "tool": "done",
                    "args": {"summary": "Notepad was opened and hej was written."},
                    "thinking": "done too early",
                }
            return {"tool": "open_app", "args": {"name": "notepad"}, "thinking": "open"}

        async def fake_execute_tool(tool, args, emit):
            executed.append(tool)
            return f"{tool} ok"

        async def fake_observe_screen(task, history, emit):
            return "Screen observation: Notepad is focused."

        async def fake_sleep(_seconds):
            return None

        original_active_window_title = getattr(loop, "active_window_title", None)
        originals = (loop.route_next_action, loop.execute_tool, loop.observe_screen, loop.asyncio.sleep)
        try:
            loop.route_next_action = fake_route_next_action
            loop.execute_tool = fake_execute_tool
            loop.observe_screen = fake_observe_screen
            loop.active_window_title = lambda: "Untitled - Notepad"
            loop.asyncio.sleep = fake_sleep

            await loop.run_agent_loop("Öppna Notepad och skriv hej", events.append, asyncio.Event())
        finally:
            loop.route_next_action, loop.execute_tool, loop.observe_screen, loop.asyncio.sleep = originals
            if original_active_window_title is None:
                delattr(loop, "active_window_title")
            else:
                loop.active_window_title = original_active_window_title

        self.assertEqual(["open_app", "type_text"], executed)
        self.assertTrue(any("Done rejected" in event.get("content", "") for event in events))
        done_events = [event for event in events if event["type"] == "done"]
        self.assertEqual("Text was typed.", done_events[-1]["summary"])

    async def _run_loop_blocks_type_text_when_opened_app_is_not_focused(self):
        from agents import loop

        events: list[dict] = []
        executed: list[str] = []

        async def fake_route_next_action(task, history, failed_tools, screen_observation=None):
            if any(
                item.get("type") == "action"
                and str(item.get("content", "")).startswith("open_app(")
                for item in history
            ):
                return {"tool": "type_text", "args": {"text": "hej"}, "thinking": "type it"}
            return {"tool": "open_app", "args": {"name": "notepad"}, "thinking": "open"}

        async def fake_execute_tool(tool, args, emit):
            executed.append(tool)
            return f"{tool} ok"

        async def fake_observe_screen(task, history, emit):
            return "Screen observation: Codex is focused."

        async def fake_sleep(_seconds):
            return None

        original_active_window_title = getattr(loop, "active_window_title", None)
        originals = (loop.route_next_action, loop.execute_tool, loop.observe_screen, loop.asyncio.sleep)
        try:
            loop.route_next_action = fake_route_next_action
            loop.execute_tool = fake_execute_tool
            loop.observe_screen = fake_observe_screen
            loop.active_window_title = lambda: "Codex"
            loop.asyncio.sleep = fake_sleep

            await loop.run_agent_loop("Öppna Notepad och skriv hej", events.append, asyncio.Event())
        finally:
            loop.route_next_action, loop.execute_tool, loop.observe_screen, loop.asyncio.sleep = originals
            if original_active_window_title is None:
                delattr(loop, "active_window_title")
            else:
                loop.active_window_title = original_active_window_title

        self.assertEqual(["open_app"], executed)
        error_events = [event for event in events if event["type"] == "error"]
        self.assertTrue(any("active window" in event["content"] for event in error_events))


class OsToolTests(unittest.TestCase):
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

    def test_backend_env_file_contains_gemma4_vision_defaults(self):
        text = (Path(__file__).parents[1] / ".env").read_text(encoding="utf-8")

        self.assertIn("OLLAMA_MODEL=gemma4:latest", text)
        self.assertIn("OLLAMA_VISION_MODEL=gemma4:latest", text)
        self.assertIn("OLLAMA_VISION_ENABLED=true", text)

    def test_mcp_manifest_includes_os_tools(self):
        from api.mcp import tools_manifest

        names = {tool["name"] for tool in tools_manifest["tools"]}

        self.assertIn("pilot_list_dir", names)
        self.assertIn("pilot_read_file", names)
        self.assertIn("pilot_find_file", names)
        self.assertIn("pilot_list_windows", names)
        self.assertIn("pilot_focus_window", names)


async def _async_value(value):
    return value


if __name__ == "__main__":
    unittest.main()

"""Direct tests for tools/codex_cli.py, the headless OpenAI Codex CLI driver.

The sibling of test_codex_driver.py (which covers tools/codex.py, the Claude
Code CLI driver) and of CodexCliResolverTests in test_traceability.py, which
already pins CLI-resolution precedence and the sandbox flag. This file covers
what those do not: the bundled-CLI glob in _find_bundled_codex, the win32
`cmd /c` wrapping branch of _build_cmd, and the whole run_codex_cli NDJSON
event parser.

Same technique as test_codex_driver.py: unittest.TestCase + unittest.mock, no
real filesystem and no real subprocess. Resolution is stubbed through
glob / os.path, and run_codex_cli is driven through a small fake process whose
async-iterable .stdout yields byte lines.
"""

import asyncio
import os
import sys
import unittest
from unittest import mock
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class _FakeStdout:
    """Async-iterable stdout yielding pre-canned byte lines, like a StreamReader."""

    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProcess:
    """Minimal stand-in for asyncio.subprocess.Process: stdout, wait, returncode."""

    def __init__(self, lines, returncode=0):
        self.stdout = _FakeStdout(lines)
        self.returncode = returncode
        self.waited = False

    async def wait(self):
        self.waited = True
        return self.returncode


def _run_codex_cli_events(lines, returncode=0, **kwargs):
    """Drive run_codex_cli over a fake subprocess and collect its yielded events."""
    import tools.codex_cli as codex_cli

    proc = _FakeProcess(
        [ln if isinstance(ln, bytes) else ln.encode() for ln in lines], returncode
    )

    async def go():
        with mock.patch.object(
            codex_cli.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
        ), mock.patch.object(codex_cli, "resolve_codex_cli", return_value="codex.exe"):
            return [ev async for ev in codex_cli.run_codex_cli("prompt", **kwargs)], proc

    return asyncio.run(go())


class FindBundledCodexTests(unittest.TestCase):
    def test_returns_none_when_localappdata_unset(self):
        import tools.codex_cli as codex_cli

        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("LOCALAPPDATA", None)
            self.assertIsNone(codex_cli._find_bundled_codex())

    def test_returns_none_when_glob_matches_nothing(self):
        import tools.codex_cli as codex_cli

        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\dev\AppData\Local"}), \
            mock.patch.object(codex_cli.glob, "glob", return_value=[]):
            self.assertIsNone(codex_cli._find_bundled_codex())

    def test_returns_none_when_no_glob_match_is_a_file(self):
        import tools.codex_cli as codex_cli

        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\dev\AppData\Local"}), \
            mock.patch.object(
                codex_cli.glob, "glob", return_value=[r"C:\bin\aaa\codex.exe"]
            ), \
            mock.patch.object(codex_cli.os.path, "isfile", return_value=False):
            self.assertIsNone(codex_cli._find_bundled_codex())

    def test_returns_most_recently_modified_match(self):
        import tools.codex_cli as codex_cli

        base = r"C:\Users\dev\AppData\Local\OpenAI\Codex\bin"
        candidates = [
            base + r"\aaa\codex.exe",
            base + r"\bbb\codex.exe",
            base + r"\ccc\codex.exe",
        ]
        mtimes = {
            candidates[0]: 100.0,
            candidates[1]: 300.0,  # newest, and neither first nor last in glob order
            candidates[2]: 200.0,
        }
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\dev\AppData\Local"}), \
            mock.patch.object(codex_cli.glob, "glob", return_value=candidates), \
            mock.patch.object(codex_cli.os.path, "isfile", return_value=True), \
            mock.patch.object(codex_cli.os.path, "getmtime", side_effect=mtimes.__getitem__):
            self.assertEqual(candidates[1], codex_cli._find_bundled_codex())

    def test_globs_under_the_codex_desktop_bin_directory(self):
        import tools.codex_cli as codex_cli

        globber = mock.Mock(return_value=[])
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\dev\AppData\Local"}), \
            mock.patch.object(codex_cli.glob, "glob", globber):
            codex_cli._find_bundled_codex()

        pattern = globber.call_args[0][0]
        self.assertEqual(
            os.path.join(
                r"C:\Users\dev\AppData\Local", "OpenAI", "Codex", "bin", "*", "codex.exe"
            ),
            pattern,
        )


class ResolveCodexCliTests(unittest.TestCase):
    """Precedence branches CodexCliResolverTests does not reach.

    test_traceability.py already pins bundled-over-PATH; these cover the
    explicit-absolute, PATH and bare-name fallbacks plus the module-level cache.
    Each resets _resolved_cli in a try/finally, mirroring that sibling class.
    """

    def test_prefers_explicit_absolute_cli_over_bundled_and_path(self):
        import tools.codex_cli as codex_cli

        explicit = r"C:\tools\codex\codex.exe"
        with mock.patch.object(codex_cli, "CODEX_CLI", explicit), \
            mock.patch.object(codex_cli.os.path, "isabs", return_value=True), \
            mock.patch.object(codex_cli.os.path, "isfile", return_value=True), \
            mock.patch.object(codex_cli.shutil, "which", return_value=r"C:\other\codex.exe"), \
            mock.patch.object(
                codex_cli, "_find_bundled_codex", return_value=r"C:\bundled\codex.exe"
            ):
            codex_cli._resolved_cli = None
            try:
                self.assertEqual(explicit, codex_cli.resolve_codex_cli())
            finally:
                codex_cli._resolved_cli = None

    def test_falls_back_to_path_when_no_bundled_cli(self):
        import tools.codex_cli as codex_cli

        on_path = r"C:\path\codex.exe"
        with mock.patch.object(codex_cli, "CODEX_CLI", "codex"), \
            mock.patch.object(codex_cli.shutil, "which", return_value=on_path), \
            mock.patch.object(codex_cli, "_find_bundled_codex", return_value=None):
            codex_cli._resolved_cli = None
            try:
                self.assertEqual(on_path, codex_cli.resolve_codex_cli())
            finally:
                codex_cli._resolved_cli = None

    def test_falls_back_to_bare_name_when_nothing_resolves(self):
        import tools.codex_cli as codex_cli

        with mock.patch.object(codex_cli, "CODEX_CLI", ""), \
            mock.patch.object(codex_cli.shutil, "which", return_value=None), \
            mock.patch.object(codex_cli, "_find_bundled_codex", return_value=None):
            codex_cli._resolved_cli = None
            try:
                # Empty CODEX_CLI falls through to the "codex" default name.
                self.assertEqual("codex", codex_cli.resolve_codex_cli())
            finally:
                codex_cli._resolved_cli = None

    def test_caches_resolution_in_module_level_resolved_cli(self):
        import tools.codex_cli as codex_cli

        which = mock.Mock(return_value=None)
        find_bundled = mock.Mock(return_value=r"C:\bundled\codex.exe")
        with mock.patch.object(codex_cli, "CODEX_CLI", "codex"), \
            mock.patch.object(codex_cli.shutil, "which", which), \
            mock.patch.object(codex_cli, "_find_bundled_codex", find_bundled):
            codex_cli._resolved_cli = None
            try:
                first = codex_cli.resolve_codex_cli()
                second = codex_cli.resolve_codex_cli()
                self.assertEqual(first, second)
                # A cached second call re-invokes neither resolver.
                self.assertEqual(1, which.call_count)
                self.assertEqual(1, find_bundled.call_count)
            finally:
                codex_cli._resolved_cli = None


class BuildCmdWrappingTests(unittest.TestCase):
    def test_non_exe_path_is_cmd_c_wrapped_on_win32(self):
        import tools.codex_cli as codex_cli

        with mock.patch.object(codex_cli, "resolve_codex_cli", return_value="codex"), \
            mock.patch.object(codex_cli.sys, "platform", "win32"):
            cmd = codex_cli._build_cmd("p", None, None)

        self.assertEqual(["cmd", "/c", "codex", "exec"], cmd[:4])

    def test_exe_path_is_not_wrapped_on_win32(self):
        import tools.codex_cli as codex_cli

        with mock.patch.object(
            codex_cli, "resolve_codex_cli", return_value=r"C:\x\codex.exe"
        ), mock.patch.object(codex_cli.sys, "platform", "win32"):
            cmd = codex_cli._build_cmd("p", None, None)

        self.assertNotEqual("cmd", cmd[0])
        self.assertEqual([r"C:\x\codex.exe", "exec"], cmd[:2])

    def test_exe_suffix_check_is_case_insensitive(self):
        import tools.codex_cli as codex_cli

        # WindowsApps ships codex.EXE; an uppercase suffix must not be wrapped.
        with mock.patch.object(
            codex_cli, "resolve_codex_cli", return_value=r"C:\x\codex.EXE"
        ), mock.patch.object(codex_cli.sys, "platform", "win32"):
            cmd = codex_cli._build_cmd("p", None, None)

        self.assertEqual(r"C:\x\codex.EXE", cmd[0])

    def test_non_exe_path_is_not_wrapped_off_win32(self):
        import tools.codex_cli as codex_cli

        with mock.patch.object(codex_cli, "resolve_codex_cli", return_value="codex"), \
            mock.patch.object(codex_cli.sys, "platform", "linux"):
            cmd = codex_cli._build_cmd("p", None, None)

        self.assertEqual(["codex", "exec"], cmd[:2])

    def test_cwd_and_resume_shape_the_argv(self):
        import tools.codex_cli as codex_cli

        with mock.patch.object(codex_cli, "resolve_codex_cli", return_value="codex.exe"):
            fresh = codex_cli._build_cmd("do it", r"C:\repo", None)
            resumed = codex_cli._build_cmd("do it", None, "thread-7")

        self.assertEqual(r"C:\repo", fresh[fresh.index("-C") + 1])
        self.assertEqual("do it", fresh[-1])
        self.assertNotIn("resume", fresh)

        self.assertEqual(["codex.exe", "exec", "resume"], resumed[:3])
        self.assertNotIn("-C", resumed)
        self.assertEqual(["thread-7", "do it"], resumed[-2:])


class RunCodexCliTests(unittest.TestCase):
    def test_file_not_found_yields_single_error_event_without_raising(self):
        import tools.codex_cli as codex_cli

        async def go():
            with mock.patch.object(
                codex_cli.asyncio,
                "create_subprocess_exec",
                AsyncMock(side_effect=FileNotFoundError()),
            ), mock.patch.object(
                codex_cli, "resolve_codex_cli", return_value=r"C:\x\codex.exe"
            ):
                return [ev async for ev in codex_cli.run_codex_cli("p")]

        events = asyncio.run(go())
        self.assertEqual(1, len(events))
        self.assertEqual("error", events[0]["type"])
        # The message embeds the resolved path via !r (repr doubles backslashes).
        self.assertIn(repr(r"C:\x\codex.exe"), events[0]["text"])
        self.assertIn("not found", events[0]["text"])

    def test_session_event_emitted_once_despite_multiple_thread_started(self):
        events, _ = _run_codex_cli_events([
            '{"type": "thread.started", "thread_id": "thread-1"}',
            '{"type": "thread.started", "thread_id": "thread-1"}',
            '{"type": "thread.started", "thread_id": "thread-2"}',
        ])
        session_events = [e for e in events if e["type"] == "session"]
        self.assertEqual(1, len(session_events))
        self.assertEqual("thread-1", session_events[0]["id"])

    def test_thread_started_without_thread_id_emits_no_session(self):
        events, _ = _run_codex_cli_events([
            '{"type": "thread.started"}',
            '{"type": "thread.started", "thread_id": "thread-9"}',
        ])
        self.assertEqual(["thread-9"], [e["id"] for e in events if e["type"] == "session"])

    def test_agent_message_item_yields_text(self):
        events, _ = _run_codex_cli_events([
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}}',
        ])
        self.assertEqual(
            [{"type": "text", "text": "hello"}], [e for e in events if e["type"] == "text"]
        )

    def test_empty_agent_message_text_emits_nothing(self):
        events, _ = _run_codex_cli_events([
            '{"type": "item.completed", "item": {"type": "agent_message", "text": ""}}',
        ])
        self.assertEqual([], events)

    def test_ignored_item_types_emit_nothing(self):
        events, _ = _run_codex_cli_events([
            '{"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}}',
            '{"type": "item.completed", "item": {"type": "todo_list", "items": []}}',
        ])
        self.assertEqual([], events)

    def test_other_item_types_yield_tool_events_carrying_the_item(self):
        events, _ = _run_codex_cli_events([
            '{"type": "item.completed", "item": {"type": "command_execution", '
            '"command": "pytest -q"}}',
        ])
        self.assertEqual(1, len(events))
        self.assertEqual("tool", events[0]["type"])
        self.assertEqual("command_execution", events[0]["name"])
        self.assertEqual({"type": "command_execution", "command": "pytest -q"}, events[0]["input"])

    def test_item_without_type_falls_back_to_generic_tool_name(self):
        events, _ = _run_codex_cli_events([
            '{"type": "item.completed", "item": {"detail": "no type field"}}',
        ])
        self.assertEqual("tool", events[0]["type"])
        self.assertEqual("tool", events[0]["name"])

    def test_error_event_types_take_message_then_error_then_fallback(self):
        events, _ = _run_codex_cli_events([
            '{"type": "thread.error", "message": "thread blew up"}',
            '{"type": "error", "error": "raw error field"}',
            '{"type": "turn.failed", "message": "turn blew up"}',
            '{"type": "error"}',
        ])
        self.assertEqual(
            ["thread blew up", "raw error field", "turn blew up", "Codex error"],
            [e["text"] for e in events if e["type"] == "error"],
        )

    def test_error_message_is_stringified(self):
        events, _ = _run_codex_cli_events([
            '{"type": "turn.failed", "error": {"code": 7}}',
        ])
        self.assertEqual("error", events[0]["type"])
        self.assertEqual(str({"code": 7}), events[0]["text"])

    def test_turn_completed_yields_result_with_last_agent_message_text(self):
        events, _ = _run_codex_cli_events([
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "first"}}',
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "second"}}',
            '{"type": "turn.completed", "usage": {"input_tokens": 10}}',
        ])
        results = [e for e in events if e["type"] == "result"]
        self.assertEqual([{"type": "result", "text": "second", "cost": None}], results)

    def test_turn_completed_without_any_text_yields_empty_result(self):
        events, _ = _run_codex_cli_events(['{"type": "turn.completed"}'])
        self.assertEqual([{"type": "result", "text": "", "cost": None}], events)

    def test_unknown_event_types_are_ignored(self):
        events, _ = _run_codex_cli_events([
            '{"type": "turn.started"}',
            '{"type": "item.started", "item": {"type": "agent_message"}}',
        ])
        self.assertEqual([], events)

    def test_invalid_json_line_is_skipped_and_stream_continues(self):
        events, _ = _run_codex_cli_events([
            "Reading additional input from stdin...",
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "after noise"}}',
        ])
        self.assertEqual(["after noise"], [e["text"] for e in events if e["type"] == "text"])

    def test_blank_lines_are_skipped(self):
        events, proc = _run_codex_cli_events([
            "",
            "   \n",
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "x"}}',
            "",
        ])
        self.assertEqual(["x"], [e["text"] for e in events if e["type"] == "text"])
        self.assertTrue(proc.waited)

    def test_nonzero_exit_without_events_yields_synthesized_error(self):
        events, _ = _run_codex_cli_events(["boom: not json"], returncode=2)
        self.assertEqual(1, len(events))
        self.assertEqual("error", events[0]["type"])
        self.assertIn("2", events[0]["text"])
        self.assertIn("no events", events[0]["text"])

    def test_nonzero_exit_after_a_parsed_event_adds_no_synthesized_error(self):
        events, _ = _run_codex_cli_events(
            ['{"type": "turn.completed"}'], returncode=2
        )
        self.assertEqual(["result"], [e["type"] for e in events])

    def test_clean_exit_without_events_yields_nothing(self):
        events, _ = _run_codex_cli_events([], returncode=0)
        self.assertEqual([], events)

    def test_cwd_is_passed_to_the_subprocess(self):
        import tools.codex_cli as codex_cli

        proc = _FakeProcess([])
        spawn = AsyncMock(return_value=proc)

        async def go():
            with mock.patch.object(codex_cli.asyncio, "create_subprocess_exec", spawn), \
                mock.patch.object(codex_cli, "resolve_codex_cli", return_value="codex.exe"):
                return [ev async for ev in codex_cli.run_codex_cli("p", cwd=r"C:\repo")]

        asyncio.run(go())
        self.assertEqual(r"C:\repo", spawn.call_args.kwargs["cwd"])
        # exec would otherwise block reading stdin until EOF.
        self.assertEqual(codex_cli.asyncio.subprocess.DEVNULL, spawn.call_args.kwargs["stdin"])


if __name__ == "__main__":
    unittest.main()

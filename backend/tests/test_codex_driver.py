"""Direct tests for tools/codex.py, the headless Claude Code CLI driver.

Mirrors the sibling pattern CodexCliResolverTests in test_traceability.py:
unittest.TestCase + unittest.mock, no real filesystem and no real subprocess.
The CLI-resolution/argv-building helpers are exercised with stubbed glob /
shutil.which / os.path, and run_codex's NDJSON parser is driven through a small
fake process whose async-iterable .stdout yields byte lines.

resolve_claude_cli caches its result in the module-level _resolved_cli; every
test that touches resolution resets it to None in a try/finally, mirroring
CodexCliResolverTests.
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
    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.waited = False

    async def wait(self):
        self.waited = True
        return 0


def _run_codex_events(lines, **kwargs):
    """Drive run_codex over a fake subprocess and collect its yielded events."""
    import tools.codex as codex

    proc = _FakeProcess([ln if isinstance(ln, bytes) else ln.encode() for ln in lines])

    async def go():
        with mock.patch.object(
            codex.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
        ), mock.patch.object(codex, "resolve_claude_cli", return_value="claude"):
            return [ev async for ev in codex.run_codex("prompt", **kwargs)], proc

    return asyncio.run(go())


class VersionKeyTests(unittest.TestCase):
    def test_sorts_numerically_not_lexicographically(self):
        import tools.codex as codex

        higher = codex._version_key(r"C:\x\claude-code\1.2.10\claude.exe")
        lower = codex._version_key(r"C:\x\claude-code\1.2.9\claude.exe")

        self.assertEqual((1, 2, 10), higher)
        self.assertGreater(higher, lower)

    def test_non_numeric_segment_falls_back_to_zero(self):
        import tools.codex as codex

        # A "dev" version directory must not raise; isdigit() is False -> 0.
        self.assertEqual((0,), codex._version_key(r"C:\x\claude-code\dev\claude.exe"))
        self.assertEqual(
            (1, 0, 0), codex._version_key(r"C:\x\claude-code\1.beta.0\claude.exe")
        )


class FindBundledClaudeTests(unittest.TestCase):
    def test_returns_none_when_localappdata_unset(self):
        import tools.codex as codex

        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("LOCALAPPDATA", None)
            self.assertIsNone(codex._find_bundled_claude())

    def test_returns_none_when_glob_matches_nothing(self):
        import tools.codex as codex

        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\dev\AppData\Local"}), \
            mock.patch.object(codex.glob, "glob", return_value=[]):
            self.assertIsNone(codex._find_bundled_claude())

    def test_returns_highest_versioned_match(self):
        import tools.codex as codex

        base = r"C:\Users\dev\AppData\Local\Packages\Claude_x\LocalCache\Roaming\Claude\claude-code"
        candidates = [
            base + r"\1.2.9\claude.exe",
            base + r"\1.2.10\claude.exe",
            base + r"\1.1.0\claude.exe",
        ]
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\dev\AppData\Local"}), \
            mock.patch.object(codex.glob, "glob", return_value=candidates), \
            mock.patch.object(codex.os.path, "isfile", return_value=True):
            self.assertEqual(base + r"\1.2.10\claude.exe", codex._find_bundled_claude())


class ResolveClaudeCliTests(unittest.TestCase):
    def test_prefers_explicit_absolute_cli_over_path_and_bundled(self):
        import tools.codex as codex

        explicit = r"C:\tools\claude\claude.exe"
        with mock.patch.object(codex, "CLAUDE_CLI", explicit), \
            mock.patch.object(codex.os.path, "isabs", return_value=True), \
            mock.patch.object(codex.os.path, "isfile", return_value=True), \
            mock.patch.object(codex.shutil, "which", return_value=r"C:\other\claude.exe"), \
            mock.patch.object(codex, "_find_bundled_claude", return_value=r"C:\bundled\claude.exe"):
            codex._resolved_cli = None
            try:
                self.assertEqual(explicit, codex.resolve_claude_cli())
            finally:
                codex._resolved_cli = None

    def test_falls_back_to_path_when_not_explicit_absolute(self):
        import tools.codex as codex

        on_path = r"C:\path\claude.exe"
        with mock.patch.object(codex, "CLAUDE_CLI", "claude"), \
            mock.patch.object(codex.shutil, "which", return_value=on_path), \
            mock.patch.object(codex, "_find_bundled_claude", return_value=r"C:\bundled\claude.exe"):
            codex._resolved_cli = None
            try:
                self.assertEqual(on_path, codex.resolve_claude_cli())
            finally:
                codex._resolved_cli = None

    def test_falls_back_to_bundled_when_not_on_path(self):
        import tools.codex as codex

        bundled = r"C:\bundled\claude.exe"
        with mock.patch.object(codex, "CLAUDE_CLI", "claude"), \
            mock.patch.object(codex.shutil, "which", return_value=None), \
            mock.patch.object(codex, "_find_bundled_claude", return_value=bundled):
            codex._resolved_cli = None
            try:
                self.assertEqual(bundled, codex.resolve_claude_cli())
            finally:
                codex._resolved_cli = None

    def test_caches_resolution_in_module_level_resolved_cli(self):
        import tools.codex as codex

        which = mock.Mock(return_value=None)
        find_bundled = mock.Mock(return_value=r"C:\bundled\claude.exe")
        with mock.patch.object(codex, "CLAUDE_CLI", "claude"), \
            mock.patch.object(codex.shutil, "which", which), \
            mock.patch.object(codex, "_find_bundled_claude", find_bundled):
            codex._resolved_cli = None
            try:
                first = codex.resolve_claude_cli()
                second = codex.resolve_claude_cli()
                self.assertEqual(first, second)
                # A cached second call re-invokes neither resolver.
                self.assertEqual(1, which.call_count)
                self.assertEqual(1, find_bundled.call_count)
            finally:
                codex._resolved_cli = None


class BuildCmdTests(unittest.TestCase):
    def test_includes_core_flags_and_configured_permission_mode(self):
        import tools.codex as codex

        with mock.patch.object(codex, "resolve_claude_cli", return_value="claude.exe"), \
            mock.patch.object(codex, "CLAUDE_PERMISSION_MODE", "acceptEdits"):
            cmd = codex._build_cmd("do the thing", None)

        self.assertIn("--print", cmd)
        self.assertIn("do the thing", cmd)
        self.assertEqual("stream-json", cmd[cmd.index("--output-format") + 1])
        self.assertIn("--verbose", cmd)
        self.assertIn("--include-partial-messages", cmd)
        self.assertEqual("acceptEdits", cmd[cmd.index("--permission-mode") + 1])

    def test_appends_resume_only_when_session_id_truthy(self):
        import tools.codex as codex

        with mock.patch.object(codex, "resolve_claude_cli", return_value="claude.exe"):
            no_resume = codex._build_cmd("p", None)
            resumed = codex._build_cmd("p", "session-42")

        self.assertNotIn("--resume", no_resume)
        self.assertIn("--resume", resumed)
        self.assertEqual("session-42", resumed[resumed.index("--resume") + 1])

    def test_empty_session_id_is_not_resumed(self):
        import tools.codex as codex

        with mock.patch.object(codex, "resolve_claude_cli", return_value="claude.exe"):
            self.assertNotIn("--resume", codex._build_cmd("p", ""))

    def test_non_exe_path_is_cmd_c_wrapped_on_win32(self):
        import tools.codex as codex

        with mock.patch.object(codex, "resolve_claude_cli", return_value="claude"), \
            mock.patch.object(codex.sys, "platform", "win32"):
            cmd = codex._build_cmd("p", None)

        self.assertEqual(["cmd", "/c"], cmd[:2])
        self.assertEqual("claude", cmd[2])

    def test_exe_path_is_not_wrapped_on_win32(self):
        import tools.codex as codex

        with mock.patch.object(codex, "resolve_claude_cli", return_value=r"C:\x\claude.exe"), \
            mock.patch.object(codex.sys, "platform", "win32"):
            cmd = codex._build_cmd("p", None)

        self.assertNotEqual("cmd", cmd[0])
        self.assertEqual(r"C:\x\claude.exe", cmd[0])


class ExtractTextTests(unittest.TestCase):
    def test_returns_text_for_text_delta(self):
        import tools.codex as codex

        self.assertEqual("hi", codex._extract_text({"type": "text_delta", "text": "hi"}))

    def test_returns_text_for_other_delta_shapes(self):
        import tools.codex as codex

        # Fallthrough branch: any other shape still returns text (or "").
        self.assertEqual("yo", codex._extract_text({"type": "input_json_delta", "text": "yo"}))
        self.assertEqual("", codex._extract_text({"type": "input_json_delta"}))


class RunCodexTests(unittest.TestCase):
    def test_file_not_found_yields_single_error_event_without_raising(self):
        import tools.codex as codex

        async def go():
            with mock.patch.object(
                codex.asyncio,
                "create_subprocess_exec",
                AsyncMock(side_effect=FileNotFoundError()),
            ), mock.patch.object(codex, "resolve_claude_cli", return_value=r"C:\x\claude.exe"):
                return [ev async for ev in codex.run_codex("p")]

        events = asyncio.run(go())
        self.assertEqual(1, len(events))
        self.assertEqual("error", events[0]["type"])
        # The message embeds the resolved path via !r (repr doubles backslashes).
        self.assertIn(repr(r"C:\x\claude.exe"), events[0]["text"])
        self.assertIn("not found", events[0]["text"])

    def test_session_event_emitted_once_despite_multiple_session_ids(self):
        events, _ = _run_codex_events([
            '{"session_id": "sess-1", "type": "system"}',
            '{"session_id": "sess-1", "type": "system"}',
            '{"session_id": "sess-2", "type": "system"}',
        ])
        session_events = [e for e in events if e["type"] == "session"]
        self.assertEqual(1, len(session_events))
        self.assertEqual("sess-1", session_events[0]["id"])

    def test_text_from_stream_event_content_block_delta(self):
        events, _ = _run_codex_events([
            '{"type": "stream_event", "event": {"type": "content_block_delta", '
            '"delta": {"type": "text_delta", "text": "streamed "}}}',
        ])
        texts = [e for e in events if e["type"] == "text"]
        self.assertEqual(["streamed "], [e["text"] for e in texts])

    def test_text_from_top_level_content_block_delta(self):
        events, _ = _run_codex_events([
            '{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "top-level"}}',
        ])
        texts = [e for e in events if e["type"] == "text"]
        self.assertEqual(["top-level"], [e["text"] for e in texts])

    def test_assistant_text_guarded_by_streamed_text_and_tool_always_emitted(self):
        # Streamed text precedes the assistant message: its text block is
        # suppressed, but the tool_use block is still emitted.
        events, _ = _run_codex_events([
            '{"type": "stream_event", "event": {"type": "content_block_delta", '
            '"delta": {"type": "text_delta", "text": "streamed "}}}',
            '{"type": "assistant", "message": {"content": ['
            '{"type": "text", "text": "batched"}, '
            '{"type": "tool_use", "name": "Edit", "input": {"path": "a.py"}}]}}',
        ])
        text_events = [e for e in events if e["type"] == "text"]
        self.assertEqual(["streamed "], [e["text"] for e in text_events])  # not "batched"
        tool_events = [e for e in events if e["type"] == "tool"]
        self.assertEqual(1, len(tool_events))
        self.assertEqual("Edit", tool_events[0]["name"])
        self.assertEqual({"path": "a.py"}, tool_events[0]["input"])

    def test_assistant_text_emitted_when_no_streamed_text(self):
        events, _ = _run_codex_events([
            '{"type": "assistant", "message": {"content": ['
            '{"type": "text", "text": "just batched"}]}}',
        ])
        self.assertEqual(["just batched"], [e["text"] for e in events if e["type"] == "text"])

    def test_streamed_text_bookkeeping_resets_across_assistant_messages(self):
        # First assistant message has streamed text (text block suppressed);
        # the flag resets so the SECOND assistant message's text block emits.
        events, _ = _run_codex_events([
            '{"type": "stream_event", "event": {"type": "content_block_delta", '
            '"delta": {"type": "text_delta", "text": "s1 "}}}',
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}',
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "second"}]}}',
        ])
        self.assertEqual(["s1 ", "second"], [e["text"] for e in events if e["type"] == "text"])

    def test_result_success_yields_result_event_with_cost(self):
        events, _ = _run_codex_events([
            '{"type": "result", "subtype": "success", "is_error": false, '
            '"result": "all done", "total_cost_usd": 0.0123}',
        ])
        result_events = [e for e in events if e["type"] == "result"]
        self.assertEqual(1, len(result_events))
        self.assertEqual("all done", result_events[0]["text"])
        self.assertEqual(0.0123, result_events[0]["cost"])
        self.assertFalse([e for e in events if e["type"] == "error"])

    def test_result_is_error_truthy_yields_error(self):
        events, _ = _run_codex_events([
            '{"type": "result", "subtype": "success", "is_error": true, "result": "boom"}',
        ])
        self.assertEqual("error", events[-1]["type"])
        self.assertEqual("boom", events[-1]["text"])

    def test_result_non_success_subtype_yields_error_even_when_is_error_falsy(self):
        events, _ = _run_codex_events([
            '{"type": "result", "subtype": "error_max_turns", "result": "hit limit"}',
        ])
        self.assertEqual("error", events[-1]["type"])
        self.assertEqual("hit limit", events[-1]["text"])

    def test_invalid_json_line_is_skipped_and_stream_continues(self):
        events, _ = _run_codex_events([
            "not json at all",
            '{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "after noise"}}',
        ])
        self.assertEqual(["after noise"], [e["text"] for e in events if e["type"] == "text"])

    def test_blank_lines_are_skipped(self):
        events, proc = _run_codex_events([
            "",
            "   ",
            '{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}}',
            "",
        ])
        self.assertEqual(["x"], [e["text"] for e in events if e["type"] == "text"])
        self.assertTrue(proc.waited)


if __name__ == "__main__":
    unittest.main()

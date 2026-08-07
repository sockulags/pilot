"""Direct tests for codex_logs.py, the Codex session-log parser.

The sibling of CodexLogTests in test_traceability.py, which pins the two happy
paths (a log with shell + mcp tool calls, and a log with only a prompt and a
final answer). This file covers what that class does not: the root-resolution
helpers, the summarize_codex_session entry point, and every error and fallback
branch of the row-classification loop -- mcp-call dedup, the error-summary
precedence chain, the execution-error fallback, the filename-derived session
id, and the _iter_jsonl / _compact / _session_id_from_filename helpers.

Same technique as CodexLogTests: unittest.TestCase over real .jsonl files
written into a tempfile.TemporaryDirectory, so the parser reads through its
own I/O path. Only default_codex_sessions_root is faked (unittest.mock), so
that summarize_codex_session -- which takes no roots argument -- resolves into
the temp directory instead of the developer's real ~/.codex/sessions.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

SESSION_ID = "019ecb37-bc91-73a0-9e35-0f5c1df85141"
LOG_NAME = f"rollout-2026-06-15T14-18-08-{SESSION_ID}.jsonl"


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return path


def _event(payload: dict) -> dict:
    return {"type": "event_msg", "payload": payload}


def _item(payload: dict) -> dict:
    return {"type": "response_item", "payload": payload}


def _summarize(rows: list[dict], name: str = LOG_NAME) -> dict:
    """Write rows to a throwaway log and return its parsed summary."""
    from codex_logs import summarize_codex_log

    with tempfile.TemporaryDirectory() as tmp:
        return summarize_codex_log(_write_jsonl(Path(tmp) / name, rows))


class CodexLogRootResolutionTests(unittest.TestCase):
    def test_default_sessions_root_is_the_codex_sessions_dir_under_home(self):
        from codex_logs import default_codex_sessions_root

        self.assertEqual(Path.home() / ".codex" / "sessions", default_codex_sessions_root())

    def test_find_codex_log_skips_a_root_that_does_not_exist(self):
        from codex_logs import find_codex_log

        with tempfile.TemporaryDirectory() as tmp:
            present = Path(tmp) / "present"
            (present / "2026" / "06").mkdir(parents=True)
            log_path = _write_jsonl(present / "2026" / "06" / LOG_NAME, [])
            missing = Path(tmp) / "no-such-root"

            self.assertFalse(missing.exists())
            self.assertEqual(log_path, find_codex_log(SESSION_ID, roots=[missing, present]))


class SummarizeCodexSessionTests(unittest.TestCase):
    """The public entry point that combines find_codex_log + summarize_codex_log."""

    def test_returns_none_without_a_session_id_or_a_matching_log(self):
        import codex_logs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_jsonl(root / LOG_NAME, [])
            with mock.patch.object(codex_logs, "default_codex_sessions_root", return_value=root):
                self.assertIsNone(codex_logs.summarize_codex_session(None))
                self.assertIsNone(codex_logs.summarize_codex_session("unknown-id"))

    def test_matching_log_yields_the_same_summary_as_the_two_step_lookup(self):
        import codex_logs

        rows = [
            {"type": "session_meta", "payload": {"id": SESSION_ID, "originator": "codex_exec"}},
            _event({"type": "user_message", "message": "Reply PONG"}),
            _event({"type": "agent_message", "phase": "final_answer", "message": "PONG"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = _write_jsonl(root / LOG_NAME, rows)
            with mock.patch.object(codex_logs, "default_codex_sessions_root", return_value=root):
                summary = codex_logs.summarize_codex_session(SESSION_ID)

            self.assertEqual(codex_logs.summarize_codex_log(log_path), summary)
            self.assertEqual(SESSION_ID, summary["codex_session_id"])
            self.assertEqual(str(log_path), summary["codex_log_path"])
            self.assertEqual("PONG", summary["codex_final_summary"])


class McpCallCountTests(unittest.TestCase):
    """mcp_tool_call_end only counts an invocation no function_call recorded."""

    def test_counts_an_invocation_whose_tool_was_not_already_recorded(self):
        summary = _summarize(
            [
                _item({"type": "function_call", "name": "shell_command", "arguments": "{}"}),
                _event({"type": "mcp_tool_call_end", "invocation": {"server": "docs", "tool": "fetch"}}),
            ]
        )

        self.assertEqual(1, summary["codex_tool_call_count"])
        self.assertEqual(1, summary["codex_shell_call_count"])
        self.assertEqual(1, summary["codex_mcp_call_count"])

    def test_does_not_double_count_an_invocation_of_a_recorded_function_call(self):
        summary = _summarize(
            [
                _item(
                    {
                        "type": "function_call",
                        "namespace": "mcp__codex_apps__docs",
                        "name": "fetch",
                        "arguments": "{}",
                    }
                ),
                _event({"type": "mcp_tool_call_end", "invocation": {"server": "docs", "tool": "fetch"}}),
            ]
        )

        self.assertEqual(1, summary["codex_tool_call_count"])
        self.assertEqual(1, summary["codex_mcp_call_count"])


class ErrorSummaryTests(unittest.TestCase):
    def test_error_rows_prefer_message_then_error_then_the_type_itself(self):
        for payload_type in ("error", "thread.error", "turn.failed"):
            with self.subTest(payload_type=payload_type, source="message"):
                summary = _summarize(
                    [_event({"type": payload_type, "message": "from message", "error": "from error"})]
                )
                self.assertEqual("from message", summary["codex_error_summary"])

            with self.subTest(payload_type=payload_type, source="error"):
                summary = _summarize([_event({"type": payload_type, "error": "from error"})])
                self.assertEqual("from error", summary["codex_error_summary"])

            with self.subTest(payload_type=payload_type, source="type"):
                summary = _summarize([_event({"type": payload_type})])
                self.assertEqual(payload_type, summary["codex_error_summary"])

    def test_execution_error_output_becomes_the_error_summary(self):
        from codex_logs import MAX_SUMMARY_CHARS

        output = "Execution Error: exit code 1\n" + "x" * (MAX_SUMMARY_CHARS * 2)
        summary = _summarize([_item({"type": "function_call_output", "output": output})])

        self.assertEqual(output[:MAX_SUMMARY_CHARS], summary["codex_error_summary"])
        self.assertEqual(MAX_SUMMARY_CHARS, len(summary["codex_error_summary"]))

    def test_clean_tool_output_leaves_the_error_summary_empty(self):
        summary = _summarize([_item({"type": "function_call_output", "output": "ok, 3 files changed"})])

        self.assertEqual("", summary["codex_error_summary"])

    def test_execution_error_output_does_not_overwrite_an_earlier_error(self):
        summary = _summarize(
            [
                _event({"type": "turn.failed", "message": "the explicit failure"}),
                _item({"type": "function_call_output", "output": "execution error: the later one"}),
            ]
        )

        self.assertEqual("the explicit failure", summary["codex_error_summary"])


class SessionIdFallbackTests(unittest.TestCase):
    def test_log_without_session_meta_derives_the_id_from_the_filename(self):
        summary = _summarize([_event({"type": "user_message", "message": "Reply PONG"})])

        self.assertEqual(SESSION_ID, summary["codex_session_id"])

    def test_session_id_from_filename_returns_the_uuid_tail_of_a_codex_stem(self):
        from codex_logs import _session_id_from_filename

        self.assertEqual(SESSION_ID, _session_id_from_filename(Path(LOG_NAME)))

    def test_session_id_from_filename_falls_back_to_the_bare_stem(self):
        from codex_logs import _session_id_from_filename

        self.assertEqual("rollout-plain", _session_id_from_filename(Path("rollout-plain.jsonl")))


class IterJsonlTests(unittest.TestCase):
    def test_blank_and_malformed_lines_are_skipped_without_raising(self):
        from codex_logs import _iter_jsonl

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / LOG_NAME
            log_path.write_text(
                "\n".join(
                    [
                        json.dumps({"row": "before"}),
                        "",
                        "   ",
                        '{"row": "truncated"',
                        json.dumps({"row": "after"}),
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual([{"row": "before"}, {"row": "after"}], list(_iter_jsonl(log_path)))

    def test_a_corrupt_row_does_not_lose_the_surrounding_summary(self):
        from codex_logs import summarize_codex_log

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / LOG_NAME
            log_path.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"id": SESSION_ID}}),
                        json.dumps(_event({"type": "user_message", "message": "Reply PONG"})),
                        '{"type": "event_msg", "payload": {"type": "agent_mess',
                        "",
                        json.dumps(
                            _event(
                                {
                                    "type": "agent_message",
                                    "phase": "final_answer",
                                    "message": "PONG",
                                }
                            )
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            summary = summarize_codex_log(log_path)

        self.assertEqual(SESSION_ID, summary["codex_session_id"])
        self.assertEqual("Reply PONG", summary["codex_prompt"])
        self.assertEqual("PONG", summary["codex_final_summary"])


class CompactTests(unittest.TestCase):
    def test_none_becomes_an_empty_string(self):
        from codex_logs import _compact

        self.assertEqual("", _compact(None))

    def test_a_string_is_passed_through_and_truncated(self):
        from codex_logs import MAX_SUMMARY_CHARS, _compact

        self.assertEqual("a string", _compact("a string"))
        self.assertEqual("x" * MAX_SUMMARY_CHARS, _compact("x" * (MAX_SUMMARY_CHARS * 2)))

    def test_an_object_is_json_serialized_and_truncated(self):
        from codex_logs import MAX_SUMMARY_CHARS, _compact

        self.assertEqual('{"a": 1}', _compact({"a": 1}))

        oversized = {"cmd": "y" * (MAX_SUMMARY_CHARS * 2)}
        compacted = _compact(oversized)
        self.assertEqual(MAX_SUMMARY_CHARS, len(compacted))
        self.assertEqual(json.dumps(oversized, ensure_ascii=False)[:MAX_SUMMARY_CHARS], compacted)


if __name__ == "__main__":
    unittest.main()

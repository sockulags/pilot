import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class CodexLogTests(unittest.TestCase):
    def test_resolves_and_summarizes_codex_exec_log(self):
        from codex_logs import find_codex_log, summarize_codex_log

        session_id = "019ecb37-bc91-73a0-9e35-0f5c1df85141"
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "2026" / "06" / "15"
            log_dir.mkdir(parents=True)
            log_path = log_dir / f"rollout-2026-06-15T14-18-08-{session_id}.jsonl"
            self._write_jsonl(
                log_path,
                [
                    {
                        "type": "session_meta",
                        "payload": {"id": session_id, "cwd": r"C:\repo", "originator": "codex_exec"},
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "Please re-run the query to fetch a list of open issues.",
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "shell_command",
                            "arguments": "{\"command\":\"git remote -v\"}",
                            "call_id": "call_shell",
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "namespace": "mcp__codex_apps__github",
                            "name": "_list_repositories",
                            "arguments": "{\"page_size\":100}",
                            "call_id": "call_github",
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "agent_message",
                            "phase": "final_answer",
                            "message": "I re-ran the Codex/GitHub connector query.",
                        },
                    },
                ],
            )

            self.assertEqual(log_path, find_codex_log(session_id, roots=[Path(tmp)]))
            summary = summarize_codex_log(log_path)

        self.assertEqual(session_id, summary["codex_session_id"])
        self.assertEqual("Please re-run the query to fetch a list of open issues.", summary["codex_prompt"])
        self.assertEqual(2, summary["codex_tool_call_count"])
        self.assertEqual(1, summary["codex_shell_call_count"])
        self.assertEqual(1, summary["codex_mcp_call_count"])
        self.assertIn("GitHub connector query", summary["codex_final_summary"])

    def test_codex_log_without_tools_still_counts_as_codex_evidence(self):
        from codex_logs import summarize_codex_log

        session_id = "019ec97d-1bf2-7991-b6ba-981dbf21615b"
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / f"rollout-2026-06-15T06-14-40-{session_id}.jsonl"
            self._write_jsonl(
                log_path,
                [
                    {"type": "session_meta", "payload": {"id": session_id, "originator": "codex_exec"}},
                    {"type": "event_msg", "payload": {"type": "user_message", "message": "Reply PONG"}},
                    {
                        "type": "event_msg",
                        "payload": {"type": "agent_message", "phase": "final_answer", "message": "PONG"},
                    },
                ],
            )

            summary = summarize_codex_log(log_path)

        self.assertEqual(0, summary["codex_tool_call_count"])
        self.assertEqual("Reply PONG", summary["codex_prompt"])
        self.assertEqual("PONG", summary["codex_final_summary"])

    def test_missing_codex_session_id_has_no_log(self):
        from codex_logs import find_codex_log

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_codex_log(None, roots=[Path(tmp)]))
            self.assertIsNone(find_codex_log("", roots=[Path(tmp)]))
            self.assertIsNone(find_codex_log("missing-session-id", roots=[Path(tmp)]))

    def _write_jsonl(self, path: Path, rows: list[dict]):
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


class RoutingAndCwdTests(unittest.TestCase):
    def test_project_github_request_is_forced_to_code_route(self):
        from agents.orchestrator import route_project_bound_message

        decision = route_project_bound_message(
            "Kolla Github med gh kommandot hur många öppna issues som finns där.",
            project="CV_Builder",
        )

        self.assertEqual("code", decision["route"])
        self.assertIn("gh", decision["prompt"])

    def test_agent_loop_injects_project_cwd_into_run_command(self):
        asyncio.run(self._agent_loop_injects_project_cwd_into_run_command())

    def test_project_cwd_is_applied_to_file_tool_defaults(self):
        from agents.loop import apply_project_cwd_to_args

        cwd = r"C:\Users\lucas\Code\CV_Builder"

        self.assertEqual({"path": cwd}, apply_project_cwd_to_args("list_dir", {}, cwd))
        self.assertEqual({"root": cwd, "name": "README.md"}, apply_project_cwd_to_args("find_file", {"name": "README.md"}, cwd))
        self.assertEqual(
            {"path": r"C:\Users\lucas\Code\CV_Builder\README.md"},
            apply_project_cwd_to_args("read_file", {"path": "README.md"}, cwd),
        )

    async def _agent_loop_injects_project_cwd_into_run_command(self):
        from agents import loop

        events: list[dict] = []
        decisions = [
            {"tool": "run_command", "args": {"cmd": "gh issue list"}, "thinking": "use gh"},
            {"tool": "done", "args": {"summary": "done"}, "thinking": "done"},
        ]
        seen_cwd: list[str | None] = []

        async def fake_route_next_action(*args, **kwargs):
            return decisions.pop(0)

        async def fake_run_command_async(cmd, cwd=None):
            seen_cwd.append(cwd)
            yield "no issues\n"

        originals = (loop.route_next_action, loop.run_command_async, loop.PERCEPTION_ENABLED)
        try:
            loop.route_next_action = fake_route_next_action
            loop.run_command_async = fake_run_command_async
            loop.PERCEPTION_ENABLED = False

            await loop.run_agent_loop(
                "Kolla Github med gh",
                events.append,
                asyncio.Event(),
                project_cwd=r"C:\Users\lucas\Code\CV_Builder",
            )
        finally:
            loop.route_next_action, loop.run_command_async, loop.PERCEPTION_ENABLED = originals

        action = next(event for event in events if event["type"] == "action")
        self.assertEqual(r"C:\Users\lucas\Code\CV_Builder", action["args"]["cwd"])
        self.assertEqual([r"C:\Users\lucas\Code\CV_Builder"], seen_cwd)


class WebSocketCodeTurnTests(unittest.TestCase):
    def test_code_turn_persists_and_emits_codex_trace(self):
        asyncio.run(self._code_turn_persists_and_emits_codex_trace())

    def test_code_turn_reports_runner_start_failure(self):
        asyncio.run(self._code_turn_reports_runner_start_failure())

    async def _code_turn_persists_and_emits_codex_trace(self):
        from api.ws import _run_code_turn

        async def fake_runner(prompt, cwd=None, resume_session_id=None):
            yield {"type": "session", "id": "codex-session-1"}
            yield {"type": "text", "text": "answer"}
            yield {"type": "result", "text": "answer"}

        trace = {
            "codex_session_id": "codex-session-1",
            "codex_prompt": "prompt",
            "codex_tool_call_count": 0,
        }
        events: list[dict] = []
        conversation: list[dict] = []

        session_id = await _run_code_turn(
            fake_runner,
            "prompt",
            r"C:\repo",
            None,
            events.append,
            asyncio.Event(),
            conversation,
            trace_provider=lambda sid: trace,
        )

        self.assertEqual("codex-session-1", session_id)
        self.assertEqual(trace, conversation[-1]["codex_trace"])
        self.assertTrue(any(event["type"] == "codex_trace" and event["trace"] == trace for event in events))

    async def _code_turn_reports_runner_start_failure(self):
        from api.ws import _run_code_turn

        async def failing_runner(prompt, cwd=None, resume_session_id=None):
            raise PermissionError(5, "Access is denied")
            yield

        events: list[dict] = []
        conversation: list[dict] = []

        session_id = await _run_code_turn(
            failing_runner,
            "prompt",
            r"C:\repo",
            None,
            events.append,
            asyncio.Event(),
            conversation,
        )

        self.assertIsNone(session_id)
        self.assertIn("Access is denied", conversation[-1]["content"])
        self.assertTrue(any(event["type"] == "error" and "Access is denied" in event["content"] for event in events))
        self.assertTrue(any(event["type"] == "done" for event in events))


class CodexCliResolverTests(unittest.TestCase):
    def test_resolve_prefers_bundled_codex_over_windowsapps_path(self):
        import tools.codex_cli as codex_cli

        bundled = r"C:\Users\lucas\AppData\Local\OpenAI\Codex\bin\hash\codex.exe"
        windowsapps = r"C:\Program Files\WindowsApps\OpenAI.Codex_hash\app\resources\codex.EXE"

        with mock.patch.object(codex_cli, "CODEX_CLI", "codex"), \
            mock.patch.object(codex_cli, "_find_bundled_codex", return_value=bundled), \
            mock.patch.object(codex_cli.shutil, "which", return_value=windowsapps):
            codex_cli._resolved_cli = None
            try:
                self.assertEqual(bundled, codex_cli.resolve_codex_cli())
            finally:
                codex_cli._resolved_cli = None

    def test_codex_exec_always_uses_danger_full_access_sandbox(self):
        import tools.codex_cli as codex_cli

        with mock.patch.object(codex_cli, "resolve_codex_cli", return_value="codex.exe"):
            fresh_cmd = codex_cli._build_cmd("prompt", r"C:\repo", None)
            resume_cmd = codex_cli._build_cmd("prompt", r"C:\repo", "session-1")

        self.assertIn("--sandbox", fresh_cmd)
        self.assertEqual("danger-full-access", fresh_cmd[fresh_cmd.index("--sandbox") + 1])
        self.assertIn("--sandbox", resume_cmd)
        self.assertEqual("danger-full-access", resume_cmd[resume_cmd.index("--sandbox") + 1])

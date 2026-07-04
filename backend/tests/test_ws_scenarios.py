import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class WebSocketScenarioTests(unittest.TestCase):
    def test_current_llm_research_scenario_records_sources_and_final_shape(self):
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState
        from agents.task_contracts import build_task_contract

        state = RuntimeState()
        state.record_tool_result(
            "web_research",
            {"query": "RTX 5060 Ti 16GB local LLM current", "min_sources": 3},
            "\n".join(
                [
                    "Research results for 'RTX 5060 Ti 16GB local LLM current':",
                    "Sources fetched: 3",
                    "1. Review",
                    "   https://example.com/rtx-review",
                    "2. Bench",
                    "   https://example.com/rtx-bench",
                    "3. Model fit",
                    "   https://example.com/llm-fit",
                ]
            ),
            ok=True,
        )
        contract = build_task_contract("research")
        state.set_contract_result(contract, contract.evaluate(state.evidence_items))

        result = self._run_scenario(
            session_id="scenario-research",
            user_text="Vilken aktuell lokal LLM passar bäst för RTX 5060 Ti 16GB?",
            outcome=LoopOutcome(
                "done",
                "- web_research({'query': 'RTX 5060 Ti 16GB local LLM current'}) -> Research results...",
                runtime_state=state,
            ),
            emitted_tools=[("web_research", {"query": "RTX 5060 Ti 16GB local LLM current"})],
            reply=(
                "För RTX 5060 Ti 16GB är en kvantiserad modell rimlig. "
                "Källor: https://example.com/rtx-review och https://example.com/llm-fit."
            ),
        )

        self.assert_final_answer_shape(result["assistant"])
        self.assertNotIn("Research results for", result["assistant"])
        self.assertIn("https://example.com/rtx-review", result["assistant"])
        meta = result["meta"]
        self.assertIn("web_research", meta["tools"])
        self.assertEqual("research", meta["requirements"]["intent"])
        self.assertTrue(meta["requirements"]["satisfied"])
        self.assertEqual(3, meta["runtime_state"]["sources"][0]["sources_fetched"])

    def test_local_model_audit_scenario_records_verified_markdown_artifact(self):
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState
        from agents.task_contracts import build_task_contract

        report_path = r"C:\repo\local_model_audit_report.md"
        state = RuntimeState()
        for tool, args, text, verified in (
            (
                "run_command",
                {"cmd": "ollama list"},
                "Command: ollama list\nOutput:\ngemma4:12b\nqwen3.5:9b",
                False,
            ),
            (
                "read_file",
                {"path": "backend/config.py"},
                "File: backend/config.py\nContent:\nOLLAMA_MODEL = 'gemma4:12b'",
                False,
            ),
            (
                "read_file",
                {"path": "README.md"},
                "File: README.md\nContent:\n`OLLAMA_MODEL` `gemma4:12b`",
                False,
            ),
            (
                "run_command",
                {"cmd": f"Set-Content -LiteralPath '{report_path}' -Value 'ok'"},
                f"Command: Set-Content -LiteralPath '{report_path}' -Value 'ok'\nOutput:\n",
                False,
            ),
            (
                "run_command",
                {"cmd": f"Test-Path -LiteralPath '{report_path}'"},
                f"Command: Test-Path -LiteralPath '{report_path}'\nOutput:\nTrue",
                True,
            ),
        ):
            state.record_tool_result(tool, args, text, ok=True, artifact_verified=verified)
        contract = build_task_contract("local_model_audit_report")
        state.set_contract_result(contract, contract.evaluate(state.evidence_items))

        result = self._run_scenario(
            session_id="scenario-audit",
            user_text="Skapa en local model audit report som markdown",
            outcome=LoopOutcome(
                "done",
                f"- run_command(...) -> Verified artifact: {report_path}",
                runtime_state=state,
            ),
            emitted_tools=[
                ("run_command", {"cmd": "ollama list"}),
                ("read_file", {"path": "backend/config.py"}),
                ("read_file", {"path": "README.md"}),
                ("run_command", {"cmd": f"Set-Content -LiteralPath '{report_path}'"}),
                ("run_command", {"cmd": f"Test-Path -LiteralPath '{report_path}'"}),
            ],
            reply="Rapporten är skapad och verifierad.",
        )

        self.assert_final_answer_shape(result["assistant"])
        self.assertIn(report_path, result["assistant"])
        meta = result["meta"]
        self.assertIn("run_command", meta["tools"])
        self.assertIn("read_file", meta["tools"])
        self.assertTrue(meta["verified"])
        self.assertEqual([{"path": report_path, "verified": True}], meta["artifacts"])
        self.assertEqual("local_model_audit_report", meta["requirements"]["intent"])
        self.assertTrue(meta["requirements"]["satisfied"])

    def test_backend_flow_analysis_scenario_records_required_files_and_answer_shape(self):
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState
        from agents.task_contracts import build_task_contract

        required_files = (
            "backend/api/ws.py",
            "backend/agents/orchestrator.py",
            "backend/agents/coordinator.py",
            "backend/agents/loop.py",
            "backend/store.py",
            "backend/tools/registry.py",
        )
        state = RuntimeState()
        for path in required_files:
            state.record_tool_result(
                "read_file",
                {"path": path},
                f"File: {path}\nContent:\n...",
                ok=True,
            )
        contract = build_task_contract("project_analysis")
        state.set_contract_result(contract, contract.evaluate(state.evidence_items))

        result = self._run_scenario(
            session_id="scenario-flow",
            user_text=(
                "Förklara det här projektets backendflöde från WebSocket till "
                "tool-call och sparad session"
            ),
            outcome=LoopOutcome(
                "done",
                "- read_file({'path': 'backend/api/ws.py'}) -> websocket_endpoint",
                runtime_state=state,
            ),
            emitted_tools=[("list_dir", {"path": "."}), *[("read_file", {"path": path}) for path in required_files]],
            reply=(
                "WebSocket-flödet börjar i backend/api/ws.py, går via orchestrator "
                "och coordinator, och sessionen sparas genom store.py."
            ),
        )

        self.assert_final_answer_shape(result["assistant"])
        self.assertIn("backend/api/ws.py", result["assistant"])
        meta = result["meta"]
        self.assertIn("list_dir", meta["tools"])
        self.assertIn("read_file", meta["tools"])
        self.assertEqual("project_analysis", meta["requirements"]["intent"])
        self.assertTrue(meta["requirements"]["satisfied"])
        self.assertEqual(list(required_files), meta["runtime_state"]["files_read"])

    def test_empty_tool_backed_reply_falls_back_to_evidence_not_klar(self):
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState

        state = RuntimeState()
        state.record_tool_result(
            "read_file",
            {"path": "backend/api/ws.py"},
            "File: backend/api/ws.py\nContent:\nwebsocket_endpoint",
            ok=True,
        )

        result = self._run_scenario(
            session_id="scenario-empty-reply",
            user_text="Förklara WebSocket-flödet",
            outcome=LoopOutcome(
                "done",
                "- read_file({'path': 'backend/api/ws.py'}) -> websocket_endpoint",
                runtime_state=state,
            ),
            emitted_tools=[("read_file", {"path": "backend/api/ws.py"})],
            reply="",
            require_assistant_delta=False,
        )

        saved_answer = result["saved"]["messages"][-1]["content"]
        self.assertTrue(saved_answer.strip())
        self.assertNotIn(saved_answer.strip().lower(), {"klar", "klart", "klart."})
        self.assertIn("read_file", saved_answer)
        self.assertIn("backend/api/ws.py", saved_answer)

    def test_raw_research_log_reply_is_replaced_with_source_evidence_summary(self):
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState

        state = RuntimeState()
        state.record_tool_result(
            "web_research",
            {"query": "RTX 5060 Ti 16GB local LLM current", "min_sources": 2},
            "\n".join(
                [
                    "Research results for 'RTX 5060 Ti 16GB local LLM current':",
                    "Sources fetched: 2",
                    "1. Review",
                    "   https://example.com/rtx-review",
                    "2. Bench",
                    "   https://example.com/rtx-bench",
                ]
            ),
            ok=True,
        )

        result = self._run_scenario(
            session_id="scenario-raw-log",
            user_text="Research aktuell lokal LLM för RTX 5060 Ti 16GB",
            outcome=LoopOutcome(
                "done",
                "- web_research({'query': 'RTX 5060 Ti 16GB local LLM current'}) -> Research results...",
                runtime_state=state,
            ),
            emitted_tools=[("web_research", {"query": "RTX 5060 Ti 16GB local LLM current"})],
            reply="web_research({'query': 'x'}) -> Research results for 'x'\nSources fetched: 2",
        )

        self.assertNotIn("web_research(", result["assistant"])
        self.assertNotIn("Research results for", result["assistant"])
        self.assertIn("https://example.com/rtx-review", result["assistant"])

    def test_unverified_file_claim_is_replaced_by_verified_fallback_artifact(self):
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState

        state = RuntimeState()
        state.record_tool_result(
            "run_command",
            {"cmd": "Set-Content -Path unverified.md -Value 'ok'"},
            "Command: Set-Content -Path unverified.md -Value 'ok'\nOutput:\n",
            ok=True,
        )

        result = self._run_scenario(
            session_id="scenario-false-file",
            user_text="Skapa en markdownrapport",
            outcome=LoopOutcome(
                "done",
                "- run_command({'cmd': 'Set-Content -Path unverified.md -Value ok'}) -> Output:",
                runtime_state=state,
            ),
            emitted_tools=[("run_command", {"cmd": "Set-Content -Path unverified.md -Value 'ok'"})],
            reply="Rapporten är klar: unverified.md",
        )

        self.assertNotIn("unverified.md", result["assistant"])
        self.assertTrue(result["meta"]["verified"])
        self.assertTrue(result["meta"]["artifacts"])
        self.assertTrue(any(artifact["verified"] for artifact in result["meta"]["artifacts"]))

    def test_confirmation_required_turn_persists_audit_metadata(self):
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState

        state = RuntimeState()
        state.record_confirmation_required(
            "run_command",
            {"cmd": "Remove-Item -Recurse .\\data"},
            "Bekräftelse krävs innan jag kör run_command.",
        )

        result = self._run_scenario(
            session_id="scenario-confirmation",
            user_text="Ta bort data-mappen",
            outcome=LoopOutcome(
                "needs_input",
                "- confirmation_required: run_command",
                "Bekräftelse krävs innan jag kör run_command.",
                runtime_state=state,
            ),
            emitted_tools=[(
                "confirmation_required",
                {"cmd": "Remove-Item -Recurse .\\data"},
            )],
            reply="",
        )

        self.assertIn("Bekräftelse krävs", result["saved"]["messages"][-1]["content"])
        self.assertEqual("needs_input", result["meta"]["status"])
        self.assertEqual("confirmation_required", result["meta"]["runtime_state"]["actions"][0]["decision"])
        self.assertEqual("high", result["meta"]["runtime_state"]["actions"][0]["risk_level"])

    def test_turn_exceeding_watchdog_timeout_is_terminated_and_client_notified(self):
        import asyncio
        import threading

        import api.ws as ws_api
        import store

        app = FastAPI()

        @app.websocket("/ws")
        async def ws(websocket: WebSocket):
            await ws_api.websocket_endpoint(websocket)

        # threading.Event: the ASGI app runs in the TestClient's own loop/thread,
        # so the flag is observed from the test thread after the socket closes.
        aborted = threading.Event()

        async def fake_classify_turn(prior, text, project=None, model_mode="auto"):
            return {"route": "computer", "task": text, "thinking": "watchdog test", "model": "gemma4:12b"}

        async def slow_run_coordinator(task, emit, abort, *args, **kwargs):
            # Simulate a hung Ollama call / stalled agent: sleep well past the
            # (patched, tiny) turn timeout. Record when the watchdog aborts us.
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                aborted.set()
                raise
            return None

        async def fake_search_memories(text, *args, **kwargs):
            return []

        async def fake_get_model_inventory():
            from agents.model_inventory import ModelInventory

            return ModelInventory()

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(store, "SESSIONS_DIR", tmp), \
             mock.patch.object(ws_api, "WS_TURN_TIMEOUT_SECONDS", 0.3), \
             mock.patch.object(ws_api, "list_projects", return_value=[]), \
             mock.patch.object(ws_api, "list_jobs", return_value=[]), \
             mock.patch.object(ws_api, "append_turn_diagnostic"), \
             mock.patch.object(ws_api, "classify_turn", new=fake_classify_turn), \
             mock.patch.object(ws_api, "get_model_inventory", new=fake_get_model_inventory), \
             mock.patch.object(ws_api, "run_coordinator", new=slow_run_coordinator), \
             mock.patch.object(ws_api, "search_memories", new=fake_search_memories):
            with TestClient(app) as client:
                with client.websocket_connect("/ws") as websocket:
                    websocket.send_json({"type": "hello", "session_id": "scenario-watchdog"})
                    self._drain_until(websocket, {"jobs"})
                    websocket.send_json({"type": "message", "text": "Gör något som hänger sig"})
                    events = self._drain_until(websocket, {"done"})

        # The client is notified (not left spinning): a friendly Swedish error and
        # a terminating done event both arrive.
        error_events = [e for e in events if e.get("type") == "error"]
        self.assertTrue(error_events, "expected a timeout error event")
        self.assertIn("lång tid", error_events[-1]["content"])
        done_events = [e for e in events if e.get("type") == "done"]
        self.assertTrue(done_events)
        self.assertEqual("Timeout", done_events[-1].get("summary"))
        # The hung work was actually cancelled by the watchdog.
        self.assertTrue(aborted.is_set(), "expected the hung turn to be cancelled")

    def _run_scenario(
        self,
        session_id: str,
        user_text: str,
        outcome,
        emitted_tools,
        reply: str,
        require_assistant_delta: bool = True,
    ) -> dict:
        import api.ws as ws_api
        import store

        app = FastAPI()

        @app.websocket("/ws")
        async def ws(websocket: WebSocket):
            await ws_api.websocket_endpoint(websocket)

        async def fake_classify_turn(prior, text, project=None, model_mode="auto"):
            return {"route": "computer", "task": text, "thinking": "scenario test", "model": "gemma4:12b"}

        async def fake_run_coordinator(task, emit, abort, *args, **kwargs):
            for tool, tool_args in emitted_tools:
                event_type = "confirmation_required" if tool == "confirmation_required" else "action"
                event_tool = "run_command" if tool == "confirmation_required" else tool
                emit({"type": event_type, "tool": event_tool, "args": tool_args})
            return outcome

        async def fake_compose_reply(conversation, grounding=None, model=None, memories=""):
            yield reply

        async def fake_search_memories(text, *args, **kwargs):
            return []

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(store, "SESSIONS_DIR", tmp), \
             mock.patch.object(ws_api, "list_projects", return_value=[]), \
             mock.patch.object(ws_api, "list_jobs", return_value=[]), \
             mock.patch.object(ws_api, "append_turn_diagnostic"), \
             mock.patch.object(ws_api, "classify_turn", new=fake_classify_turn), \
             mock.patch.object(ws_api, "run_coordinator", new=fake_run_coordinator), \
             mock.patch.object(ws_api, "compose_reply", new=fake_compose_reply), \
             mock.patch.object(ws_api, "search_memories", new=fake_search_memories):
            original_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with TestClient(app) as client:
                    with client.websocket_connect("/ws") as websocket:
                        websocket.send_json({"type": "hello", "session_id": session_id})
                        self._drain_until(websocket, {"jobs"})
                        websocket.send_json({"type": "message", "text": user_text})
                        events = self._drain_until(websocket, {"done"})
            finally:
                os.chdir(original_cwd)

            saved = self._load_saved_session(Path(tmp), session_id)

        assistant_events = [event["content"] for event in events if event.get("type") == "assistant_delta"]
        if require_assistant_delta:
            self.assertTrue(assistant_events)
        assistant_message = saved["messages"][-1]
        return {
            "events": events,
            "assistant": "".join(assistant_events),
            "saved": saved,
            "meta": assistant_message["meta"],
        }

    def _drain_until(self, websocket, stop_types: set[str]) -> list[dict]:
        events: list[dict] = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event.get("type") in stop_types:
                return events

    def _load_saved_session(self, root: Path, session_id: str) -> dict:
        path = root / f"{session_id}.json"
        for _ in range(50):
            if path.exists():
                import json

                return json.loads(path.read_text(encoding="utf-8"))
            time.sleep(0.01)
        self.fail(f"Session was not saved: {path}")

    def assert_final_answer_shape(self, answer: str) -> None:
        self.assertTrue(answer.strip())
        self.assertNotEqual("Klar", answer.strip())
        self.assertNotIn("web_research(", answer)
        self.assertNotIn("<tool_code>", answer)


if __name__ == "__main__":
    unittest.main()

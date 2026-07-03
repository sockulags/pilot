import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class RoutingDecisionTests(unittest.TestCase):
    def _build(self, **overrides):
        from agents.routing import build_routing_decision

        kwargs = dict(
            route_mode="auto",
            classified_route="chat",
            agent="claude",
            text="",
            project=None,
            cwd=None,
        )
        kwargs.update(overrides)
        return build_routing_decision(**kwargs)

    def test_every_decision_has_reason_and_engine(self):
        # Each route/engine combination must produce a non-empty reason and a
        # known execution engine — every turn is explainable.
        from agents.routing import REQUIRED_PERMISSIONS

        cases = [
            dict(classified_route="chat"),
            dict(classified_route="computer"),
            dict(classified_route="code", text="fix the bug in utils.py"),
            dict(classified_route="code", text="använd codex to fix it"),
            dict(route_mode="code", classified_route="chat"),
            dict(route_mode="computer", classified_route="chat"),
        ]
        for case in cases:
            decision = self._build(**case)
            self.assertTrue(decision.reason.strip(), case)
            self.assertIn(decision.execution_engine, REQUIRED_PERMISSIONS, case)
            self.assertTrue(decision.required_permissions, case)

    def test_fix_code_locally_stays_in_repo_agent(self):
        decision = self._build(
            classified_route="code", text="fix the bug in utils.py", project="pilot", cwd="/repo"
        )
        self.assertEqual("local_repo_agent", decision.execution_engine)
        self.assertFalse(decision.is_offload())
        self.assertEqual("/repo", decision.cwd)

    def test_swedish_fix_code_locally_stays_in_repo_agent(self):
        # Mirror existing test phrasing ("fixa koden lokalt").
        decision = self._build(
            classified_route="code", text="fixa koden lokalt", project="pilot", cwd="/repo"
        )
        self.assertEqual("local_repo_agent", decision.execution_engine)
        self.assertFalse(decision.is_offload())

    def test_use_codex_offloads_to_codex(self):
        from agents.orchestrator import should_offload_code

        self.assertTrue(should_offload_code("auto", "använd codex för att fixa det här"))
        decision = self._build(
            classified_route="code", agent="codex", text="använd codex för att fixa det här",
            project="pilot", cwd="/repo",
        )
        self.assertEqual("codex", decision.execution_engine)
        self.assertTrue(decision.is_offload())
        self.assertIn("external_agent", decision.required_permissions)

    def test_use_claude_offloads_to_claude_code(self):
        from agents.orchestrator import should_offload_code

        self.assertTrue(should_offload_code("auto", "använd claude code för detta"))
        decision = self._build(
            classified_route="code", agent="claude", text="använd claude code för detta",
            project="pilot", cwd="/repo",
        )
        self.assertEqual("claude_code", decision.execution_engine)
        self.assertTrue(decision.is_offload())

    def test_github_task_with_project_routes_to_computer_with_reason(self):
        # classify_turn already force-routes GitHub/repo terms to computer when a
        # project is active; build_routing_decision explains that choice.
        decision = self._build(
            classified_route="computer",
            text="fixa github issue #42 in the repo",
            project="pilot",
            cwd="/repo",
        )
        self.assertEqual("computer", decision.route)
        self.assertEqual("local_tools", decision.execution_engine)
        lowered = decision.reason.lower()
        self.assertTrue("github" in lowered or "project" in lowered, decision.reason)

    def test_no_project_code_question_engine_and_reason(self):
        # A code-classified question with no offload signal stays local.
        decision = self._build(
            classified_route="code", text="how do I write a python decorator?",
            project=None, cwd=None,
        )
        self.assertEqual("local_repo_agent", decision.execution_engine)
        self.assertFalse(decision.is_offload())
        self.assertIsNone(decision.cwd)
        self.assertTrue(decision.reason.strip())

    def test_forced_route_mode_is_honored_and_explained(self):
        decision = self._build(route_mode="code", classified_route="chat", agent="codex", text="anything")
        self.assertEqual("code", decision.route)
        self.assertTrue(decision.is_offload())  # forced code -> offload
        self.assertIn("forced route_mode=code", decision.reason)

    def test_forced_chat_route_overrides_classifier(self):
        decision = self._build(route_mode="chat", classified_route="code", text="fix utils.py")
        self.assertEqual("chat", decision.route)
        self.assertEqual("local_chat", decision.execution_engine)
        self.assertIn("forced route_mode=chat", decision.reason)

    def test_to_event_shape_is_emitted_before_action(self):
        decision = self._build(classified_route="computer", text="open the terminal")
        event = decision.to_event()
        self.assertEqual("routing_decision", event["type"])
        self.assertEqual("local_tools", event["execution_engine"])
        self.assertIn("required_permissions", event)
        self.assertIn("reason", event)


class RoutingEventEmittedBeforeActionTests(unittest.TestCase):
    """ws-level check: the routing_decision event precedes the coordinator/run."""

    def test_ws_emits_routing_decision_before_first_action(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from fastapi import FastAPI, WebSocket
        from fastapi.testclient import TestClient

        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState
        import api.ws as ws_api
        import store

        app = FastAPI()

        @app.websocket("/ws")
        async def ws(websocket: WebSocket):
            await ws_api.websocket_endpoint(websocket)

        async def fake_classify_turn(prior, text, project=None, model_mode="auto"):
            return {"route": "computer", "task": text, "thinking": "test", "model": "gemma4:12b"}

        async def fake_run_coordinator(task, emit, abort, *args, **kwargs):
            emit({"type": "action", "tool": "list_dir", "args": {"path": "."}})
            return LoopOutcome("done", "- list_dir", runtime_state=RuntimeState())

        async def fake_compose_reply(conversation, grounding=None, model=None, memories=""):
            yield "ok"

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
                        websocket.send_json({"type": "hello", "session_id": "routing-evt"})
                        self._drain_until(websocket, {"jobs"})
                        websocket.send_json({"type": "message", "text": "open the terminal"})
                        events = self._drain_until(websocket, {"done"})
            finally:
                os.chdir(original_cwd)

            saved = self._load_saved(Path(tmp), "routing-evt")

        types = [e.get("type") for e in events]
        self.assertIn("routing_decision", types)
        routing_idx = types.index("routing_decision")
        action_idx = types.index("action")
        self.assertLess(routing_idx, action_idx, "routing_decision must precede the first action")

        routing_event = events[routing_idx]
        self.assertEqual("local_tools", routing_event["execution_engine"])
        self.assertTrue(routing_event["reason"].strip())

        meta = saved["messages"][-1]["meta"]
        self.assertEqual("local_tools", meta["execution_engine"])
        self.assertTrue(meta["routing_reason"].strip())

    def _drain_until(self, websocket, stop_types):
        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event.get("type") in stop_types:
                return events

    def _load_saved(self, root, session_id):
        import json
        import time

        path = root / f"{session_id}.json"
        for _ in range(50):
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            time.sleep(0.01)
        self.fail(f"Session was not saved: {path}")


if __name__ == "__main__":
    unittest.main()

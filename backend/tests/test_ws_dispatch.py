"""WS message dispatch for projects, model/route pinning and jobs.

`backend/tests/test_projects.py` and `test_jobs.py` cover `add_project`,
`create_job` and friends in isolation; nothing proved that
`websocket_endpoint` wires a client message to the right function with the
right arguments, replies with the right event, and updates the right piece of
per-connection state (`cwd`, the `claude_session_id`/`codex_session_id` reset on
a project switch, `model_mode`, `route_mode`).

These tests drive the real endpoint over a real WebSocket with the same light
harness `test_ws_auth.py` uses. Nothing in the dispatch path is mocked — only
the three on-disk stores it writes to (`store.SESSIONS_DIR`,
`projects.PROJECTS_FILE`, `jobs.JOBS_FILE`) are redirected into a temp dir, so a
test run never touches the developer's real data. `connections._hooks` is
process-wide module state that `hello` registers into, so it is snapshotted and
restored, the same way `test_ws_scenarios.py` does it.

Note on patch targets: `api/ws.py` binds `add_project`, `create_job` etc. into
its own namespace at import time, but those functions read `PROJECTS_FILE` /
`JOBS_FILE` from *their* module globals at call time — so patching the store
paths on `projects` / `jobs` is enough to isolate the real implementations.
"""

import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import api.ws as ws_module  # noqa: E402
import connections  # noqa: E402
import jobs as jobs_module  # noqa: E402
import projects as projects_module  # noqa: E402
import store  # noqa: E402
from config import OLLAMA_MODELS  # noqa: E402

KNOWN_MODEL = next(iter(OLLAMA_MODELS))
VALID_SCHEDULE = {"type": "daily", "time": "09:00"}


def _make_client() -> TestClient:
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await ws_module.websocket_endpoint(websocket)

    return TestClient(app)


class WebSocketDispatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pilot-ws-dispatch-")
        self.root = Path(self._tmp.name)
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir()

        self._stack = contextlib.ExitStack()
        # No token configured: this file is about dispatch, not the auth gate
        # (test_ws_auth.py owns that).
        self._stack.enter_context(mock.patch.object(ws_module, "PILOT_AUTH_TOKEN", ""))
        self._stack.enter_context(mock.patch.object(store, "SESSIONS_DIR", str(self.sessions_dir)))
        self._stack.enter_context(
            mock.patch.object(projects_module, "PROJECTS_FILE", str(self.root / "projects.json"))
        )
        self._stack.enter_context(mock.patch.object(projects_module, "PILOT_PROJECT_ROOTS", ""))
        self._stack.enter_context(
            mock.patch.object(jobs_module, "JOBS_FILE", str(self.root / "jobs.json"))
        )

        self._saved_hooks = {k: list(v) for k, v in connections._hooks.items()}

    def tearDown(self):
        connections._hooks.clear()
        connections._hooks.update(self._saved_hooks)
        self._stack.close()
        self._tmp.cleanup()

    # --- helpers ------------------------------------------------------------

    def _project_dir(self, name: str) -> str:
        path = self.root / name
        path.mkdir()
        return str(path)

    def _hello(self, ws, session_id: str | None = None) -> dict:
        """Send `hello` and drain its history/projects/jobs preamble."""
        msg = {"type": "hello"}
        if session_id:
            msg["session_id"] = session_id
        ws.send_json(msg)
        self.assertEqual("history", ws.receive_json()["type"])
        projects_reply = ws.receive_json()
        self.assertEqual("projects", projects_reply["type"])
        self.assertEqual("jobs", ws.receive_json()["type"])
        return projects_reply

    def _expect(self, ws, msg_type: str) -> dict:
        reply = ws.receive_json()
        self.assertEqual(msg_type, reply["type"], f"unexpected reply: {reply}")
        return reply

    def _add_project_via_ws(self, ws, path: str) -> dict:
        ws.send_json({"type": "add_project", "path": path})
        reply = self._expect(ws, "projects")
        entry = next(p for p in reply["projects"] if p["path"] == os.path.abspath(path))
        return entry

    def _add_job_via_ws(self, ws, **overrides) -> dict:
        msg = {
            "type": "add_job",
            "payload": "drick vatten",
            "schedule": VALID_SCHEDULE,
            **overrides,
        }
        ws.send_json(msg)
        return self._expect(ws, "jobs")

    def _saved_session(self, session_id: str) -> dict:
        import json

        with open(self.sessions_dir / f"{session_id}.json", "r", encoding="utf-8") as f:
            return json.load(f)

    # --- add_project --------------------------------------------------------

    def test_add_project_with_valid_path_is_stored_and_broadcast(self):
        target = self._project_dir("myproj")
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "add_project", "path": target})
            # No error precedes the broadcast on the happy path.
            reply = self._expect(ws, "projects")

        self.assertEqual(
            [(os.path.abspath(target), "myproj")],
            [(p["path"], p["name"]) for p in reply["projects"]],
        )
        # It really went through projects.add_project, not just into the reply.
        self.assertEqual(1, len(projects_module.list_projects()))

    def test_add_project_with_missing_path_sends_error_then_projects(self):
        missing = str(self.root / "does_not_exist")
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "add_project", "path": missing})
            error = self._expect(ws, "error")
            reply = self._expect(ws, "projects")

        self.assertTrue(error["content"].startswith("Mappen finns inte:"))
        self.assertIn(os.path.abspath(missing), error["content"])
        self.assertEqual([], reply["projects"])
        self.assertEqual([], projects_module.list_projects())

    def test_add_project_with_empty_path_sends_error_then_projects(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "add_project"})  # path defaults to ""
            error = self._expect(ws, "error")
            reply = self._expect(ws, "projects")

        self.assertEqual("Tom sökväg.", error["content"])
        self.assertEqual([], reply["projects"])

    # --- remove_project -----------------------------------------------------

    def test_remove_project_drops_only_the_given_id(self):
        a = self._project_dir("a")
        b = self._project_dir("b")
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            entry_a = self._add_project_via_ws(ws, a)
            self._add_project_via_ws(ws, b)

            ws.send_json({"type": "remove_project", "id": entry_a["id"]})
            reply = self._expect(ws, "projects")

        self.assertEqual(["b"], [p["name"] for p in reply["projects"]])
        self.assertEqual(["b"], [p["name"] for p in projects_module.list_projects()])

    def test_remove_project_with_unknown_id_leaves_the_list_intact(self):
        a = self._project_dir("a")
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            self._add_project_via_ws(ws, a)
            ws.send_json({"type": "remove_project", "id": "no-such-id"})
            reply = self._expect(ws, "projects")

        self.assertEqual(["a"], [p["name"] for p in reply["projects"]])

    # --- select_project -----------------------------------------------------

    def test_select_project_switches_cwd_and_resets_coding_sessions(self):
        session_id = "dispatch-select-switch"
        a = self._project_dir("a")
        b = self._project_dir("b")
        projects_module.add_project(a)
        stored, _ = projects_module.add_project(b)
        id_a = next(p["id"] for p in stored if p["name"] == "a")
        id_b = next(p["id"] for p in stored if p["name"] == "b")
        # A session already sitting in project a with live coding sessions.
        store.save_session(
            session_id,
            [{"role": "user", "content": "hej", "turn": 1}],
            1,
            cwd=os.path.abspath(a),
            claude_session_id="claude-abc",
            codex_session_id="codex-xyz",
        )

        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            self._hello(ws, session_id)
            self.assertEqual("claude-abc", self._saved_session(session_id)["claude_session_id"])

            ws.send_json({"type": "select_project", "id": id_b})
            reply = self._expect(ws, "projects")

        self.assertEqual(os.path.abspath(b), reply["selected"])
        saved = self._saved_session(session_id)
        self.assertEqual(os.path.abspath(b), saved["cwd"])
        self.assertIsNone(saved["claude_session_id"])
        self.assertIsNone(saved["codex_session_id"])
        self.assertNotEqual(id_a, id_b)

    def test_select_project_for_the_active_project_keeps_coding_sessions(self):
        session_id = "dispatch-select-same"
        a = self._project_dir("a")
        stored, _ = projects_module.add_project(a)
        id_a = stored[0]["id"]
        store.save_session(
            session_id,
            [{"role": "user", "content": "hej", "turn": 1}],
            1,
            cwd=os.path.abspath(a),
            claude_session_id="claude-abc",
            codex_session_id="codex-xyz",
        )

        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            self._hello(ws, session_id)

            ws.send_json({"type": "select_project", "id": id_a})
            reply = self._expect(ws, "projects")
            # select_project only persists when the cwd actually changed, so
            # force a write through another handler to read the live state back.
            ws.send_json({"type": "select_agent", "agent": "codex"})
            self._expect(ws, "projects")

        self.assertEqual(os.path.abspath(a), reply["selected"])
        saved = self._saved_session(session_id)
        self.assertEqual("codex", saved["agent"])  # the forced write happened
        self.assertEqual("claude-abc", saved["claude_session_id"])
        self.assertEqual("codex-xyz", saved["codex_session_id"])

    # --- select_model -------------------------------------------------------

    def test_select_model_pins_a_known_model_and_returns_to_auto(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_model", "model_mode": KNOWN_MODEL})
            self.assertEqual(KNOWN_MODEL, self._expect(ws, "projects")["model_mode"])

            ws.send_json({"type": "select_model", "model_mode": "auto"})
            self.assertEqual("auto", self._expect(ws, "projects")["model_mode"])

    def test_select_model_ignores_an_unknown_model_id(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_model", "model_mode": KNOWN_MODEL})
            self._expect(ws, "projects")

            ws.send_json({"type": "select_model", "model_mode": "no-such-model:1b"})
            self.assertEqual(KNOWN_MODEL, self._expect(ws, "projects")["model_mode"])

    # --- select_route -------------------------------------------------------

    def test_select_route_accepts_every_supported_mode(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            for mode in ("chat", "computer", "code", "auto"):
                ws.send_json({"type": "select_route", "route_mode": mode})
                self.assertEqual(mode, self._expect(ws, "projects")["route_mode"])

    def test_select_route_ignores_an_unrecognized_mode(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_route", "route_mode": "code"})
            self._expect(ws, "projects")

            ws.send_json({"type": "select_route", "route_mode": "telepathy"})
            self.assertEqual("code", self._expect(ws, "projects")["route_mode"])

    # --- add_job ------------------------------------------------------------

    def test_add_job_creates_the_job_and_lists_it(self):
        session_id = "dispatch-add-job"
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            self._hello(ws, session_id)
            reply = self._add_job_via_ws(ws, title="Vatten", kind="task")

        self.assertEqual(1, len(reply["jobs"]))
        job = reply["jobs"][0]
        self.assertEqual("Vatten", job["title"])
        self.assertEqual("drick vatten", job["payload"])
        self.assertEqual("task", job["kind"])
        self.assertTrue(job["enabled"])
        self.assertEqual("dagligen kl 09:00", job["summary"])
        self.assertEqual([session_id], [j["session_id"] for j in jobs_module.list_jobs()])

    def test_add_job_falls_back_to_the_payload_as_title(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            self._hello(ws, "dispatch-job-title")
            reply = self._add_job_via_ws(ws)

        self.assertEqual("drick vatten", reply["jobs"][0]["title"])

    def test_add_job_without_payload_errors_and_creates_nothing(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            self._hello(ws, "dispatch-job-no-payload")
            ws.send_json({"type": "add_job", "payload": "   ", "schedule": VALID_SCHEDULE})
            error = self._expect(ws, "error")
            reply = self._expect(ws, "jobs")

        self.assertEqual("Jobbet saknar text.", error["content"])
        self.assertEqual([], reply["jobs"])
        self.assertEqual([], jobs_module.list_jobs())

    def test_add_job_with_an_invalid_schedule_errors_and_creates_nothing(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            self._hello(ws, "dispatch-job-bad-schedule")
            ws.send_json(
                {"type": "add_job", "payload": "drick vatten", "schedule": {"type": "bogus"}}
            )
            error = self._expect(ws, "error")
            reply = self._expect(ws, "jobs")

        self.assertEqual("Ogiltigt schema för jobbet.", error["content"])
        self.assertEqual([], reply["jobs"])
        self.assertEqual([], jobs_module.list_jobs())

    # --- pause_job / resume_job / delete_job --------------------------------

    def test_pause_and_resume_job_toggle_enabled(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            self._hello(ws, "dispatch-job-toggle")
            job_id = self._add_job_via_ws(ws)["jobs"][0]["id"]

            ws.send_json({"type": "pause_job", "id": job_id})
            paused = self._expect(ws, "jobs")
            self.assertFalse(paused["jobs"][0]["enabled"])

            ws.send_json({"type": "resume_job", "id": job_id})
            resumed = self._expect(ws, "jobs")
            self.assertTrue(resumed["jobs"][0]["enabled"])

        self.assertTrue(jobs_module.get_job(job_id)["enabled"])

    def test_delete_job_removes_it_from_the_list(self):
        client = _make_client()
        with client.websocket_connect("/ws") as ws:
            self._hello(ws, "dispatch-job-delete")
            job_id = self._add_job_via_ws(ws)["jobs"][0]["id"]

            ws.send_json({"type": "delete_job", "id": job_id})
            reply = self._expect(ws, "jobs")

        self.assertEqual([], reply["jobs"])
        self.assertIsNone(jobs_module.get_job(job_id))


if __name__ == "__main__":
    unittest.main()

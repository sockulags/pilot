"""Auth gate for the WebSocket endpoint.

The WS can run arbitrary shell commands and desktop input, and the backend
binds 0.0.0.0 — so when PILOT_AUTH_TOKEN is set the endpoint must be
fail-closed: no message type other than a successful "hello" may be processed.
"""

import os
import sys
import unittest
from unittest import mock

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import api.ws as ws_module


def _make_client() -> TestClient:
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await ws_module.websocket_endpoint(websocket)

    return TestClient(app)


class WebSocketAuthTests(unittest.TestCase):
    def test_message_without_hello_is_rejected_when_token_set(self):
        # Regression: auth used to be checked only inside the "hello" branch,
        # so skipping hello bypassed it entirely and started a full turn.
        with mock.patch.object(ws_module, "PILOT_AUTH_TOKEN", "secret"):
            client = _make_client()
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "message", "text": "run whoami"})
                reply = ws.receive_json()
                self.assertEqual("error", reply["type"])
                self.assertEqual("unauthorized", reply["content"])
                with self.assertRaises(WebSocketDisconnect):
                    ws.receive_json()

    def test_control_message_without_hello_is_rejected_when_token_set(self):
        with mock.patch.object(ws_module, "PILOT_AUTH_TOKEN", "secret"):
            client = _make_client()
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "add_project", "path": "C:\\"})
                reply = ws.receive_json()
                self.assertEqual("error", reply["type"])
                self.assertEqual("unauthorized", reply["content"])

    def test_hello_with_wrong_token_is_rejected(self):
        with mock.patch.object(ws_module, "PILOT_AUTH_TOKEN", "secret"):
            client = _make_client()
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "hello", "token": "wrong"})
                reply = ws.receive_json()
                self.assertEqual("error", reply["type"])
                self.assertEqual("unauthorized", reply["content"])

    def test_hello_with_missing_token_is_rejected(self):
        with mock.patch.object(ws_module, "PILOT_AUTH_TOKEN", "secret"):
            client = _make_client()
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "hello"})
                reply = ws.receive_json()
                self.assertEqual("error", reply["type"])
                self.assertEqual("unauthorized", reply["content"])

    def test_hello_with_correct_token_authenticates(self):
        with mock.patch.object(ws_module, "PILOT_AUTH_TOKEN", "secret"):
            client = _make_client()
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "hello", "token": "secret"})
                reply = ws.receive_json()
                self.assertEqual("history", reply["type"])
                # And subsequent control messages are served, not rejected.
                ws.receive_json()  # projects
                ws.receive_json()  # jobs
                ws.send_json({"type": "select_agent", "agent": "codex"})
                reply = ws.receive_json()
                self.assertEqual("projects", reply["type"])
                self.assertEqual("codex", reply["agent"])

    def test_no_token_configured_keeps_endpoint_open(self):
        with mock.patch.object(ws_module, "PILOT_AUTH_TOKEN", ""):
            client = _make_client()
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "select_agent", "agent": "claude"})
                reply = ws.receive_json()
                self.assertEqual("projects", reply["type"])


if __name__ == "__main__":
    unittest.main()

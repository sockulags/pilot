import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

import api.mcp as mcp


class MCPAuthTests(unittest.TestCase):
    """The MCP HTTP surface exposes computer-control tools, so when a token is
    configured it must be presented; unauthenticated calls get 401 and never
    reach tool execution (mocked here so no real OS action runs)."""

    TOKEN = "s3cret-mcp-token"

    def _client(self):
        return TestClient(mcp.create_mcp_app())

    def test_call_without_token_is_rejected(self):
        with mock.patch.object(mcp, "PILOT_MCP_AUTH_TOKEN", self.TOKEN), \
                mock.patch.object(mcp, "open_app") as open_app:
            with self._client() as client:
                resp = client.post(
                    "/mcp/call",
                    json={"name": "pilot_open_app", "arguments": {"name": "Calculator"}},
                )
            self.assertEqual(401, resp.status_code)
            self.assertEqual("unauthorized", resp.json()["error"])
            open_app.assert_not_called()

    def test_call_with_wrong_token_is_rejected(self):
        with mock.patch.object(mcp, "PILOT_MCP_AUTH_TOKEN", self.TOKEN), \
                mock.patch.object(mcp, "run_command_sync") as run_command:
            with self._client() as client:
                resp = client.post(
                    "/mcp/call",
                    headers={"Authorization": "Bearer nope"},
                    json={"name": "pilot_run_command", "arguments": {"cmd": "whoami"}},
                )
            self.assertEqual(401, resp.status_code)
            run_command.assert_not_called()

    def test_call_with_bearer_token_succeeds(self):
        with mock.patch.object(mcp, "PILOT_MCP_AUTH_TOKEN", self.TOKEN), \
                mock.patch.object(mcp, "run_command_sync", return_value="ok") as run_command:
            with self._client() as client:
                resp = client.post(
                    "/mcp/call",
                    headers={"Authorization": f"Bearer {self.TOKEN}"},
                    json={"name": "pilot_run_command", "arguments": {"cmd": "whoami"}},
                )
            self.assertEqual(200, resp.status_code)
            self.assertEqual("ok", resp.json()["content"][0]["text"])
            run_command.assert_called_once()

    def test_call_with_x_pilot_token_header_succeeds(self):
        with mock.patch.object(mcp, "PILOT_MCP_AUTH_TOKEN", self.TOKEN), \
                mock.patch.object(mcp, "open_app", return_value="opened") as open_app:
            with self._client() as client:
                resp = client.post(
                    "/mcp/call",
                    headers={"X-Pilot-Token": self.TOKEN},
                    json={"name": "pilot_open_app", "arguments": {"name": "Calculator"}},
                )
            self.assertEqual(200, resp.status_code)
            self.assertEqual("opened", resp.json()["content"][0]["text"])
            open_app.assert_called_once()

    def test_no_token_configured_allows_calls(self):
        with mock.patch.object(mcp, "PILOT_MCP_AUTH_TOKEN", ""), \
                mock.patch.object(mcp, "open_app", return_value="opened") as open_app:
            with self._client() as client:
                resp = client.post(
                    "/mcp/call",
                    json={"name": "pilot_open_app", "arguments": {"name": "Calculator"}},
                )
            self.assertEqual(200, resp.status_code)
            open_app.assert_called_once()

    def test_sse_endpoint_requires_token(self):
        with mock.patch.object(mcp, "PILOT_MCP_AUTH_TOKEN", self.TOKEN):
            with self._client() as client:
                resp = client.get("/mcp")
            self.assertEqual(401, resp.status_code)


if __name__ == "__main__":
    unittest.main()

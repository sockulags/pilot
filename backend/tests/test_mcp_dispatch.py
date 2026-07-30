import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import Request
from fastapi.testclient import TestClient

import api.mcp as mcp


class MCPDispatchTests(unittest.TestCase):
    """Dispatch behaviour of ``POST /mcp/call`` and the SSE manifest stream.

    Auth is covered separately in ``tests/test_mcp_auth.py``; here no token is
    configured so every call reaches the dispatch chain. Every underlying tool
    is mocked on the ``api.mcp`` module, so no test takes a real screenshot,
    moves the mouse, types, or enumerates windows.
    """

    def setUp(self):
        patcher = mock.patch.object(mcp, "PILOT_MCP_AUTH_TOKEN", "")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self):
        return TestClient(mcp.create_mcp_app())

    def _call(self, name, arguments=None):
        body: dict = {"name": name}
        if arguments is not None:
            body["arguments"] = arguments
        with self._client() as client:
            return client.post("/mcp/call", json=body)

    def _text(self, resp):
        return resp.json()["content"][0]["text"]

    # --- success paths for the handlers that had no test at all -------------

    def test_screenshot_returns_image_content(self):
        with mock.patch.object(mcp, "screenshot", return_value="BASE64PNG") as shot:
            resp = self._call("pilot_screenshot")
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            [{"type": "image", "data": "BASE64PNG", "mimeType": "image/png"}],
            resp.json()["content"],
        )
        shot.assert_called_once_with()

    def test_click_dispatches_with_default_button(self):
        with mock.patch.object(mcp, "click", return_value="clicked (10, 20)") as click:
            resp = self._call("pilot_click", {"x": 10, "y": 20})
        self.assertEqual(200, resp.status_code)
        self.assertEqual("clicked (10, 20)", self._text(resp))
        click.assert_called_once_with(10, 20, "left")

    def test_click_passes_explicit_button_through(self):
        with mock.patch.object(mcp, "click", return_value="clicked") as click:
            resp = self._call("pilot_click", {"x": 1, "y": 2, "button": "right"})
        self.assertEqual(200, resp.status_code)
        click.assert_called_once_with(1, 2, "right")

    def test_type_dispatches_text(self):
        with mock.patch.object(mcp, "type_text", return_value="typed 5 chars") as type_text:
            resp = self._call("pilot_type", {"text": "hello"})
        self.assertEqual(200, resp.status_code)
        self.assertEqual("typed 5 chars", self._text(resp))
        type_text.assert_called_once_with("hello")

    def test_list_dir_returns_json_encoded_listing(self):
        listing = [{"name": "notes.txt", "isDir": False}]
        with mock.patch.object(mcp, "list_dir", return_value=listing) as list_dir:
            resp = self._call("pilot_list_dir", {"path": "C:/work"})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(listing, json.loads(self._text(resp)))
        list_dir.assert_called_once_with("C:/work")

    def test_list_dir_without_path_uses_tool_default(self):
        with mock.patch.object(mcp, "list_dir", return_value=[]) as list_dir:
            resp = self._call("pilot_list_dir", {})
        self.assertEqual(200, resp.status_code)
        list_dir.assert_called_once_with(None)

    def test_read_file_returns_file_text(self):
        with mock.patch.object(mcp, "read_file", return_value={"text": "file body"}) as read_file:
            resp = self._call("pilot_read_file", {"path": "notes.txt"})
        self.assertEqual(200, resp.status_code)
        self.assertEqual("file body", self._text(resp))
        read_file.assert_called_once_with("notes.txt")

    def test_find_file_returns_json_encoded_matches(self):
        matches = ["C:/work/notes.txt"]
        with mock.patch.object(mcp, "find_file", return_value=matches) as find_file:
            resp = self._call("pilot_find_file", {"name": "notes.txt", "root": "C:/work"})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(matches, json.loads(self._text(resp)))
        find_file.assert_called_once_with("notes.txt", "C:/work")

    def test_list_windows_returns_json_encoded_windows(self):
        windows = [{"title": "Pilot", "isActive": True}]
        with mock.patch.object(mcp, "list_windows", return_value=windows) as list_windows:
            resp = self._call("pilot_list_windows")
        self.assertEqual(200, resp.status_code)
        self.assertEqual(windows, json.loads(self._text(resp)))
        list_windows.assert_called_once_with()

    def test_focus_window_returns_json_encoded_result(self):
        focused = {"ok": True, "title": "Pilot"}
        with mock.patch.object(mcp, "focus_window", return_value=focused) as focus_window:
            resp = self._call("pilot_focus_window", {"title": "Pilot"})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(focused, json.loads(self._text(resp)))
        focus_window.assert_called_once_with("Pilot")

    # --- error paths: ordinary bad input must be a structured 404, not a 500 --

    def _assert_not_found(self, resp, tool, needle):
        self.assertEqual(404, resp.status_code)
        body = resp.json()
        self.assertIn("error", body)
        self.assertIn(tool, body["error"])
        self.assertIn(needle, body["error"])

    def test_read_file_missing_path_is_404(self):
        with mock.patch.object(
            mcp, "read_file", side_effect=FileNotFoundError("no such file: nope.txt")
        ):
            resp = self._call("pilot_read_file", {"path": "nope.txt"})
        self._assert_not_found(resp, "pilot_read_file", "nope.txt")

    def test_list_dir_missing_directory_is_404(self):
        with mock.patch.object(
            mcp, "list_dir", side_effect=NotADirectoryError("not a directory: nope_dir")
        ):
            resp = self._call("pilot_list_dir", {"path": "nope_dir"})
        self._assert_not_found(resp, "pilot_list_dir", "nope_dir")

    def test_find_file_missing_root_is_404(self):
        with mock.patch.object(
            mcp, "find_file", side_effect=NotADirectoryError("not a directory: nope_dir")
        ):
            resp = self._call("pilot_find_file", {"name": "x", "root": "nope_dir"})
        self._assert_not_found(resp, "pilot_find_file", "nope_dir")

    def test_focus_window_unknown_title_is_404(self):
        with mock.patch.object(
            mcp, "focus_window", side_effect=ValueError("no window matching 'nope_window'")
        ):
            resp = self._call("pilot_focus_window", {"title": "nope_window"})
        self._assert_not_found(resp, "pilot_focus_window", "nope_window")

    def test_run_command_with_missing_cwd_is_404(self):
        with mock.patch.object(
            mcp, "run_command_sync", side_effect=NotADirectoryError("cwd not found: nope_dir")
        ):
            resp = self._call("pilot_run_command", {"cmd": "whoami", "cwd": "nope_dir"})
        self._assert_not_found(resp, "pilot_run_command", "nope_dir")

    def test_unrelated_exception_is_not_swallowed_as_404(self):
        """The catch is narrow on purpose: a PermissionError is a different
        failure than "not found" and must not be reported as one."""
        with mock.patch.object(mcp, "read_file", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self._call("pilot_read_file", {"path": "locked.txt"})

    # --- validation / negotiation branches ----------------------------------

    def test_non_string_tool_name_returns_unknown_tool(self):
        resp = self._call(123, {})
        self.assertEqual(200, resp.status_code)
        self.assertEqual("Unknown tool: 123", resp.json()["error"])

    def test_non_dict_arguments_is_400(self):
        with self._client() as client:
            resp = client.post(
                "/mcp/call", json={"name": "pilot_list_windows", "arguments": ["nope"]}
            )
        self.assertEqual(400, resp.status_code)
        self.assertEqual("invalid arguments: expected an object", resp.json()["error"])

    def test_non_whitelisted_app_requires_confirmation(self):
        with mock.patch.object(mcp, "open_app") as open_app:
            resp = self._call("pilot_open_app", {"name": "Notepad"})
        self.assertEqual(200, resp.status_code)
        body = resp.json()
        self.assertEqual("confirmation_required", body["error"])
        self.assertEqual("pilot_open_app", body["tool"])
        self.assertEqual("high", body["riskLevel"])
        self.assertIn("sideEffects", body)
        self.assertTrue(body["reason"])
        open_app.assert_not_called()

    def test_missing_required_argument_is_400(self):
        with mock.patch.object(mcp, "read_file") as read_file:
            resp = self._call("pilot_read_file", {})
        self.assertEqual(400, resp.status_code)
        self.assertEqual("missing required argument(s): path", resp.json()["error"])
        read_file.assert_not_called()

    def test_unknown_tool_name_returns_unknown_tool(self):
        resp = self._call("pilot_does_not_exist", {})
        self.assertEqual(200, resp.status_code)
        self.assertEqual("Unknown tool: pilot_does_not_exist", resp.json()["error"])

    # --- SSE stream ----------------------------------------------------------

    def test_sse_stream_emits_tools_manifest_then_keepalives(self):
        """The stream never ends by design, so it is driven through the ASGI
        endpoint directly: ``TestClient`` (like httpx's ASGI transport) buffers
        a response until the app signals completion, which an endless generator
        never does. Calling the route gives the real ``StreamingResponse`` and
        its real body iterator, which can be read event by event and closed."""
        real_sleep = asyncio.sleep

        async def _skip_keepalive_wait(delay, *args, **kwargs):
            # The stream idles 30s between pings; collapse only that wait so the
            # keepalive event is observable without slowing the suite down.
            return await real_sleep(0 if delay == 30 else delay, *args, **kwargs)

        async def read_first_events():
            response = await self._sse_endpoint()(self._sse_request())
            self.assertEqual(200, response.status_code)
            self.assertEqual("text/event-stream", response.media_type)
            self.assertEqual("no-cache", response.headers["cache-control"])

            events = []
            stream = response.body_iterator
            with mock.patch.object(asyncio, "sleep", _skip_keepalive_wait):
                try:
                    for _ in range(2):
                        chunk = await stream.__anext__()
                        events.append(json.loads(chunk.split("data: ", 1)[1]))
                finally:
                    await stream.aclose()
            return events

        events = asyncio.run(read_first_events())

        self.assertEqual("tools", events[0]["type"])
        names = [tool["name"] for tool in events[0]["tools"]]
        self.assertIn("pilot_screenshot", names)
        self.assertIn("pilot_read_file", names)
        self.assertEqual({"type": "ping"}, events[1])

    def _sse_endpoint(self):
        app = mcp.create_mcp_app()
        return next(
            route.endpoint
            for route in app.routes
            if getattr(route, "path", None) == "/mcp"
            and "GET" in getattr(route, "methods", ())
        )

    def _sse_request(self):
        return Request({
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "root_path": "",
            "headers": [],
        })


if __name__ == "__main__":
    unittest.main()

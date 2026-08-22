"""Wiring smoke tests for `create_app()`.

The app factory is where the process-wide HTTP surface is assembled: the health
probe, the CORS boundary, the settings API, the WebSocket route and the
optional static frontend mount. A mistake here (a router that stops being
included, a CORS list that stops being read, a static mount that swallows
/health) breaks the whole product while every unit test still passes, so the
assembly itself gets its own tests.

`lifespan()` is deliberately not exercised: its body validates a vision model
against a live Ollama and starts the external MCP manager plus the scheduler
task, which needs process-level doubles disproportionate to a wiring check.
These tests build the app and speak HTTP to it without entering the
TestClient context manager, so the lifespan never runs and `vision_status`
stays at its pre-startup value.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main


def _app_without_frontend():
    """create_app() with the static mount guaranteed off (no such directory)."""
    missing = os.path.join(
        tempfile.gettempdir(), "pilot-test-frontend-that-does-not-exist"
    )
    with mock.patch.object(main, "FRONTEND_DIR", missing):
        return main.create_app()


class HealthEndpointTests(unittest.TestCase):
    def test_health_reports_the_pre_startup_vision_status(self):
        app = _app_without_frontend()
        # No `with TestClient(...)`: lifespan must not run, so this is the
        # initial state create_app() sets, not a validated vision model.
        resp = TestClient(app).get("/health")

        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {"status": "ok", "vision": {"ok": None, "message": "not checked"}},
            resp.json(),
        )

    def test_health_follows_app_state_vision_status(self):
        app = _app_without_frontend()
        app.state.vision_status = {"ok": True, "message": "qwen3.5:9b ready"}

        resp = TestClient(app).get("/health")

        self.assertEqual(
            {"ok": True, "message": "qwen3.5:9b ready"}, resp.json()["vision"]
        )


class CorsTests(unittest.TestCase):
    """The configured origin list must reach the middleware, not a default."""

    def test_configured_origin_is_allowed_on_preflight(self):
        with mock.patch.object(main, "PILOT_CORS_ORIGINS", ["http://localhost:4321"]):
            app = _app_without_frontend()

        resp = TestClient(app).options("/health", headers={
            "Origin": "http://localhost:4321",
            "Access-Control-Request-Method": "GET",
        })

        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            "http://localhost:4321", resp.headers["access-control-allow-origin"]
        )

    def test_unconfigured_origin_gets_no_allow_header(self):
        with mock.patch.object(main, "PILOT_CORS_ORIGINS", ["http://localhost:4321"]):
            app = _app_without_frontend()

        resp = TestClient(app).options("/health", headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        })

        self.assertNotIn("access-control-allow-origin", resp.headers)

    def test_cors_middleware_is_attached_with_the_configured_origins(self):
        with mock.patch.object(main, "PILOT_CORS_ORIGINS", ["http://localhost:4321"]):
            app = _app_without_frontend()

        cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
        self.assertEqual(1, len(cors))
        self.assertEqual(["http://localhost:4321"], cors[0].kwargs["allow_origins"])
        self.assertTrue(cors[0].kwargs["allow_credentials"])


class RouteWiringTests(unittest.TestCase):
    def test_settings_router_is_mounted(self):
        resp = TestClient(_app_without_frontend()).get("/api/settings/models")

        self.assertEqual(200, resp.status_code)
        self.assertIn("role_catalog", resp.json())

    def test_websocket_route_is_registered(self):
        # The handler's own behaviour is covered by the WS tests; here only the
        # route's presence on the assembled app matters.
        paths = [
            r.path for r in _app_without_frontend().routes
            if isinstance(r, APIWebSocketRoute)
        ]
        self.assertEqual(["/ws"], paths)


class StaticFrontendMountTests(unittest.TestCase):
    """The frontend mount is conditional on the built export existing."""

    def test_built_frontend_is_served_at_root(self):
        with tempfile.TemporaryDirectory(prefix="pilot-test-frontend-") as out:
            with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
                fh.write("<!doctype html><title>Pilot</title>")
            with mock.patch.object(main, "FRONTEND_DIR", out):
                app = main.create_app()
            client = TestClient(app)

            root = client.get("/")
            self.assertEqual(200, root.status_code)
            self.assertIn("<title>Pilot</title>", root.text)
            # The mount is last, so the API surface still wins over it.
            self.assertEqual(200, client.get("/health").status_code)

        self.assertEqual(
            ["static"], [r.name for r in app.routes if isinstance(r, Mount)]
        )

    def test_missing_frontend_dir_registers_no_mount(self):
        app = _app_without_frontend()

        self.assertEqual([], [r for r in app.routes if isinstance(r, Mount)])
        self.assertEqual(404, TestClient(app).get("/").status_code)


if __name__ == "__main__":
    unittest.main()

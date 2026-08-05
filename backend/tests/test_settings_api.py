"""REST settings API: auth, masking, save-and-reload, provider testing."""

import asyncio
import socket
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import model_settings
from agents import local_runtime
from api import settings as settings_api

_LOOPBACK_OLLAMA = "http://127.0.0.1:11434"
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(settings_api.create_settings_router())
    return app


def _mock_transport_client(handler):
    """A client factory that answers from `handler` instead of a socket.

    Only the transport is faked, so `raise_for_status()`, `.json()` and header
    handling stay the genuine httpx behaviour the production code relies on.
    """

    def factory(*_args, **kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

    return factory


def _capabilities(discovered):
    """Pin the /api/show enrichment so the registry merge stays deterministic."""

    async def fake(_names):
        return discovered

    return mock.patch.object(settings_api, "discover_model_capabilities", fake)


def _loopback_runtime(**overrides):
    runtime = local_runtime.LocalRuntimeConfig(
        kind="ollama", base_url=_LOOPBACK_OLLAMA, **overrides
    )
    return mock.patch.object(
        model_settings, "local_runtime_snapshot", lambda *_a, **_k: runtime
    )


def _public_dns():
    """Resolve any cloud provider hostname to a public address, offline."""
    return mock.patch.object(
        socket, "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ],
    )


def _mock_discover(names=None, error=None):
    """(recorded configs, patch) for local_runtime.discover."""
    seen = []

    async def fake(config):
        seen.append(config)
        if error is not None:
            raise error
        return list(names or [])

    return seen, mock.patch.object(local_runtime, "discover", fake)


def _provider_payload(**overrides) -> dict:
    body = {
        "version": 1,
        "cloud_providers": [{
            "id": "openai",
            "label": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-secret-abcdef123456",
            "models": ["gpt-4o-mini"],
            "enabled": True,
        }],
        "roles": {"research_agent": {"provider": "openai", "model": "gpt-4o-mini"}},
    }
    body.update(overrides)
    return body


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_app())

    def test_get_returns_catalog_and_masked_settings(self):
        resp = self.client.get("/api/settings/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("role_catalog", data)
        role_ids = {r["id"] for r in data["role_catalog"]}
        self.assertIn("default_agent", role_ids)
        self.assertIn("synthesis", role_ids)

    def test_put_saves_and_masks_key(self):
        resp = self.client.put("/api/settings/models", json=_provider_payload())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["errors"], [])
        entry = data["settings"]["cloud_providers"][0]
        self.assertEqual(entry["api_key"], "")
        self.assertTrue(entry["has_key"])
        # And the assignment is live for the routing layer.
        self.assertEqual(
            model_settings.resolve_role_model("research_agent"),
            "cloud:openai:gpt-4o-mini",
        )

    def test_put_with_invalid_payload_returns_400_and_keeps_old(self):
        self.client.put("/api/settings/models", json=_provider_payload())
        bad = _provider_payload()
        bad["cloud_providers"][0]["base_url"] = "not-a-url"
        resp = self.client.put("/api/settings/models", json=bad)
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.json()["errors"])
        model_settings.reset_cache_for_tests()
        self.assertEqual(
            model_settings.resolve_role_model("research_agent"),
            "cloud:openai:gpt-4o-mini",
        )

    def test_put_without_key_keeps_stored_secret(self):
        self.client.put("/api/settings/models", json=_provider_payload())
        update = _provider_payload()
        update["cloud_providers"][0]["api_key"] = ""  # browser never has the key
        resp = self.client.put("/api/settings/models", json=update)
        self.assertEqual(resp.status_code, 200)
        entry = model_settings.cloud_provider("openai")
        self.assertIsNotNone(entry)
        self.assertEqual(model_settings.provider_api_key(entry), "sk-secret-abcdef123456")

    def test_auth_enforced_when_token_set(self):
        with mock.patch.object(settings_api, "PILOT_AUTH_TOKEN", "sekret"):
            self.assertEqual(self.client.get("/api/settings/models").status_code, 401)
            ok = self.client.get(
                "/api/settings/models", headers={"Authorization": "Bearer sekret"}
            )
            self.assertEqual(ok.status_code, 200)
            ok2 = self.client.get(
                "/api/settings/models", headers={"X-Pilot-Token": "sekret"}
            )
            self.assertEqual(ok2.status_code, 200)

    def test_auth_enforced_on_put_available_models_and_test_provider(self):
        def tags(_request):
            return httpx.Response(200, json={"models": []})

        with mock.patch.object(settings_api, "PILOT_AUTH_TOKEN", "sekret"):
            self.assertEqual(401, self.client.put(
                "/api/settings/models", json=_provider_payload()
            ).status_code)
            self.assertEqual(401, self.client.get("/api/models/available").status_code)
            self.assertEqual(401, self.client.post(
                "/api/settings/test-provider", json={"provider": "ollama"}
            ).status_code)

            bearer = {"Authorization": "Bearer sekret"}
            saved = self.client.put(
                "/api/settings/models", json=_provider_payload(), headers=bearer
            )
            self.assertEqual(200, saved.status_code)

            with _loopback_runtime(), _capabilities({}), mock.patch.object(
                local_runtime, "client", _mock_transport_client(tags)
            ):
                available = self.client.get(
                    "/api/models/available", headers={"X-Pilot-Token": "sekret"}
                )
            self.assertEqual(200, available.status_code)
            # The saved cloud provider is reported without leaking its key.
            entry = available.json()["cloud"][0]
            self.assertEqual(("openai", True), (entry["id"], entry["has_key"]))

            _seen, patched = _mock_discover([])
            with patched:
                probe = self.client.post(
                    "/api/settings/test-provider",
                    json={"provider": "ollama"}, headers=bearer,
                )
            self.assertEqual(200, probe.status_code)

    def test_available_models_survives_ollama_down(self):
        async def boom(_base):
            return False, [], "ConnectError: down"

        with mock.patch.object(settings_api, "_fetch_ollama_models", boom):
            resp = self.client.get("/api/models/available")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["ollama"]["ok"])
        self.assertEqual(data["ollama"]["models"], [])

    def test_test_provider_requires_base_url(self):
        resp = self.client.post(
            "/api/settings/test-provider", json={"provider": "nonexistent"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])


class FetchOllamaModelsTests(unittest.TestCase):
    """The real _fetch_ollama_models; only the HTTP transport is faked."""

    def test_merges_live_tags_with_the_static_registry(self):
        seen = []

        def handler(request):
            seen.append((str(request.url), request.headers.get("Authorization")))
            return httpx.Response(200, json={"models": [
                {"name": "gemma4:12b", "size": 8000},
                {"name": "deepseek-r1:14b", "size": 9000},
                {"name": "mystery-model:7b", "size": 7},
                {"name": ""},        # nameless rows are skipped
                "not-a-dict",        # so are malformed ones
            ]})

        with _loopback_runtime(), _capabilities(
            {"gemma4:12b": {"declared_context": 65536, "tools": True}}
        ), mock.patch.object(local_runtime, "client", _mock_transport_client(handler)):
            ok, models, detail = asyncio.run(
                settings_api._fetch_ollama_models(_LOOPBACK_OLLAMA)
            )

        self.assertTrue(ok)
        self.assertEqual("3 modeller installerade", detail)
        # An uncredentialed runtime must not send an Authorization header.
        self.assertEqual([(f"{_LOOPBACK_OLLAMA}/api/tags", None)], seen)

        known, tool_less, unknown = models
        self.assertEqual(
            ("gemma4:12b", "Gemma 4 12B", True, True, 8000),
            (known["id"], known["label"], known["tools"],
             known["in_registry"], known["size"]),
        )
        self.assertEqual(settings_api.OLLAMA_MODELS["gemma4:12b"]["hint"], known["hint"])
        self.assertEqual(65536, known["declared_context"])
        self.assertTrue(known["capabilities"]["tools"])
        self.assertIn("classifier", known["effective_contexts"])
        # A registry entry that declares tools: False must not be defaulted true.
        self.assertEqual(
            ("DeepSeek-R1 14B", False, True),
            (tool_less["label"], tool_less["tools"], tool_less["in_registry"]),
        )
        # An unlisted model falls back to its own name, no hint, tools assumed.
        self.assertEqual(
            ("mystery-model:7b", "mystery-model:7b", "", True, False),
            (unknown["id"], unknown["label"], unknown["hint"],
             unknown["tools"], unknown["in_registry"]),
        )
        self.assertIsNone(unknown["declared_context"])
        self.assertFalse(unknown["capabilities"]["tools"])

    def test_sends_the_runtime_credential_when_one_is_configured(self):
        seen = []

        def handler(request):
            seen.append(request.headers.get("Authorization"))
            return httpx.Response(200, json={"models": []})

        with _loopback_runtime(api_key="runtime-secret"), _capabilities({}), \
                mock.patch.object(
                    local_runtime, "client", _mock_transport_client(handler)
                ):
            ok, models, detail = asyncio.run(
                settings_api._fetch_ollama_models(_LOOPBACK_OLLAMA)
            )

        self.assertEqual((True, [], "0 modeller installerade"), (ok, models, detail))
        self.assertEqual(["Bearer runtime-secret"], seen)

    def test_reports_the_exception_when_the_tags_call_raises(self):
        def refuse(_request):
            raise httpx.ConnectError("connection refused")

        with _loopback_runtime(), _capabilities({}), mock.patch.object(
            local_runtime, "client", _mock_transport_client(refuse)
        ):
            ok, models, detail = asyncio.run(
                settings_api._fetch_ollama_models(_LOOPBACK_OLLAMA)
            )

        self.assertEqual((False, []), (ok, models))
        self.assertEqual("ConnectError: connection refused", detail)

    def test_reports_the_exception_when_ollama_answers_with_an_error_status(self):
        def broken(_request):
            return httpx.Response(500, text="boom")

        with _loopback_runtime(), _capabilities({}), mock.patch.object(
            local_runtime, "client", _mock_transport_client(broken)
        ):
            ok, models, detail = asyncio.run(
                settings_api._fetch_ollama_models(_LOOPBACK_OLLAMA)
            )

        self.assertEqual((False, []), (ok, models))
        self.assertTrue(detail.startswith("HTTPStatusError:"), detail)

    def test_an_endpoint_outside_the_local_boundary_is_never_requested(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, json={"models": []})

        with _loopback_runtime(), _capabilities({}), mock.patch.object(
            local_runtime, "client", _mock_transport_client(handler)
        ):
            ok, models, detail = asyncio.run(
                settings_api._fetch_ollama_models("https://8.8.8.8:443")
            )

        self.assertEqual((False, []), (ok, models))
        self.assertEqual([], seen)
        self.assertTrue(detail.startswith("LocalRuntimeError:"), detail)


class OpenAiProviderProbeTests(unittest.TestCase):
    """The real _test_openai_provider; only the HTTP transport is faked."""

    def test_missing_key_is_reported_before_any_request(self):
        def handler(_request):
            raise AssertionError("no request may be made without an API key")

        with mock.patch.object(httpx, "AsyncClient", _mock_transport_client(handler)):
            result = asyncio.run(settings_api._test_openai_provider(
                "https://api.example.com/v1", ""
            ))

        self.assertEqual((False, "API-nyckel saknas"), result)

    def test_401_is_translated_to_an_invalid_key_message(self):
        def handler(_request):
            return httpx.Response(401, json={"error": "invalid_api_key"})

        with _public_dns(), mock.patch.object(
            httpx, "AsyncClient", _mock_transport_client(handler)
        ):
            result = asyncio.run(settings_api._test_openai_provider(
                "https://api.example.com/v1", "sk-bad"
            ))

        self.assertEqual((False, "401 — ogiltig API-nyckel"), result)

    def test_success_reports_the_model_count(self):
        seen = []

        def handler(request):
            seen.append((str(request.url), request.headers.get("Authorization")))
            return httpx.Response(200, json={"data": [
                {"id": "a"}, {"id": "b"}, {"id": "c"},
            ]})

        with _public_dns(), mock.patch.object(
            httpx, "AsyncClient", _mock_transport_client(handler)
        ):
            ok, detail = asyncio.run(settings_api._test_openai_provider(
                "https://api.example.com/v1/", "sk-good"
            ))

        self.assertTrue(ok)
        self.assertEqual("OK — 3 modeller tillgängliga", detail)
        self.assertEqual(
            [("https://api.example.com/v1/models", "Bearer sk-good")], seen
        )

    def test_transport_failure_is_reported_with_its_type(self):
        def handler(_request):
            raise httpx.ReadTimeout("timed out")

        with _public_dns(), mock.patch.object(
            httpx, "AsyncClient", _mock_transport_client(handler)
        ):
            result = asyncio.run(settings_api._test_openai_provider(
                "https://api.example.com/v1", "sk-good"
            ))

        self.assertEqual((False, "ReadTimeout: timed out"), result)


class ProviderProbeRouteTests(unittest.TestCase):
    """POST /api/settings/test-provider: local runtime and cloud branches."""

    def setUp(self):
        self.client = TestClient(_app())

    def test_invalid_runtime_kind_is_rejected_without_probing(self):
        seen, patched = _mock_discover(["gemma4:12b"])
        with patched:
            resp = self.client.post("/api/settings/test-provider", json={
                "provider": "ollama", "kind": "not-a-real-kind",
            })

        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {"ok": False, "detail": "invalid runtime kind", "models": []}, resp.json()
        )
        self.assertEqual([], seen)

    def test_local_probe_returns_the_discovered_models(self):
        seen, patched = _mock_discover(["gemma4:12b", "mystery-model:7b"])
        with patched:
            resp = self.client.post("/api/settings/test-provider", json={
                "provider": "local", "kind": "openai_compatible",
                "base_url": "http://127.0.0.1:1234/v1", "api_key": "runtime-secret",
            })

        self.assertEqual({
            "ok": True, "detail": "OK — 2 modeller tillgängliga",
            "models": ["gemma4:12b", "mystery-model:7b"],
        }, resp.json())
        # The unsaved form values are what gets probed, not the stored runtime.
        self.assertEqual(1, len(seen))
        self.assertEqual(
            ("openai_compatible", "http://127.0.0.1:1234/v1", "runtime-secret"),
            (seen[0].kind, seen[0].base_url, seen[0].api_key),
        )

    def test_local_probe_surfaces_the_runtime_error_code(self):
        seen, patched = _mock_discover(error=local_runtime.LocalRuntimeError(
            "unreachable", "Local runtime is unreachable"
        ))
        with patched:
            resp = self.client.post(
                "/api/settings/test-provider", json={"provider": "ollama"}
            )

        self.assertEqual({
            "ok": False, "detail": "unreachable: Local runtime is unreachable",
            "models": [],
        }, resp.json())
        self.assertEqual(1, len(seen))

    def test_cloud_probe_uses_the_stored_key_end_to_end(self):
        self.client.put("/api/settings/models", json=_provider_payload())
        seen = []

        def handler(request):
            seen.append((str(request.url), request.headers.get("Authorization")))
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})

        with _public_dns(), mock.patch.object(
            httpx, "AsyncClient", _mock_transport_client(handler)
        ):
            resp = self.client.post(
                "/api/settings/test-provider", json={"provider": "openai"}
            )

        self.assertEqual(
            {"ok": True, "detail": "OK — 1 modeller tillgängliga"}, resp.json()
        )
        self.assertEqual(
            [("https://api.openai.com/v1/models", "Bearer sk-secret-abcdef123456")],
            seen,
        )


if __name__ == "__main__":
    unittest.main()

"""REST settings API: auth, masking, save-and-reload, provider testing."""

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import model_settings
from api import settings as settings_api


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(settings_api.create_settings_router())
    return app


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


if __name__ == "__main__":
    unittest.main()

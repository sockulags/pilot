"""Model settings: persistence, validation, role resolution and provider views.

The contract under test is the settings feature's core promise:
- with no settings file, behaviour is exactly the env-driven stock behaviour;
- role assignments override per role; the default assignment runs everything
  else; cloud and local can mix; broken assignments fail closed to defaults.
"""

import json
import os
import unittest
from unittest import mock

import model_settings
from config import OLLAMA_MODEL


def _write_raw(payload: dict) -> None:
    os.makedirs(os.path.dirname(model_settings.MODEL_SETTINGS_FILE), exist_ok=True)
    with open(model_settings.MODEL_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    model_settings.reset_cache_for_tests()


def _settings_with_provider(roles: dict | None = None, **provider_overrides) -> dict:
    provider = {
        "id": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test-1234567890",
        "models": ["gpt-4o-mini"],
        "enabled": True,
    }
    provider.update(provider_overrides)
    return {"version": 1, "cloud_providers": [provider], "roles": roles or {}}


class CloudModelIdTests(unittest.TestCase):
    def test_roundtrip(self):
        cid = model_settings.cloud_model_id("openai", "gpt-4o-mini")
        self.assertEqual(cid, "cloud:openai:gpt-4o-mini")
        self.assertTrue(model_settings.is_cloud_model_id(cid))
        self.assertEqual(model_settings.parse_cloud_model_id(cid), ("openai", "gpt-4o-mini"))

    def test_ollama_ids_are_not_cloud(self):
        self.assertFalse(model_settings.is_cloud_model_id("gemma4:12b"))
        self.assertIsNone(model_settings.parse_cloud_model_id("gemma4:12b"))

    def test_malformed_cloud_id(self):
        self.assertIsNone(model_settings.parse_cloud_model_id("cloud:onlyprovider"))
        self.assertIsNone(model_settings.parse_cloud_model_id("cloud::model"))


class LoadDefaultsTests(unittest.TestCase):
    def test_no_file_gives_stock_behaviour(self):
        settings = model_settings.load_settings()
        self.assertEqual(settings["roles"], {})
        self.assertIsNone(model_settings.resolve_role_model("default_agent"))
        self.assertIsNone(model_settings.resolve_role_model("research_agent"))
        # Pipeline resolution falls back to env models.
        self.assertEqual(
            model_settings.resolve_pipeline_model("synthesis"), OLLAMA_MODEL
        )

    def test_corrupt_file_degrades_to_defaults(self):
        os.makedirs(os.path.dirname(model_settings.MODEL_SETTINGS_FILE), exist_ok=True)
        with open(model_settings.MODEL_SETTINGS_FILE, "w", encoding="utf-8") as f:
            f.write("{not json")
        model_settings.reset_cache_for_tests()
        settings = model_settings.load_settings()
        self.assertIn("roles", settings)
        self.assertIsNone(model_settings.resolve_role_model("default_agent"))

    def test_env_seeded_provider_not_used_until_persisted(self):
        # OPENAI_API_KEY in the env pre-populates the page but must not route.
        with mock.patch.object(model_settings, "OPENAI_API_KEY", "sk-env"):
            model_settings.reset_cache_for_tests()
            self.assertEqual(model_settings.enabled_cloud_providers(), [])


class ValidationTests(unittest.TestCase):
    def test_valid_roundtrip(self):
        saved, errors = model_settings.save_settings(
            _settings_with_provider(roles={
                "default_agent": {"provider": "ollama", "model": "gemma4:12b"},
                "research_agent": {"provider": "openai", "model": "gpt-4o-mini"},
            })
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            saved["roles"]["research_agent"], {"provider": "openai", "model": "gpt-4o-mini"}
        )
        # Reload from disk sees the same thing.
        model_settings.reset_cache_for_tests()
        self.assertEqual(
            model_settings.resolve_role_model("research_agent"),
            "cloud:openai:gpt-4o-mini",
        )

    def test_unknown_role_is_dropped_with_error(self):
        saved, errors = model_settings.save_settings(
            _settings_with_provider(roles={"nonsense_role": {"provider": "ollama", "model": "x"}})
        )
        self.assertTrue(errors)
        self.assertNotIn("nonsense_role", saved.get("roles", {}))

    def test_role_pointing_at_unknown_provider_is_rejected(self):
        settings = _settings_with_provider(
            roles={"research_agent": {"provider": "missing", "model": "m"}}
        )
        coerced, errors = model_settings.validate_settings(settings)
        self.assertTrue(any("missing" in e for e in errors))
        self.assertNotIn("research_agent", coerced["roles"])

    def test_local_only_role_cannot_go_cloud(self):
        settings = _settings_with_provider(
            roles={"vision_agent": {"provider": "openai", "model": "gpt-4o"}}
        )
        coerced, errors = model_settings.validate_settings(settings)
        self.assertTrue(any("vision_agent" in e for e in errors))
        self.assertNotIn("vision_agent", coerced["roles"])

    def test_provider_id_ollama_is_reserved(self):
        settings = _settings_with_provider(id="ollama")
        coerced, errors = model_settings.validate_settings(settings)
        self.assertTrue(errors)
        self.assertEqual(coerced["cloud_providers"], [])

    def test_bad_base_url_rejected(self):
        settings = _settings_with_provider(base_url="not-a-url")
        _coerced, errors = model_settings.validate_settings(settings)
        self.assertTrue(any("base_url" in e for e in errors))

    def test_invalid_settings_do_not_overwrite_stored(self):
        model_settings.save_settings(_settings_with_provider())
        _saved, errors = model_settings.save_settings("garbage")  # type: ignore[arg-type]
        self.assertTrue(errors)
        model_settings.reset_cache_for_tests()
        self.assertEqual(len(model_settings.load_settings()["cloud_providers"]), 1)


class ResolutionTests(unittest.TestCase):
    def test_role_beats_default_beats_env(self):
        model_settings.save_settings(
            _settings_with_provider(roles={
                "default_agent": {"provider": "openai", "model": "gpt-4o-mini"},
                "code_agent": {"provider": "ollama", "model": "devstral:latest"},
            })
        )
        # Explicit role assignment wins.
        self.assertEqual(model_settings.resolve_role_model("code_agent"), "devstral:latest")
        # Unassigned pipeline role inherits the default assignment.
        self.assertEqual(
            model_settings.resolve_pipeline_model("classifier"), "cloud:openai:gpt-4o-mini"
        )

    def test_disabled_provider_fails_closed(self):
        model_settings.save_settings(
            _settings_with_provider(
                enabled=False,
                roles={"research_agent": {"provider": "openai", "model": "gpt-4o-mini"}},
            )
        )
        self.assertIsNone(model_settings.resolve_role_model("research_agent"))

    def test_provider_without_key_fails_closed(self):
        model_settings.save_settings(
            _settings_with_provider(
                api_key="",
                roles={"research_agent": {"provider": "openai", "model": "gpt-4o-mini"}},
            )
        )
        self.assertIsNone(model_settings.resolve_role_model("research_agent"))

    def test_api_key_env_fallback(self):
        model_settings.save_settings(
            _settings_with_provider(api_key="", api_key_env="TEST_PILOT_KEY")
        )
        with mock.patch.dict(os.environ, {"TEST_PILOT_KEY": "sk-from-env"}):
            entry = model_settings.cloud_provider("openai")
            self.assertIsNotNone(entry)
            self.assertEqual(model_settings.provider_api_key(entry), "sk-from-env")


class MaskingTests(unittest.TestCase):
    def test_masked_settings_never_contain_keys(self):
        model_settings.save_settings(_settings_with_provider())
        masked = model_settings.masked_settings()
        entry = masked["cloud_providers"][0]
        self.assertEqual(entry["api_key"], "")
        self.assertTrue(entry["has_key"])
        self.assertEqual(entry["key_hint"], "…7890")

    def test_apply_client_update_keeps_stored_key(self):
        model_settings.save_settings(_settings_with_provider())
        client_payload = _settings_with_provider(api_key="")
        merged = model_settings.apply_client_update(client_payload)
        self.assertEqual(
            merged["cloud_providers"][0]["api_key"], "sk-test-1234567890"
        )

    def test_apply_client_update_accepts_new_key(self):
        model_settings.save_settings(_settings_with_provider())
        client_payload = _settings_with_provider(api_key="sk-new-key-000")
        merged = model_settings.apply_client_update(client_payload)
        self.assertEqual(merged["cloud_providers"][0]["api_key"], "sk-new-key-000")


class PersistedGateTests(unittest.TestCase):
    def test_enabled_cloud_providers_requires_saved_file(self):
        self.assertFalse(model_settings.settings_persisted())
        self.assertEqual(model_settings.enabled_cloud_providers(), [])
        model_settings.save_settings(_settings_with_provider())
        self.assertTrue(model_settings.settings_persisted())
        self.assertEqual(len(model_settings.enabled_cloud_providers()), 1)


if __name__ == "__main__":
    unittest.main()

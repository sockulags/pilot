"""Provider layer: role-aware and cloud-id routing.

Verifies the precedence contract documented in agents/providers.py:
run-level backend override (eval) > cloud model id > role assignment >
default_agent assignment > caller's model > env — and that each path actually
hits the right HTTP endpoint with the right credentials.
"""

import asyncio
import unittest
from unittest import mock

import httpx

import model_settings
from agents import providers


def _settings(roles: dict | None = None) -> dict:
    return {
        "version": 1,
        "cloud_providers": [{
            "id": "cloudx",
            "label": "CloudX",
            "base_url": "https://cloudx.example/v1",
            "api_key": "sk-cloudx",
            "models": ["big-model"],
            "enabled": True,
        }],
        "roles": roles or {},
    }


class _Recorder:
    """Patch httpx.AsyncClient.post and record every request."""

    def __init__(self, response_payload: dict):
        self.calls: list[tuple[str, dict, dict]] = []
        self._payload = response_payload

    async def post(self, url, json=None, headers=None, **kwargs):  # noqa: A002
        self.calls.append((url, json or {}, headers or {}))
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=self._payload, request=request)


def _run_chat_once(recorder, **kwargs):
    async def go():
        with mock.patch.object(httpx, "AsyncClient") as client_cls:
            instance = client_cls.return_value
            instance.__aenter__ = mock.AsyncMock(return_value=recorder)
            instance.__aexit__ = mock.AsyncMock(return_value=False)
            return await providers.chat_once([{"role": "user", "content": "hi"}], **kwargs)
    return asyncio.run(go())


_OLLAMA_RESPONSE = {"message": {"content": "ok"}}
_OPENAI_RESPONSE = {"choices": [{"message": {"content": "ok"}}], "usage": {}}


class RoleRoutingTests(unittest.TestCase):
    def setUp(self):
        providers.set_backend(None)

    def tearDown(self):
        providers.set_backend(None)

    def test_no_settings_goes_to_ollama_unchanged(self):
        recorder = _Recorder(_OLLAMA_RESPONSE)
        _run_chat_once(recorder, model="gemma4:12b", role="classifier")
        url, payload, _ = recorder.calls[0]
        self.assertIn("/api/chat", url)
        self.assertEqual(payload["model"], "gemma4:12b")

    def test_cloud_model_id_routes_to_provider(self):
        model_settings.save_settings(_settings())
        recorder = _Recorder(_OPENAI_RESPONSE)
        _run_chat_once(recorder, model="cloud:cloudx:big-model")
        url, payload, headers = recorder.calls[0]
        self.assertEqual(url, "https://cloudx.example/v1/chat/completions")
        self.assertEqual(payload["model"], "big-model")
        self.assertEqual(headers["Authorization"], "Bearer sk-cloudx")

    def test_role_assignment_overrides_caller_model(self):
        model_settings.save_settings(
            _settings(roles={"synthesis": {"provider": "cloudx", "model": "big-model"}})
        )
        recorder = _Recorder(_OPENAI_RESPONSE)
        _run_chat_once(recorder, model="gemma4:12b", role="synthesis")
        url, payload, _ = recorder.calls[0]
        self.assertIn("cloudx.example", url)
        self.assertEqual(payload["model"], "big-model")

    def test_default_assignment_runs_everything(self):
        model_settings.save_settings(
            _settings(roles={"default_agent": {"provider": "cloudx", "model": "big-model"}})
        )
        recorder = _Recorder(_OPENAI_RESPONSE)
        _run_chat_once(recorder, model="gemma4:12b", role="gateway")
        url, _, _ = recorder.calls[0]
        self.assertIn("cloudx.example", url)

    def test_unroled_call_ignores_role_assignments(self):
        model_settings.save_settings(
            _settings(roles={"default_agent": {"provider": "cloudx", "model": "big-model"}})
        )
        recorder = _Recorder(_OLLAMA_RESPONSE)
        _run_chat_once(recorder, model="gemma4:12b")  # no role tag
        url, _, _ = recorder.calls[0]
        self.assertIn("/api/chat", url)

    def test_cloud_id_with_missing_provider_falls_back_to_ollama(self):
        recorder = _Recorder(_OLLAMA_RESPONSE)
        _run_chat_once(recorder, model="cloud:nothere:some-model")
        url, payload, _ = recorder.calls[0]
        self.assertIn("/api/chat", url)
        # Falls back to the local default, never sends the raw cloud id to Ollama.
        self.assertNotIn("cloud:", payload["model"])

    def test_eval_backend_override_ignores_roles_and_cloud_ids(self):
        model_settings.save_settings(
            _settings(roles={"synthesis": {"provider": "cloudx", "model": "big-model"}})
        )
        providers.set_backend("ollama")
        recorder = _Recorder(_OLLAMA_RESPONSE)
        _run_chat_once(recorder, model="cloud:cloudx:big-model", role="synthesis")
        url, payload, _ = recorder.calls[0]
        self.assertIn("/api/chat", url)
        self.assertNotIn("cloud:", payload["model"])

    def test_custom_ollama_base_url_from_settings(self):
        settings = _settings()
        settings["ollama"] = {"base_url": "http://lan-box:11434"}
        model_settings.save_settings(settings)
        recorder = _Recorder(_OLLAMA_RESPONSE)
        _run_chat_once(recorder, model="gemma4:12b")
        url, _, _ = recorder.calls[0]
        self.assertTrue(url.startswith("http://lan-box:11434/"))


class ApplyRoleTests(unittest.TestCase):
    def test_apply_role_without_role_is_identity(self):
        self.assertEqual(providers.apply_role("m", None), "m")

    def test_apply_role_prefers_specific_assignment(self):
        model_settings.save_settings(_settings(roles={
            "default_agent": {"provider": "ollama", "model": "gemma4:12b"},
            "classifier": {"provider": "cloudx", "model": "big-model"},
        }))
        self.assertEqual(
            providers.apply_role("whatever", "classifier"), "cloud:cloudx:big-model"
        )
        self.assertEqual(providers.apply_role("whatever", "gateway"), "gemma4:12b")


if __name__ == "__main__":
    unittest.main()

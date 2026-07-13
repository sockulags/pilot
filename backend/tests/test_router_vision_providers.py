"""Router and vision calls route through the provider layer / honour a custom
Ollama URL — no direct hardcoded-OLLAMA_BASE_URL POSTs remain.

Two contracts:
  * route_next_action goes through providers.chat_once, so per-role model
    settings and the OpenAI/cloud backend reach router decisions too.
  * The vision calls stay LOCAL by design (raw screenshots must never hit a
    cloud provider) but DO honour model_settings.ollama_base_url().
"""

import asyncio
import json
import unittest
from unittest import mock

import httpx

import model_settings
from agents import providers, router, vision
from agents.context_manager import ContextBudgetError
from agents.context_telemetry import build_context_telemetry


class _Recorder:
    """Patch httpx.AsyncClient.post and record every request."""

    def __init__(self, response_payload: dict):
        self.calls: list[tuple[str, dict, dict]] = []
        self._payload = response_payload

    async def post(self, url, json=None, headers=None, **kwargs):  # noqa: A002
        self.calls.append((url, json or {}, headers or {}))
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=self._payload, request=request)


def _patched_client(recorder):
    """Context manager patching httpx.AsyncClient to yield ``recorder``."""
    client_cls = mock.patch.object(httpx, "AsyncClient").start()
    instance = client_cls.return_value
    instance.__aenter__ = mock.AsyncMock(return_value=recorder)
    instance.__aexit__ = mock.AsyncMock(return_value=False)
    return client_cls


_OLLAMA_ROUTER_RESPONSE = {
    "message": {"content": '{"tool": "done", "args": {"summary": "ok"}, "thinking": "t"}'}
}
_OPENAI_ROUTER_RESPONSE = {
    "choices": [{"message": {"content": '{"tool": "done", "args": {"summary": "ok"}, "thinking": "t"}'}}],
    "usage": {},
}
_VISION_RESPONSE = {"message": {"content": "a login screen"}}


class RouterProviderTests(unittest.TestCase):
    def setUp(self):
        providers.set_backend(None)
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: providers.set_backend(None))

    def _route(self, recorder, **kwargs):
        _patched_client(recorder)
        return asyncio.run(router.route_next_action("do a thing", [], **kwargs))

    def test_route_goes_through_provider_to_ollama(self):
        recorder = _Recorder(_OLLAMA_ROUTER_RESPONSE)
        decision = self._route(recorder, model="gemma4:12b")
        url, payload, _ = recorder.calls[0]
        self.assertIn("/api/chat", url)
        self.assertEqual(payload["model"], "gemma4:12b")
        self.assertEqual(payload["options"]["temperature"], 0.1)
        self.assertEqual(decision["tool"], "done")

    def test_route_honours_cloud_model_id(self):
        # A cloud model id passed as the router model resolves to that provider,
        # proving the call flows through the provider layer (the router has no
        # dedicated role, but cloud ids and the backend override still route it).
        model_settings.save_settings({
            "version": 1,
            "cloud_providers": [{
                "id": "cloudx", "label": "CloudX",
                "base_url": "https://cloudx.example/v1", "api_key": "sk-cloudx",
                "models": ["big-model"], "enabled": True,
            }],
        })
        recorder = _Recorder(_OPENAI_ROUTER_RESPONSE)
        decision = self._route(recorder, model="cloud:cloudx:big-model")
        url, payload, headers = recorder.calls[0]
        self.assertEqual(url, "https://cloudx.example/v1/chat/completions")
        self.assertEqual(payload["model"], "big-model")
        self.assertEqual(headers["Authorization"], "Bearer sk-cloudx")
        self.assertEqual(decision["tool"], "done")

    def test_route_honours_backend_override(self):
        # The eval run-level backend override reaches router decisions too.
        providers.set_backend("openai")
        with mock.patch.object(providers, "OPENAI_API_KEY", "sk-env"):
            recorder = _Recorder(_OPENAI_ROUTER_RESPONSE)
            decision = self._route(recorder, model="gemma4:12b")
        url, _, _ = recorder.calls[0]
        self.assertIn("/chat/completions", url)
        self.assertEqual(decision["tool"], "done")

    def test_route_honours_custom_ollama_url(self):
        model_settings.save_settings({
            "version": 1, "ollama": {"base_url": "http://lan-box:11434"},
        })
        recorder = _Recorder(_OLLAMA_ROUTER_RESPONSE)
        self._route(recorder, model="gemma4:12b")
        url, _, _ = recorder.calls[0]
        self.assertTrue(url.startswith("http://lan-box:11434/"))


class VisionStaysLocalTests(unittest.TestCase):
    """Perception must never leave the machine: even with a cloud default_agent
    assignment, the vision calls stay on Ollama, only honouring a custom URL."""

    def setUp(self):
        self.addCleanup(mock.patch.stopall)

    def _cloud_default_settings(self, ollama_url: str | None = None) -> dict:
        settings = {
            "version": 1,
            "cloud_providers": [{
                "id": "cloudx", "label": "CloudX",
                "base_url": "https://cloudx.example/v1", "api_key": "sk-cloudx",
                "models": ["big-model"], "enabled": True,
            }],
            "roles": {"default_agent": {"provider": "cloudx", "model": "big-model"}},
        }
        if ollama_url:
            settings["ollama"] = {"base_url": ollama_url}
        return settings

    def test_analyze_screenshot_never_hits_cloud(self):
        model_settings.save_settings(self._cloud_default_settings("http://lan-box:11434"))
        recorder = _Recorder(_VISION_RESPONSE)
        _patched_client(recorder)
        out = asyncio.run(router.analyze_screenshot("task", "b64img", []))
        url, payload, _ = recorder.calls[0]
        self.assertNotIn("cloudx", url)
        self.assertTrue(url.startswith("http://lan-box:11434/"))
        self.assertIn("/api/chat", url)
        # Ollama image format: base64 lives on the user message, not top-level.
        self.assertEqual(payload["messages"][-1]["images"], ["b64img"])
        self.assertFalse(payload["think"])
        self.assertGreaterEqual(payload["options"]["num_ctx"], 8192)
        self.assertEqual(out, "a login screen")

    def test_screenshot_request_records_safe_media_telemetry_and_actual_usage(self):
        recorder = _Recorder({
            "message": {"content": "a login screen"},
            "prompt_eval_count": 4310,
            "eval_count": 24,
        })
        _patched_client(recorder)
        providers.reset_usage()

        out = asyncio.run(router.analyze_screenshot("inspect login", "private-b64", []))
        telemetry = build_context_telemetry(providers.get_context_reports())

        self.assertEqual(out, "a login screen")
        self.assertIsNotNone(telemetry)
        call = telemetry["calls"][0]
        self.assertEqual(call["model"], router.OLLAMA_VISION_MODEL)
        self.assertEqual(call["context_role"], "vision")
        self.assertEqual(call["effective_limit"], recorder.calls[0][1]["options"]["num_ctx"])
        self.assertEqual(call["categories"]["media"], 4096)
        self.assertEqual(call["actual_prompt_tokens"], 4310)
        self.assertEqual(call["actual_completion_tokens"], 24)
        self.assertNotIn("private-b64", json.dumps(telemetry))
        self.assertTrue(recorder.calls[0][0].startswith("http://"))

    def test_analyze_screenshot_rejects_empty_visible_answer(self):
        recorder = _Recorder({
            "message": {"content": "", "thinking": "I can see the desktop"},
            "done_reason": "length",
        })
        _patched_client(recorder)

        with self.assertRaisesRegex(RuntimeError, "empty visual description"):
            asyncio.run(router.analyze_screenshot("task", "b64img", []))

    def test_analyze_screenshot_uses_live_declared_context_limit(self):
        from agents import model_inventory

        recorder = _Recorder(_VISION_RESPONSE)
        _patched_client(recorder)
        with mock.patch.dict(
            model_inventory._DISCOVERED_CONTEXTS,
            {router.OLLAMA_VISION_MODEL: 6000}, clear=True,
        ):
            asyncio.run(router.analyze_screenshot("task", "b64img", []))
        self.assertEqual(recorder.calls[0][1]["options"]["num_ctx"], 6000)

    def test_direct_vision_overflow_retries_once_without_reentering_agent_loop(self):
        class OverflowRecorder(_Recorder):
            async def post(self, url, json=None, headers=None, **kwargs):
                self.calls.append((url, json or {}, headers or {}))
                request = httpx.Request("POST", url)
                if len(self.calls) == 1:
                    return httpx.Response(
                        400, text="prompt exceeds context size", request=request,
                    )
                return httpx.Response(200, json=_VISION_RESPONSE, request=request)

        recorder = OverflowRecorder(_VISION_RESPONSE)
        _patched_client(recorder)
        providers.reset_usage()
        out = asyncio.run(router.analyze_screenshot("task", "b64img", []))
        self.assertEqual(out, "a login screen")
        self.assertEqual(len(recorder.calls), 2)
        reports = providers.get_context_reports()
        self.assertEqual(len(reports), 2)
        self.assertFalse(reports[0].retry)
        self.assertTrue(reports[1].retry)
        self.assertTrue(all(report.context_role == "vision" for report in reports))

    def test_custom_unknown_vision_model_uses_safe_startup_context(self):
        recorder = _Recorder(_VISION_RESPONSE)
        _patched_client(recorder)
        with mock.patch.object(router, "OLLAMA_VISION_MODEL", "custom-vision:model"):
            with self.assertRaises(ContextBudgetError):
                asyncio.run(router.analyze_screenshot("task", "b64img", []))
        self.assertEqual(recorder.calls, [])

    def test_vision_done_summary_never_hits_cloud(self):
        model_settings.save_settings(self._cloud_default_settings("http://lan-box:11434"))
        recorder = _Recorder(_VISION_RESPONSE)
        _patched_client(recorder)
        out = asyncio.run(router.vision_done_summary("task", "b64img"))
        url, payload, _ = recorder.calls[0]
        self.assertNotIn("cloudx", url)
        self.assertTrue(url.startswith("http://lan-box:11434/"))
        self.assertEqual(payload["messages"][-1]["images"], ["b64img"])
        self.assertGreaterEqual(payload["options"]["num_ctx"], 8192)
        self.assertEqual(out, "a login screen")

    def test_validate_vision_model_honours_custom_url(self):
        model_settings.save_settings(self._cloud_default_settings("http://lan-box:11434"))
        recorder = _Recorder({"message": {"content": "OK"}})
        _patched_client(recorder)
        ok, _msg = asyncio.run(vision.validate_vision_model())
        url, payload, _ = recorder.calls[0]
        self.assertTrue(ok)
        self.assertNotIn("cloudx", url)
        self.assertTrue(url.startswith("http://lan-box:11434/"))
        self.assertFalse(payload["think"])
        self.assertGreaterEqual(payload["options"]["num_ctx"], 8192)

    def test_validate_vision_model_rejects_empty_visible_answer(self):
        recorder = _Recorder({"message": {"content": "", "thinking": "OK"}})
        _patched_client(recorder)

        ok, msg = asyncio.run(vision.validate_vision_model())

        self.assertFalse(ok)
        self.assertIn("empty response", msg)

    def test_unknown_vision_model_preflight_failure_is_actionable_health_result(self):
        recorder = _Recorder({"message": {"content": "OK"}})
        _patched_client(recorder)

        with mock.patch.object(vision, "OLLAMA_VISION_MODEL", "custom-vision:model"):
            ok, msg = asyncio.run(vision.validate_vision_model())

        self.assertFalse(ok)
        self.assertIn("custom-vision:model", msg)
        self.assertIn("mandatory context requires", msg)
        self.assertIn("ollama pull llama3.2-vision:11b", msg)
        self.assertNotIn("\n", msg)
        self.assertEqual(recorder.calls, [])


if __name__ == "__main__":
    unittest.main()

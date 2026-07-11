import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class _Resp:
    def __init__(self, payload=None, raise_status=False):
        self._payload = payload if payload is not None else {}
        self._raise_status = raise_status

    def raise_for_status(self):
        if self._raise_status:
            raise RuntimeError("HTTP 500")

    def json(self):
        return self._payload


class _Client:
    """Minimal async httpx.AsyncClient stand-in: get() returns a queued response
    or raises a queued error."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        if self._error is not None:
            raise self._error
        return self._response

    async def post(self, url, json=None):
        if self._error is not None:
            raise self._error
        return self._response


def _patch_httpx(module, response=None, error=None):
    def factory(*args, **kwargs):
        return _Client(response=response, error=error)

    return mock.patch.object(module.httpx, "AsyncClient", factory)


class ModelInventoryTests(unittest.TestCase):
    def tearDown(self):
        from agents import model_inventory as mi
        mi._DISCOVERED_CONTEXTS.clear()

    def test_show_metadata_discovers_declared_context_and_capabilities(self):
        from agents import model_inventory as mi

        tags = _Resp({"models": [{"name": "gemma4:12b"}]})
        show = _Resp({
            "model_info": {"gemma4.context_length": 65536},
            "capabilities": ["completion", "tools", "thinking"],
        })

        class Client(_Client):
            async def get(self, url):
                return tags
            async def post(self, url, json=None):
                return show

        with mock.patch.object(mi.httpx, "AsyncClient", lambda *a, **k: Client()):
            inv = asyncio.run(mi.get_model_inventory())

        caps = inv.capabilities["gemma4:12b"]
        self.assertEqual(65536, caps.declared_context)
        self.assertTrue(caps.tools)
        self.assertTrue(caps.thinking)
        self.assertEqual(65536, mi.declared_context_for("gemma4:12b"))

    def test_lower_show_limit_becomes_request_time_authority(self):
        from agents import model_inventory as mi

        tags = _Resp({"models": [{"name": "gemma4:12b"}]})
        show = _Resp({
            "model_info": {"gemma4.context_length": 2048},
            "capabilities": ["completion", "tools"],
        })

        class Client(_Client):
            async def get(self, url): return tags
            async def post(self, url, json=None): return show

        with mock.patch.object(mi.httpx, "AsyncClient", lambda *a, **k: Client()):
            asyncio.run(mi.get_model_inventory())

        self.assertEqual(2048, mi.resolve_context_budget("gemma4:12b", "synthesis"))

    def test_failed_refresh_atomically_removes_stale_live_authority(self):
        from agents import model_inventory as mi

        mi._DISCOVERED_CONTEXTS = {"custom:model": 65536, "removed:model": 65536}
        with _patch_httpx(mi, error=RuntimeError("show unavailable")):
            result = asyncio.run(mi.discover_model_capabilities({"custom:model"}))

        self.assertEqual({}, result)
        self.assertEqual({}, mi._DISCOVERED_CONTEXTS)
        self.assertEqual(4096, mi.resolve_context_budget("custom:model", "synthesis"))

    def test_tags_failure_clears_prior_authority(self):
        from agents import model_inventory as mi

        mi._DISCOVERED_CONTEXTS = {"custom:model": 65536}
        with _patch_httpx(mi, error=RuntimeError("ollama down")):
            inv = asyncio.run(mi.get_model_inventory())

        self.assertFalse(inv.discovery_ok)
        self.assertEqual({}, mi._DISCOVERED_CONTEXTS)

    def test_unknown_model_uses_safe_baseline_until_discovered(self):
        from agents import model_inventory as mi

        self.assertEqual(4096, mi.resolve_context_budget("custom:model", "code_agent"))
        self.assertEqual(4096, mi.resolve_context_budget("custom:model", "synthesis"))
        mi._DISCOVERED_CONTEXTS = {"custom:model": 12000}
        self.assertEqual(12000, mi.resolve_context_budget("custom:model", "synthesis"))
        self.assertEqual(12000, mi.resolve_context_budget("custom:model", "code_agent"))
    def test_inventory_exposes_declared_effective_limits_and_capabilities(self):
        from agents import model_inventory as mi

        inv = mi.build_inventory({"gemma4:12b", "qwen3.5:9b"})

        gemma = inv.capabilities["gemma4:12b"]
        self.assertEqual(262144, gemma.declared_context)
        self.assertEqual(8192, gemma.effective_context)
        self.assertEqual(4096, gemma.effective_contexts["classifier"])
        self.assertEqual(16384, gemma.effective_contexts["synthesis"])
        self.assertTrue(gemma.tools)
        self.assertFalse(gemma.embedding)
        vision = inv.capabilities["qwen3.5:9b"]
        self.assertTrue(vision.vision)
        self.assertTrue(vision.thinking)

    def test_runtime_budget_resolution_clamps_to_declared_maximum(self):
        from agents.model_inventory import resolve_context_budget

        self.assertEqual(2048, resolve_context_budget("tiny", "classifier", declared_max=2048))

    def test_discovery_success_classifies_installed_tools_and_vision(self):
        from agents import model_inventory as mi

        payload = {
            "models": [
                {"name": "gemma4:12b"},
                {"name": "qwen3.5:9b"},
                {"name": "deepseek-r1:14b"},
                {"name": "some-unconfigured:7b"},  # installed but not in registry
            ]
        }
        with _patch_httpx(mi, response=_Resp(payload)):
            inv = asyncio.run(mi.get_model_inventory())

        self.assertTrue(inv.discovery_ok)
        # Only registry models count as installed/healthy.
        self.assertIn("gemma4:12b", inv.healthy)
        self.assertIn("qwen3.5:9b", inv.healthy)
        self.assertNotIn("some-unconfigured:7b", inv.installed)
        # tools_capable reflects the registry tools flag among healthy models.
        self.assertIn("gemma4:12b", inv.tools_capable)
        self.assertIn("qwen3.5:9b", inv.tools_capable)
        self.assertNotIn("deepseek-r1:14b", inv.tools_capable)  # tools=False
        # vision model is installed -> vision_capable.
        self.assertEqual({"qwen3.5:9b"}, set(inv.vision_capable))

    def test_discovery_failure_fails_closed(self):
        from agents import model_inventory as mi

        with _patch_httpx(mi, error=RuntimeError("connection refused")):
            inv = asyncio.run(mi.get_model_inventory())

        self.assertFalse(inv.discovery_ok)
        self.assertEqual(frozenset(), inv.healthy)
        self.assertEqual(frozenset(), inv.installed)
        self.assertEqual(frozenset(), inv.tools_capable)
        self.assertEqual(frozenset(), inv.vision_capable)
        # configured is still known even when discovery fails.
        self.assertIn("gemma4:12b", inv.configured)

    def test_empty_tags_fails_closed(self):
        from agents import model_inventory as mi

        with _patch_httpx(mi, response=_Resp({"models": []})):
            inv = asyncio.run(mi.get_model_inventory())

        self.assertFalse(inv.discovery_ok)
        self.assertEqual(frozenset(), inv.healthy)

    def test_http_error_fails_closed(self):
        from agents import model_inventory as mi

        with _patch_httpx(mi, response=_Resp({"models": [{"name": "gemma4:12b"}]}, raise_status=True)):
            inv = asyncio.run(mi.get_model_inventory())

        self.assertFalse(inv.discovery_ok)
        self.assertEqual(frozenset(), inv.healthy)

    def test_vision_model_not_installed_is_not_vision_capable(self):
        from agents import model_inventory as mi

        # gemma installed, vision model (qwen3.5:9b) absent.
        inv = mi.build_inventory({"gemma4:12b"})
        self.assertTrue(inv.discovery_ok)
        self.assertEqual(frozenset(), inv.vision_capable)
        self.assertIn("gemma4:12b", inv.healthy)


class CoordinatorExpertInventoryTests(unittest.TestCase):
    def test_available_expert_models_uses_inventory_minus_coordinator(self):
        from agents import coordinator
        from agents.model_inventory import build_inventory

        inv = build_inventory({"gemma4:12b", "qwen3.5:9b", "gpt-oss:20b"})
        experts = asyncio.run(
            coordinator.available_expert_models("gemma4:12b", inventory=inv)
        )
        self.assertNotIn("gemma4:12b", experts)  # coordinator excluded
        self.assertIn("qwen3.5:9b", experts)
        self.assertIn("gpt-oss:20b", experts)

    def test_available_expert_models_fails_closed_when_discovery_fails(self):
        """The key acceptance test: if /api/tags fails, no experts are advertised."""
        from agents import coordinator
        from agents import model_inventory as mi

        with _patch_httpx(mi, error=RuntimeError("ollama down")):
            experts = asyncio.run(
                coordinator.available_expert_models("gemma4:12b")
            )

        self.assertEqual({}, experts)

    def test_available_expert_models_does_not_return_full_registry_on_failure(self):
        from agents import coordinator
        from agents import model_inventory as mi
        from config import OLLAMA_MODELS

        with _patch_httpx(mi, error=RuntimeError("ollama down")):
            experts = asyncio.run(
                coordinator.available_expert_models("nonexistent-coordinator")
            )

        # Fail-open would have returned (almost) the whole registry here.
        self.assertEqual(0, len(experts))
        self.assertNotEqual(set(OLLAMA_MODELS), set(experts))


class RoutingInventoryTests(unittest.TestCase):
    def test_router_model_unavailable_falls_back_with_reason(self):
        from agents import turn_policy
        from agents.turn_policy import TaskContext, select_agent_for_intent

        ctx = TaskContext(intent="chat")
        with mock.patch.object(turn_policy, "AGENT_ROLE_MODELS", {
            "default_agent": "gemma4:12b",
        }):
            # The default/router model itself is not in the healthy set.
            selected = select_agent_for_intent(
                "auto", ctx, available_models=set()
            )
        # Falls back to the configured OLLAMA_MODEL with a recorded reason.
        self.assertIsNotNone(selected.fallback_reason)
        self.assertEqual(turn_policy.OLLAMA_MODEL, selected.model)

    def test_expert_role_model_not_installed_is_not_selected(self):
        from agents import turn_policy
        from agents.turn_policy import TaskContext, select_agent_for_intent

        ctx = TaskContext(intent="research")
        with mock.patch.object(turn_policy, "AGENT_ROLE_MODELS", {
            "default_agent": "gemma4:12b",
            "research_agent": "gpt-oss:20b",
        }):
            # research model configured but NOT installed; default is installed.
            selected = select_agent_for_intent(
                "auto", ctx, available_models={"gemma4:12b"}
            )
        self.assertEqual("research_agent", selected.role)
        self.assertEqual("gemma4:12b", selected.model)  # not gpt-oss:20b
        self.assertIn("gpt-oss:20b", selected.fallback_reason)

    def test_vision_role_model_unavailable_falls_back(self):
        from agents import turn_policy
        from agents.turn_policy import TaskContext, select_agent_for_intent

        ctx = TaskContext(intent="vision")
        with mock.patch.object(turn_policy, "AGENT_ROLE_MODELS", {
            "default_agent": "gemma4:12b",
            "vision_agent": "qwen3.5:9b",
        }):
            # vision model not installed; only the default is healthy.
            selected = select_agent_for_intent(
                "auto", ctx, available_models={"gemma4:12b"}
            )
        self.assertEqual("vision_agent", selected.role)
        self.assertEqual("gemma4:12b", selected.model)
        self.assertIn("qwen3.5:9b", selected.fallback_reason)

    def test_default_is_fail_closed_not_full_registry(self):
        """When available_models is None, routing must NOT treat the whole
        configured registry as available."""
        from agents import turn_policy
        from agents.turn_policy import TaskContext, select_agent_for_intent

        ctx = TaskContext(intent="research")
        with mock.patch.object(turn_policy, "AGENT_ROLE_MODELS", {
            "default_agent": "gemma4:12b",
            "research_agent": "gpt-oss:20b",
        }):
            # No available set passed -> fail closed to the safe default model.
            selected = select_agent_for_intent("auto", ctx)
        # research_agent (gpt-oss:20b) is configured but unverified, so it is
        # NOT selected; routing falls back to the default model.
        self.assertEqual("gemma4:12b", selected.model)
        self.assertIsNotNone(selected.fallback_reason)


if __name__ == "__main__":
    unittest.main()

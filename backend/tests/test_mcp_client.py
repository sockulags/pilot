import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mcp_client import MCPManager, MCPServerConfig
from tools import registry

_SERVER = os.path.join(os.path.dirname(__file__), "_mcp_echo_server.py")


class MCPClientIntegrationTests(unittest.TestCase):
    """Verify the MCP client end-to-end against a real (in-process) MCP server:
    discover tools -> register into the registry -> call -> get the result back."""

    def tearDown(self):
        registry.clear_external()

    def test_discovers_registers_and_calls_tool(self):
        asyncio.run(self._roundtrip())

    async def _roundtrip(self):
        manager = MCPManager()
        config = MCPServerConfig(name="test", command=sys.executable, args=[_SERVER])
        specs = await manager.start([config])
        try:
            names = {s.name for s in specs}
            self.assertIn("mcp__test__echo", names)
            echo_spec = next(s for s in specs if s.name == "mcp__test__echo")
            self.assertEqual("high", echo_spec.risk_level)
            self.assertTrue(echo_spec.side_effects)
            self.assertTrue(registry.confirmation_required(echo_spec.name, {"text": "hi"}))
            self.assertIn(
                "external mcp tool",
                registry.confirmation_reason(echo_spec.name, {"text": "hi"}).lower(),
            )
            # Surfaced into the registry's coordinator allowlist + schemas.
            self.assertIn("mcp__test__echo", registry.coordinator_tool_names())
            self.assertTrue(
                any(s["function"]["name"] == "mcp__test__echo" for s in registry.tool_schemas())
            )
            self.assertTrue(manager.handles("mcp__test__echo"))
            # Round-trip a real call through the worker session.
            result = await manager.call("mcp__test__echo", {"text": "hi"})
            self.assertIn("echo: hi", result)
        finally:
            await manager.stop()
        # After shutdown, external tools are cleared from the registry.
        self.assertNotIn("mcp__test__echo", registry.coordinator_tool_names())

    def test_unknown_mcp_tool_is_reported(self):
        async def _run():
            manager = MCPManager()
            return await manager.call("mcp__nope__missing", {})
        self.assertIn("Unknown MCP tool", asyncio.run(_run()))


if __name__ == "__main__":
    unittest.main()

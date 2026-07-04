"""MCP client — connect to external MCP servers and surface their tools.

Realises the "native core + MCP client for external servers" decision: browser
control and any other mature MCP server plug in here, while OS/file/gh/web stay
native. Discovered tools are namespaced ``mcp__<server>__<tool>`` and registered
into the tool registry, so the coordinator picks them like any other tool; the
agent loop routes their execution back to this client.

Concurrency: each server runs in its OWN asyncio task that owns the full stdio
session lifecycle (open → serve calls from a queue → close). All MCP/anyio
context operations therefore happen in a single task, which avoids the
cross-task cancel-scope errors the SDK raises if a session is awaited from a
different task than the one that opened it.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import PILOT_MCP_BROWSER_CMD, PILOT_MCP_BROWSER_ENABLED
from tools import registry

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    name: str  # short namespace, e.g. "browser"
    command: str  # e.g. "npx"
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    enabled: bool = True


def _render_tool_result(result) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        elif getattr(block, "type", "") == "image":
            parts.append("[image returned]")
    out = "\n".join(parts).strip()
    if getattr(result, "isError", False):
        return f"(MCP tool error) {out}".strip()
    return out or "(no content)"


class _ServerWorker:
    """Owns one MCP server subprocess + session for its whole lifetime."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.tools: list[dict] = []  # [{name, description, inputSchema}]
        self.error: str | None = None
        self._requests: asyncio.Queue = asyncio.Queue()
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self, timeout: float = 90.0) -> bool:
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self.config.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.error = self.error or f"timed out starting MCP server {self.config.name!r}"
        return self.error is None

    async def _run(self) -> None:
        params = StdioServerParameters(
            command=self.config.command, args=self.config.args, env=self.config.env
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.tools = [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "inputSchema": t.inputSchema or {"type": "object", "properties": {}},
                        }
                        for t in listed.tools
                    ]
                    self._ready.set()
                    await self._serve(session)
        except Exception as exc:  # noqa: BLE001 — surface any startup/transport failure
            self.error = f"{type(exc).__name__}: {exc}"
            logger.warning("MCP server %s failed: %s", self.config.name, self.error)
            self._ready.set()  # unblock start()

    async def _serve(self, session: ClientSession) -> None:
        while True:
            item = await self._requests.get()
            if item is None:  # shutdown sentinel
                return
            tool_name, args, fut = item
            # Skip a request whose caller already gave up (timed out / cancelled):
            # executing it would run the tool's side effects AFTER the caller was
            # told it failed (review 2026-07-04).
            if fut.done():
                continue
            try:
                result = await session.call_tool(tool_name, args or {})
                if not fut.done():
                    fut.set_result(_render_tool_result(result))
            except Exception as exc:  # noqa: BLE001
                if not fut.done():
                    fut.set_result(f"(MCP call failed) {type(exc).__name__}: {exc}")

    async def call(self, tool_name: str, args: dict, timeout: float = 120.0) -> str:
        if self.error:
            return f"MCP server {self.config.name!r} unavailable: {self.error}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._requests.put((tool_name, args, fut))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return f"MCP tool {tool_name!r} timed out after {timeout}s"

    async def stop(self) -> None:
        if self._task and not self._task.done():
            await self._requests.put(None)
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()


class MCPManager:
    """Starts the configured MCP servers and routes tool calls to them."""

    def __init__(self) -> None:
        self._workers: dict[str, _ServerWorker] = {}
        self._route: dict[str, tuple[str, str]] = {}  # namespaced -> (server, raw tool)

    async def start(self, configs: list[MCPServerConfig]) -> list[registry.ToolSpec]:
        specs: list[registry.ToolSpec] = []
        for cfg in configs:
            if not cfg.enabled:
                continue
            worker = _ServerWorker(cfg)
            if not await worker.start():
                logger.warning("Skipping MCP server %s: %s", cfg.name, worker.error)
                continue
            self._workers[cfg.name] = worker
            for tool in worker.tools:
                namespaced = f"{registry.EXTERNAL_PREFIX}{cfg.name}__{tool['name']}"
                self._route[namespaced] = (cfg.name, tool["name"])
                specs.append(_spec_from_mcp_tool(namespaced, cfg.name, tool))
        registry.register_external(specs)
        if specs:
            logger.info("Registered %d MCP tools from %d server(s)", len(specs), len(self._workers))
        return specs

    def handles(self, tool: str) -> bool:
        return tool in self._route

    async def call(self, tool: str, args: dict) -> str:
        entry = self._route.get(tool)
        if not entry:
            return f"Unknown MCP tool: {tool}"
        server, raw = entry
        return await self._workers[server].call(raw, args or {})

    async def stop(self) -> None:
        for worker in self._workers.values():
            await worker.stop()
        self._workers.clear()
        self._route.clear()
        registry.clear_external()


def _spec_from_mcp_tool(namespaced: str, server: str, tool: dict) -> registry.ToolSpec:
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties", {}) or {}
    required = tuple(schema.get("required", []) or [])
    desc = (tool.get("description") or "").strip().replace("\n", " ")
    summary = desc[:80] if desc else f"{server} tool {tool['name']}"
    return registry.ToolSpec(
        name=namespaced,
        summary=summary,
        description=f"{namespaced}: {desc or summary}",
        when_to_use=f"External '{server}' MCP tool — {summary}",
        params=properties,
        required=required,
        category=server,
        coordinator=True,
        deterministic=False,
    )


def configs_from_env() -> list[MCPServerConfig]:
    """Build the enabled MCP server configs from settings (currently: browser)."""
    configs: list[MCPServerConfig] = []
    if PILOT_MCP_BROWSER_ENABLED:
        parts = PILOT_MCP_BROWSER_CMD.split()
        # Resolve the launcher (npx -> npx.cmd on Windows) so the subprocess starts.
        command = shutil.which(parts[0]) or parts[0]
        configs.append(
            MCPServerConfig(name="browser", command=command, args=parts[1:], enabled=True)
        )
    return configs


# Module-level singleton used by the app lifespan and the agent loop.
manager = MCPManager()

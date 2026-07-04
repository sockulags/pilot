from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import json
import asyncio
import secrets
from config import PILOT_MCP_AUTH_TOKEN
from tools import (
    screenshot, click, type_text, open_app,
    list_dir, read_file, find_file, list_windows, focus_window,
)
from tools import registry
from tools.system import run_command_sync


def _request_token(request: Request) -> str | None:
    """Extract a presented auth token from an MCP request.

    Accepts either `Authorization: Bearer <token>` (standard) or an
    `X-Pilot-Token` header, mirroring the WS `hello` token boundary.
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[len("bearer "):].strip()
    token = request.headers.get("X-Pilot-Token")
    return token.strip() if token else None


def _auth_ok(request: Request) -> bool:
    """True when no token is configured, or the request presents a matching one."""
    if not PILOT_MCP_AUTH_TOKEN:
        return True
    presented = _request_token(request) or ""
    return secrets.compare_digest(presented, PILOT_MCP_AUTH_TOKEN)


# Generated from the single tool registry (tools/registry.py) so this server's
# manifest can't drift from the tools the assistant actually has.
tools_manifest = registry.mcp_manifest()

_MCP_TO_INTERNAL = {
    spec.mcp_name or f"pilot_{spec.name}": spec.name
    for spec in registry.REGISTRY
    if spec.mcp_facing
}


def create_mcp_app() -> FastAPI:
    app = FastAPI(title="Pilot MCP Server")

    @app.get("/mcp")
    async def mcp_sse(request: Request):
        if not _auth_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        async def event_stream():
            yield f"data: {json.dumps({'type': 'tools', **tools_manifest})}\n\n"
            while True:
                await asyncio.sleep(30)
                yield "data: {\"type\":\"ping\"}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.post("/mcp/call")
    async def mcp_call(body: dict, request: Request):
        if not _auth_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        tool = body.get("name")
        args = body.get("arguments", {})
        if not isinstance(args, dict):
            return JSONResponse(
                {"error": "invalid arguments: expected an object"}, status_code=400
            )
        internal_tool = _MCP_TO_INTERNAL.get(tool)
        if internal_tool and registry.confirmation_required(internal_tool, args):
            return {
                "error": "confirmation_required",
                "tool": tool,
                "riskLevel": registry.risk_level_for(internal_tool, args),
                "sideEffects": registry.side_effects_for(internal_tool),
                "reason": registry.confirmation_reason(internal_tool, args),
            }

        # Required-argument guard: a client omitting e.g. "cmd" must get a clear
        # 400, not an unhandled KeyError -> HTTP 500 (review 2026-07-04).
        REQUIRED = {
            "pilot_click": ("x", "y"),
            "pilot_type": ("text",),
            "pilot_run_command": ("cmd",),
            "pilot_open_app": ("name",),
            "pilot_read_file": ("path",),
            "pilot_find_file": ("name",),
            "pilot_focus_window": ("title",),
        }
        missing = [k for k in REQUIRED.get(tool, ()) if k not in args]
        if missing:
            return JSONResponse(
                {"error": f"missing required argument(s): {', '.join(missing)}"},
                status_code=400,
            )

        if tool == "pilot_screenshot":
            img = screenshot()
            return {"content": [{"type": "image", "data": img, "mimeType": "image/png"}]}

        elif tool == "pilot_click":
            result = click(args["x"], args["y"], args.get("button", "left"))
            return {"content": [{"type": "text", "text": result}]}

        elif tool == "pilot_type":
            result = type_text(args["text"])
            return {"content": [{"type": "text", "text": result}]}

        elif tool == "pilot_run_command":
            result = run_command_sync(args["cmd"], args.get("cwd"))
            return {"content": [{"type": "text", "text": result}]}

        elif tool == "pilot_open_app":
            result = open_app(args["name"])
            return {"content": [{"type": "text", "text": result}]}

        elif tool == "pilot_list_dir":
            result = list_dir(args.get("path"))
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

        elif tool == "pilot_read_file":
            result = read_file(args["path"])
            return {"content": [{"type": "text", "text": result["text"]}]}

        elif tool == "pilot_find_file":
            result = find_file(args["name"], args.get("root"))
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

        elif tool == "pilot_list_windows":
            result = list_windows()
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

        elif tool == "pilot_focus_window":
            result = focus_window(args["title"])
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

        return {"error": f"Unknown tool: {tool}"}

    return app

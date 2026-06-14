from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import json
import asyncio
from tools import screenshot, get_screen_size, click, type_text, scroll, open_app
from tools.system import run_command_sync


def create_mcp_app() -> FastAPI:
    app = FastAPI(title="Pilot MCP Server")

    tools_manifest = {
        "tools": [
            {
                "name": "pilot_screenshot",
                "description": "Take a screenshot of the current screen",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "pilot_click",
                "description": "Click at screen coordinates",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "button": {"type": "string", "default": "left"},
                    },
                    "required": ["x", "y"],
                },
            },
            {
                "name": "pilot_type",
                "description": "Type text using the keyboard",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            {
                "name": "pilot_run_command",
                "description": "Run a shell command and return output",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string"},
                        "cwd": {"type": "string"},
                    },
                    "required": ["cmd"],
                },
            },
            {
                "name": "pilot_open_app",
                "description": "Open an application by name or path",
                "inputSchema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        ]
    }

    @app.get("/mcp")
    async def mcp_sse():
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
    async def mcp_call(body: dict):
        tool = body.get("name")
        args = body.get("arguments", {})

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

        return {"error": f"Unknown tool: {tool}"}

    return app

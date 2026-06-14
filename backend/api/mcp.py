from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import json
import asyncio
from tools import (
    screenshot, click, type_text, open_app,
    list_dir, read_file, find_file, list_windows, focus_window,
)
from tools.system import run_command_sync


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
            {
                "name": "pilot_list_dir",
                "description": "List files and directories",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": [],
                },
            },
            {
                "name": "pilot_read_file",
                "description": "Read a text file",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "pilot_find_file",
                "description": "Find files by exact name",
                "inputSchema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "root": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {
                "name": "pilot_list_windows",
                "description": "List visible desktop windows",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "pilot_focus_window",
                "description": "Focus a window by title",
                "inputSchema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            },
        ]
    }


def create_mcp_app() -> FastAPI:
    app = FastAPI(title="Pilot MCP Server")

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

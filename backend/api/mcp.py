from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import json
import asyncio
from tools import (
    screenshot, click, type_text, open_app,
    list_dir, read_file, find_file, list_windows, focus_window,
)
from tools import registry
from tools.system import run_command_sync


# Generated from the single tool registry (tools/registry.py) so this server's
# manifest can't drift from the tools the assistant actually has.
tools_manifest = registry.mcp_manifest()


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

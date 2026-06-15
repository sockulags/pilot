import asyncio
import sys
import os
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(__file__))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket

from api.ws import websocket_endpoint
from api.mcp import create_mcp_app
from agents.vision import validate_vision_model
from config import BACKEND_PORT, MCP_PORT, OLLAMA_VISION_ENABLED, FRONTEND_DIR
from scheduler import run_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not OLLAMA_VISION_ENABLED:
        app.state.vision_status = {"ok": False, "message": "vision disabled"}
    else:
        ok, message = await validate_vision_model()
        app.state.vision_status = {"ok": ok, "message": message}
        print(message)
    # Background scheduler for recurring reminders / jobs (survives restarts via
    # the persisted jobs store; reconciles missed fires on startup).
    scheduler_task = asyncio.create_task(run_scheduler())
    try:
        yield
    finally:
        scheduler_task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title="Pilot Backend", lifespan=lifespan)
    app.state.vision_status = {"ok": None, "message": "not checked"}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "vision": app.state.vision_status}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket_endpoint(websocket)

    # Serve the built frontend from one origin when it exists (production /
    # remote). Mounted last so /health and /ws match first. In dev (no out/)
    # this is skipped and the frontend runs separately via `pnpm dev`.
    if os.path.isdir(FRONTEND_DIR):
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
        print(f"Serving frontend -> {FRONTEND_DIR}")

    return app


async def main():
    main_app = create_app()
    mcp_app = create_mcp_app()

    config_main = uvicorn.Config(main_app, host="0.0.0.0", port=BACKEND_PORT, log_level="info")
    config_mcp = uvicorn.Config(mcp_app, host="0.0.0.0", port=MCP_PORT, log_level="info")

    server_main = uvicorn.Server(config_main)
    server_mcp = uvicorn.Server(config_mcp)

    print(f"Pilot backend   -> http://localhost:{BACKEND_PORT}")
    print(f"MCP server      -> http://localhost:{MCP_PORT}/mcp")
    print(f"WebSocket       -> ws://localhost:{BACKEND_PORT}/ws")

    await asyncio.gather(
        server_main.serve(),
        server_mcp.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())

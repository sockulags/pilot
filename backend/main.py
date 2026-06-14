import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.websockets import WebSocket

from api.ws import websocket_endpoint
from api.mcp import create_mcp_app
from config import BACKEND_PORT, MCP_PORT


def create_app() -> FastAPI:
    app = FastAPI(title="Pilot Backend")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket_endpoint(websocket)

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

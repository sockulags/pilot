import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect
from agents.loop import run_agent_loop


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    abort_event = asyncio.Event()
    agent_task: asyncio.Task | None = None

    def emit(event: dict):
        asyncio.create_task(websocket.send_json(event))

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "run":
                if agent_task and not agent_task.done():
                    abort_event.set()
                    await asyncio.sleep(0.1)

                abort_event = asyncio.Event()
                task_text = msg.get("task", "")

                agent_task = asyncio.create_task(
                    run_agent_loop(task_text, emit, abort_event)
                )

            elif msg.get("type") == "abort":
                abort_event.set()
                await websocket.send_json({"type": "done", "summary": "Aborted"})

    except WebSocketDisconnect:
        abort_event.set()
        if agent_task and not agent_task.done():
            agent_task.cancel()

"""WebSocket endpoint — a multi-turn chat driven by the turn orchestrator.

Protocol (client -> server):
- {"type": "hello", "session_id": "..."} resume/persist a session (sent first)
- {"type": "message", "text": "..."}      start a new turn
- {"type": "abort"}                        abort the in-flight turn
- {"type": "reset"}                        clear the conversation (and its store)

On "hello" the backend loads the persisted conversation for that session_id and
replies with {"type":"history", "messages":[...], "turn": N} so a reconnecting
client (mobile drops the socket often) or a restarted backend resumes context.
The conversation is saved to disk after every turn.

Each turn is classified by agents/orchestrator.classify_turn into one of three
routes (chat / code / computer) and executed accordingly. Every server event
carries a "turn" id (and "route") so the UI can group a turn's activity.
"""

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from agents.loop import run_agent_loop
from agents.orchestrator import classify_turn, stream_chat
from store import clear_session, load_session, save_session
from tools import run_codex


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    conversation: list[dict] = []
    session_id: str | None = None
    current_abort = asyncio.Event()
    turn_task: asyncio.Task | None = None
    turn_counter = 0

    def send(event: dict):
        asyncio.create_task(websocket.send_json(event))

    def persist():
        if session_id:
            save_session(session_id, conversation, turn_counter)

    async def handle_message(text: str, turn: int, abort: asyncio.Event):
        # Classify against history BEFORE this user message, then record it.
        prior = list(conversation)
        conversation.append({"role": "user", "content": text})

        decision = await classify_turn(prior, text)
        route = decision["route"]

        summary_holder = {"text": ""}

        def emit(event: dict):
            tagged = {**event, "turn": turn, "route": route}
            if event.get("type") == "done":
                summary_holder["text"] = event.get("summary", "")
            send(tagged)

        emit({"type": "turn_start", "route": route, "thinking": decision.get("thinking", "")})

        if route == "chat":
            reply = await _stream_text(stream_chat(conversation, text), emit, abort)
            conversation.append({"role": "assistant", "content": reply or "(no reply)"})
            emit({"type": "done"})

        elif route == "code":
            emit({"type": "thinking", "content": "Lämnar över till Claude Code..."})
            reply = await _stream_text(run_codex(decision["prompt"]), emit, abort)
            conversation.append({"role": "assistant", "content": reply or "(no output)"})
            emit({"type": "done", "summary": "Claude Code klar"})

        else:  # computer — run_agent_loop emits its own terminal "done"
            await run_agent_loop(decision["task"], emit, abort)
            conversation.append({"role": "assistant", "content": summary_holder["text"] or "Done"})

        persist()

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "hello":
                session_id = msg.get("session_id") or None
                stored = load_session(session_id) if session_id else {"messages": [], "turn": 0}
                conversation = list(stored["messages"])
                turn_counter = stored["turn"]
                await websocket.send_json(
                    {"type": "history", "messages": conversation, "turn": turn_counter}
                )

            elif msg_type == "message":
                if turn_task and not turn_task.done():
                    current_abort.set()
                    await asyncio.sleep(0.1)
                current_abort = asyncio.Event()
                turn_counter += 1
                turn_task = asyncio.create_task(
                    handle_message(msg.get("text", ""), turn_counter, current_abort)
                )

            elif msg_type == "abort":
                current_abort.set()
                await websocket.send_json({"type": "done", "turn": turn_counter, "summary": "Avbruten"})

            elif msg_type == "reset":
                current_abort.set()
                conversation = []
                turn_counter = 0
                if session_id:
                    clear_session(session_id)
                await websocket.send_json({"type": "reset_ok"})

    except WebSocketDisconnect:
        current_abort.set()
        if turn_task and not turn_task.done():
            turn_task.cancel()


async def _stream_text(source, emit, abort: asyncio.Event) -> str:
    """Drain a text-chunk async generator into assistant_delta events.

    Stops early if the turn is aborted. Returns the accumulated text.
    """
    parts: list[str] = []
    async for chunk in source:
        if abort.is_set():
            break
        if not chunk:
            continue
        parts.append(chunk)
        emit({"type": "assistant_delta", "content": chunk})
    return "".join(parts).strip()

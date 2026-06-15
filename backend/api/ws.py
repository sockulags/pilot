"""WebSocket endpoint — a multi-turn chat driven by the turn orchestrator.

Protocol (client -> server):
- {"type": "hello", "session_id": "..."} resume/persist a session (sent first)
- {"type": "message", "text": "..."}      start a new turn
- {"type": "abort"}                        abort the in-flight turn
- {"type": "reset"}                        clear the conversation (and its store)
- {"type": "add_project", "path": "..."}   add a project root
- {"type": "remove_project", "id": "..."}  remove a project root
- {"type": "select_project", "id": "..."}  set this conversation's project (cwd)

On "hello" the backend loads the persisted conversation (messages, turn, cwd,
claude_session_id) for that session_id and replies with `history` + `projects`.
Conversations are saved after every turn so a reconnecting client (mobile drops
the socket often) or a restarted backend resumes context — including which
project the `code` route runs in and the Claude Code session to resume.
"""

import asyncio
import json
import os

from fastapi import WebSocket, WebSocketDisconnect

from agents.loop import run_agent_loop
from agents.orchestrator import classify_turn, stream_chat
from projects import add_project, list_projects, path_for_id, remove_project
from store import clear_session, load_session, save_session
from tools import run_codex


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    conversation: list[dict] = []
    session_id: str | None = None
    cwd: str | None = None
    claude_session_id: str | None = None
    current_abort = asyncio.Event()
    turn_task: asyncio.Task | None = None
    turn_counter = 0

    def send(event: dict):
        asyncio.create_task(websocket.send_json(event))

    def persist():
        if session_id:
            save_session(session_id, conversation, turn_counter, cwd, claude_session_id)

    async def send_projects():
        await websocket.send_json({"type": "projects", "projects": list_projects(), "selected": cwd})

    async def handle_message(text: str, turn: int, abort: asyncio.Event):
        nonlocal claude_session_id
        prior = list(conversation)
        conversation.append({"role": "user", "content": text})

        project = os.path.basename(cwd.rstrip("\\/")) if cwd else None
        decision = await classify_turn(prior, text, project=project)
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
            if not cwd:
                msg = "Välj en projektmapp först (dropdown ovanför inmatningen)."
                emit({"type": "assistant_delta", "content": msg})
                conversation.append({"role": "assistant", "content": msg})
                emit({"type": "done"})
            else:
                claude_session_id = await _run_code_turn(
                    decision["prompt"], cwd, claude_session_id, emit, abort, conversation
                )

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
                stored = load_session(session_id) if session_id else dict(load_session(""))
                conversation = list(stored["messages"])
                turn_counter = stored["turn"]
                cwd = stored.get("cwd")
                claude_session_id = stored.get("claude_session_id")
                await websocket.send_json(
                    {"type": "history", "messages": conversation, "turn": turn_counter}
                )
                await send_projects()

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
                claude_session_id = None  # keep cwd; new Claude session next code turn
                if session_id:
                    clear_session(session_id)
                    persist()
                await websocket.send_json({"type": "reset_ok"})

            elif msg_type == "add_project":
                _projects, error = add_project(msg.get("path", ""))
                if error:
                    await websocket.send_json({"type": "error", "content": error})
                await send_projects()

            elif msg_type == "remove_project":
                remove_project(msg.get("id", ""))
                await send_projects()

            elif msg_type == "select_project":
                new_cwd = path_for_id(msg.get("id", ""))
                if new_cwd != cwd:
                    cwd = new_cwd
                    claude_session_id = None  # switching project starts a fresh Claude session
                    persist()
                await send_projects()

    except WebSocketDisconnect:
        current_abort.set()
        if turn_task and not turn_task.done():
            turn_task.cancel()


async def _run_code_turn(prompt, cwd, resume_id, emit, abort, conversation) -> str | None:
    """Drive Claude Code for one turn. Returns the (possibly new) claude session id."""
    emit({"type": "thinking", "content": f"Claude Code i {cwd}..."})
    parts: list[str] = []
    result_text: str | None = None
    error_text: str | None = None
    session_id = resume_id

    async for ev in run_codex(prompt, cwd=cwd, resume_session_id=resume_id):
        if abort.is_set():
            break
        etype = ev.get("type")
        if etype == "text":
            parts.append(ev["text"])
            emit({"type": "assistant_delta", "content": ev["text"]})
        elif etype == "tool":
            emit({"type": "action", "tool": ev.get("name", "tool"), "args": ev.get("input", {})})
        elif etype == "session":
            session_id = ev["id"]
        elif etype == "result":
            result_text = ev.get("text", "")
        elif etype == "error":
            error_text = ev.get("text", "")
            emit({"type": "error", "content": error_text})

    reply = "".join(parts).strip() or result_text or error_text or "(no output)"
    conversation.append({"role": "assistant", "content": reply})
    emit({"type": "done", "summary": f"Fel: {error_text}" if error_text else "Claude Code klar"})
    return session_id


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

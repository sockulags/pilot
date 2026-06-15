"""WebSocket endpoint — a multi-turn chat driven by the turn orchestrator.

Protocol (client -> server):
- {"type": "hello", "session_id": "..."} resume/persist a session (sent first)
- {"type": "message", "text": "..."}      start a new turn
- {"type": "abort"}                        abort the in-flight turn
- {"type": "reset"}                        clear the conversation (and its store)
- {"type": "add_project", "path": "..."}   add a project root
- {"type": "remove_project", "id": "..."}  remove a project root
- {"type": "select_project", "id": "..."}  set this conversation's project (cwd)
- {"type": "select_agent", "agent": "..."}  set the code-route agent (claude|codex)
- {"type": "select_model", "model_mode": ".."} pin the local model, or "auto"

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

from agents.coordinator import run_coordinator
from agents.orchestrator import classify_turn, compose_reply
from codex_logs import summarize_codex_session
from config import (
    OLLAMA_MODEL,
    OLLAMA_MODELS,
    PILOT_AUTH_TOKEN,
    is_known_model,
    tools_capable_model,
)
from projects import add_project, list_projects, path_for_id, remove_project
from store import clear_session, load_session, save_session
from tools import run_codex, run_codex_cli


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    conversation: list[dict] = []
    session_id: str | None = None
    cwd: str | None = None
    claude_session_id: str | None = None
    codex_session_id: str | None = None
    agent: str = "claude"
    model_mode: str = "auto"  # "auto" = orchestrator picks per turn; else a pinned model id
    current_abort = asyncio.Event()
    turn_task: asyncio.Task | None = None
    turn_counter = 0

    def send(event: dict):
        asyncio.create_task(websocket.send_json(event))

    def persist():
        if session_id:
            save_session(
                session_id, conversation, turn_counter, cwd,
                claude_session_id, codex_session_id, agent, model_mode,
            )

    def model_catalog() -> list[dict]:
        return [
            {"id": mid, "label": meta["label"], "hint": meta["hint"]}
            for mid, meta in OLLAMA_MODELS.items()
        ]

    async def send_projects():
        await websocket.send_json({
            "type": "projects",
            "projects": list_projects(),
            "selected": cwd,
            "agent": agent,
            "model_mode": model_mode,
            "models": model_catalog(),
        })

    def resolve_model_token(token: str) -> str | None:
        """Map a user-typed token to "auto", an exact id, or a unique prefix."""
        token = token.strip().lower()
        if not token or token == "auto":
            return "auto"
        if is_known_model(token):
            return token
        matches = [mid for mid in OLLAMA_MODELS if mid.lower().startswith(token)]
        return matches[0] if len(matches) == 1 else None

    async def _handle_model_command(text: str, turn: int):
        nonlocal model_mode
        arg = text.strip()[len("/model"):].strip()

        def reply(msg: str):
            send({"type": "turn_start", "turn": turn, "route": "chat", "thinking": "model command"})
            send({"type": "assistant_delta", "turn": turn, "route": "chat", "content": msg})
            send({"type": "done", "turn": turn, "route": "chat"})

        if not arg:
            current = "auto (väljer själv per fråga)" if model_mode == "auto" else model_mode
            names = ", ".join(["auto", *OLLAMA_MODELS])
            reply(f"Nuvarande modell: **{current}**.\nByt med `/model <id|auto>`. Val: {names}")
            return

        resolved = resolve_model_token(arg)
        if resolved is None:
            reply(f"Okänd modell {arg!r}. Val: {', '.join(['auto', *OLLAMA_MODELS])}")
            return

        model_mode = resolved
        persist()
        await send_projects()
        label = "auto (väljer själv per fråga)" if resolved == "auto" else resolved
        reply(f"Modell satt till **{label}**.")

    async def handle_message(text: str, turn: int, abort: asyncio.Event):
        nonlocal claude_session_id, codex_session_id, model_mode

        # `/model <id|auto>` is a client-side control, not a turn for the brain.
        if text.strip().lower().startswith("/model"):
            await _handle_model_command(text, turn)
            return

        prior = list(conversation)
        conversation.append({"role": "user", "content": text})

        project = os.path.basename(cwd.rstrip("\\/")) if cwd else None
        decision = await classify_turn(prior, text, project=project, model_mode=model_mode)
        route = decision["route"]
        # The coordinator (front brain) is fast gemma4 in auto mode; a pin makes
        # the chosen model the lead. It consults installed experts as needed.
        coordinator_model = OLLAMA_MODEL if model_mode == "auto" else tools_capable_model(decision.get("model"))

        def emit(event: dict):
            send({**event, "turn": turn, "route": route})

        emit({
            "type": "turn_start",
            "route": route,
            "thinking": decision.get("thinking", ""),
            "model": coordinator_model,
        })

        if route in ("chat", "computer"):
            # Both run through the in-turn coordinator: gemma4 (or the pinned
            # model) auto-orchestrates over the installed experts, perception and
            # OS tools, then compose_reply synthesises the final answer — grounded
            # in what was gathered, or plain conversational when nothing was.
            if cwd:
                emit({"type": "context", "content": f"Working directory: {cwd}"})
            intent = (
                "The user wants you to do something on this computer or find something out; "
                "act or consult when it helps."
                if route == "computer"
                else "The user's message looks conversational; consult an expert or use a "
                "tool only when it clearly improves the answer, otherwise just answer."
            )
            outcome = await run_coordinator(
                text, emit, abort, prior, project_cwd=cwd,
                coordinator_model=coordinator_model, intent_hint=intent,
            )
            grounding = outcome if outcome.action_log else None
            reply = await _stream_text(compose_reply(conversation, grounding, coordinator_model), emit, abort)
            conversation.append({"role": "assistant", "content": reply or outcome.detail or "Klar"})
            emit({"type": "done"})

        elif route == "code":
            if not cwd:
                msg = "Välj en projektmapp först (dropdown ovanför inmatningen)."
                emit({"type": "assistant_delta", "content": msg})
                conversation.append({"role": "assistant", "content": msg})
                emit({"type": "done"})
            elif agent == "codex":
                emit({"type": "thinking", "content": f"Codex i {cwd}..."})
                emit({"type": "context", "content": f"Working directory: {cwd}"})
                codex_session_id = await _run_code_turn(
                    run_codex_cli,
                    decision["prompt"],
                    cwd,
                    codex_session_id,
                    emit,
                    abort,
                    conversation,
                    trace_provider=summarize_codex_session,
                )
            else:
                emit({"type": "thinking", "content": f"Claude Code i {cwd}..."})
                emit({"type": "context", "content": f"Working directory: {cwd}"})
                claude_session_id = await _run_code_turn(
                    run_codex, decision["prompt"], cwd, claude_session_id, emit, abort, conversation
                )

        persist()

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "hello":
                if PILOT_AUTH_TOKEN and msg.get("token") != PILOT_AUTH_TOKEN:
                    await websocket.send_json({"type": "error", "content": "unauthorized"})
                    await websocket.close()
                    return
                session_id = msg.get("session_id") or None
                stored = load_session(session_id) if session_id else dict(load_session(""))
                conversation = list(stored["messages"])
                turn_counter = stored["turn"]
                cwd = stored.get("cwd")
                claude_session_id = stored.get("claude_session_id")
                codex_session_id = stored.get("codex_session_id")
                agent = stored.get("agent", "claude")
                model_mode = stored.get("model_mode", "auto")
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
                claude_session_id = None  # keep cwd/agent; fresh coding sessions next turn
                codex_session_id = None
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
                    claude_session_id = None  # switching project starts fresh coding sessions
                    codex_session_id = None
                    persist()
                await send_projects()

            elif msg_type == "select_agent":
                new_agent = msg.get("agent")
                if new_agent in ("claude", "codex"):
                    agent = new_agent
                    persist()
                await send_projects()

            elif msg_type == "select_model":
                requested = str(msg.get("model_mode", "auto"))
                if requested == "auto" or is_known_model(requested):
                    model_mode = requested
                    persist()
                await send_projects()

    except WebSocketDisconnect:
        current_abort.set()
        if turn_task and not turn_task.done():
            turn_task.cancel()


async def _run_code_turn(
    runner,
    prompt,
    cwd,
    resume_id,
    emit,
    abort,
    conversation,
    trace_provider=None,
) -> str | None:
    """Drive a coding agent (Claude Code or Codex) for one turn.

    ``runner`` is run_codex or run_codex_cli — both yield the same typed events.
    Returns the (possibly new) coding-agent session id for resume.
    """
    parts: list[str] = []
    result_text: str | None = None
    error_text: str | None = None
    session_id = resume_id

    try:
        async for ev in runner(prompt, cwd=cwd, resume_session_id=resume_id):
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
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        emit({"type": "error", "content": error_text})

    reply = "".join(parts).strip() or result_text or error_text or "(no output)"
    message = {
        "role": "assistant",
        "content": reply,
        "cwd": cwd,
        "code_session_id": session_id,
    }
    if trace_provider and session_id:
        try:
            trace = trace_provider(session_id)
        except Exception:
            trace = None
        if trace:
            message["codex_trace"] = trace
            emit({"type": "codex_trace", "trace": trace})
    conversation.append(message)
    emit({"type": "done", "summary": f"Fel: {error_text}" if error_text else "Klar"})
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

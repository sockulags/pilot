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
from datetime import datetime

from fastapi import WebSocket, WebSocketDisconnect

from agents.coordinator import run_coordinator
from agents.gateway import refine_query
from agents.orchestrator import classify_turn, compose_reply, should_offload_code
from codex_logs import summarize_codex_session
from diagnostics import append_turn_diagnostic
from config import (
    OLLAMA_MODEL,
    OLLAMA_MODELS,
    PILOT_AUTH_TOKEN,
    is_known_model,
    tools_capable_model,
)
from connections import register, unregister
from jobs import (
    create_job,
    delete_job,
    describe_schedule,
    list_jobs,
    parse_job_command,
    set_enabled,
)
from memory import format_for_prompt, search_memories
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
    route_mode: str = "auto"  # "auto" = classifier routes; else forced chat|computer|code
    current_abort = asyncio.Event()
    turn_task: asyncio.Task | None = None
    turn_counter = 0
    registered_session: str | None = None  # which session this conn is registered under

    def send(event: dict):
        asyncio.create_task(websocket.send_json(event))

    def deliver_turn(content: str, title: str = ""):
        """Push a fired job's finished message to this client as its own turn.

        Called by the scheduler (outside any user turn) via the connection
        registry. No awaits, so it runs atomically relative to an in-flight turn
        — it updates *this* connection's conversation + persists, which avoids a
        racing turn's persist() clobbering the message.
        """
        nonlocal turn_counter
        turn_counter += 1
        t = turn_counter
        send({"type": "turn_start", "turn": t, "route": "chat",
              "thinking": f"Schemalagt: {title}", "model": OLLAMA_MODEL})
        send({"type": "assistant_delta", "turn": t, "route": "chat", "content": content})
        send({"type": "done", "turn": t, "route": "chat"})
        conversation.append({"role": "assistant", "content": content})
        persist()

    def persist():
        if session_id:
            save_session(
                session_id, conversation, turn_counter, cwd,
                claude_session_id, codex_session_id, agent, model_mode, route_mode,
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
            "route_mode": route_mode,
        })

    async def send_jobs():
        # Each job carries a ready-made Swedish schedule summary so the UI (and
        # the /job list reply) need not re-derive it.
        jobs = [
            {**j, "summary": describe_schedule(j["schedule"]), "next_run_label": _fmt_next(j["next_run"])}
            for j in list_jobs(session_id)
        ]
        await websocket.send_json({"type": "jobs", "jobs": jobs})

    def _format_job_list() -> str:
        jobs = list_jobs(session_id)
        if not jobs:
            return (
                "Inga schemalagda jobb. Skapa t.ex.:\n"
                "- `/job daily 09:00 <text>`\n"
                "- `/job every 10m <text>`\n"
                "- `/job mon,fri 08:00 <text>`\n"
                "- `/job once 2026-06-20 09:00 <text>`"
            )
        lines = ["**Schemalagda jobb:**"]
        for j in jobs:
            status = "" if j["enabled"] else " _(pausad)_"
            lines.append(
                f"- `{j['id']}` {j['title']} — {describe_schedule(j['schedule'])}, "
                f"nästa {_fmt_next(j['next_run'])}{status}"
            )
        lines.append("\nHantera: `/job pause <id>`, `/job resume <id>`, `/job delete <id>`.")
        return "\n".join(lines)

    async def _handle_job_command(text: str, turn: int):
        arg = text.strip()[len("/job"):].strip()
        spec = parse_job_command(arg)

        def reply(msg: str):
            send({"type": "turn_start", "turn": turn, "route": "chat", "thinking": "job command"})
            send({"type": "assistant_delta", "turn": turn, "route": "chat", "content": msg})
            send({"type": "done", "turn": turn, "route": "chat"})

        action = spec["action"]
        if action == "error":
            reply(spec["message"])
        elif action == "list":
            reply(_format_job_list())
        elif action in ("pause", "resume", "delete"):
            jid = spec["id"]
            if action == "delete":
                ok = delete_job(jid)
                msg = f"Jobb `{jid}` borttaget." if ok else f"Hittade inget jobb `{jid}`."
            else:
                job = set_enabled(jid, action == "resume")
                if job is None:
                    msg = f"Hittade inget jobb `{jid}`."
                else:
                    msg = f"Jobb `{jid}` {'återupptaget' if action == 'resume' else 'pausat'}."
            await send_jobs()
            reply(msg)
        elif action == "create":
            if not session_id:
                reply("Ingen aktiv session — kan inte skapa jobb.")
                return
            job = create_job(
                session_id=session_id, title=spec["title"],
                payload=spec["payload"], schedule=spec["schedule"],
                kind=spec.get("kind", "reminder"),
            )
            await send_jobs()
            kind_label = "uppgift" if job["kind"] == "task" else "påminnelse"
            reply(
                f"Jobb skapat ({kind_label}): **{job['title']}** — {describe_schedule(job['schedule'])}. "
                f"Nästa körning {_fmt_next(job['next_run'])}. Id `{job['id']}`."
            )

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

        # `/job ...` manages scheduled jobs — also a control, not a brain turn.
        if text.strip().lower().startswith("/job"):
            await _handle_job_command(text, turn)
            return

        prior = list(conversation)
        conversation.append({"role": "user", "content": text})

        project = os.path.basename(cwd.rstrip("\\/")) if cwd else None
        # Recall relevant long-term memories for this turn (degrades to "" on failure).
        memories = format_for_prompt(await search_memories(text))
        # route_mode "auto" lets the classifier decide; otherwise the user has
        # pinned the route (Läge toggle) and we skip classification entirely.
        if route_mode == "auto":
            decision = await classify_turn(prior, text, project=project, model_mode=model_mode)
        else:
            decision = {"route": route_mode, "prompt": text, "thinking": f"forced route: {route_mode}"}
        route = decision["route"]
        # The coordinator (front brain) is fast gemma4 in auto mode; a pin makes
        # the chosen model the lead. It consults installed experts as needed.
        coordinator_model = OLLAMA_MODEL if model_mode == "auto" else tools_capable_model(model_mode)

        def emit(event: dict):
            enriched = {**event, "turn": turn, "route": route}
            diagnostic_events.append(enriched)
            send(enriched)

        diagnostic_events: list[dict] = []
        turn_status = "done"

        emit({
            "type": "turn_start",
            "route": route,
            "thinking": decision.get("thinking", ""),
            "model": coordinator_model,
        })

        # Local-first: a code turn only reaches the external agent on an explicit
        # offload signal (Läge=code, or "use codex/claude" in the message). All
        # other turns — chat, computer, and a locally-kept code turn — run through
        # the in-turn coordinator.
        offload = route == "code" and should_offload_code(route_mode, text)

        if not offload:
            # gemma4 (or the pinned model) auto-orchestrates over the installed
            # experts, perception and OS tools, then compose_reply synthesises the
            # answer — grounded in what was gathered, or plain chat when nothing was.
            if cwd:
                emit({"type": "context", "content": f"Working directory: {cwd}"})
            if route == "computer":
                intent = (
                    "The user wants you to do something on this computer or find something "
                    "out; act or consult when it helps."
                )
            elif route == "code":
                intent = (
                    "The user wants to work on the active project's code/files. Inspect with "
                    "read_file/list_dir/search_files, use run_command for tests, git and "
                    "builds, and consult the coder model for code. Work locally — do not "
                    "offload unless the user explicitly asks."
                )
            else:
                intent = (
                    "The user's message looks conversational. If they ask you to find "
                    "something out, look something up, or do something on the computer, USE "
                    "the right tool rather than just describing it; otherwise just answer."
                )
            outcome = await run_coordinator(
                text, emit, abort, prior, project_cwd=cwd,
                coordinator_model=coordinator_model, intent_hint=intent,
                memories=memories, session_id=session_id,
            )
            turn_status = outcome.status
            if outcome.status == "needs_input":
                # The coordinator judged the request too vague — its clarifying
                # question IS the reply; no synthesis, no orchestration ran.
                emit({"type": "assistant_delta", "content": outcome.detail})
                conversation.append({"role": "assistant", "content": outcome.detail})
                emit({"type": "done"})
            else:
                grounding = outcome if outcome.action_log else None
                reply = await _stream_text(
                    compose_reply(conversation, grounding, coordinator_model, memories), emit, abort
                )
                conversation.append({"role": "assistant", "content": reply or outcome.detail or "Klar"})
                emit({"type": "done"})

        else:  # explicit offload to the external coding agent
            if not cwd:
                turn_status = "blocked"
                msg = ("Välj en projektmapp först (dropdown ovanför inmatningen) för att "
                       "offloada till kodagenten.")
                emit({"type": "assistant_delta", "content": msg})
                conversation.append({"role": "assistant", "content": msg})
                emit({"type": "done"})
            else:
                # Refine/translate the instruction (English pivot) before the
                # external agent; keep the user's verbatim words alongside.
                prompt = decision.get("prompt", text)
                code_prompt = _with_refined_prompt(await refine_query(prior, prompt), prompt)
                if agent == "codex":
                    emit({"type": "thinking", "content": f"Codex i {cwd}..."})
                    emit({"type": "context", "content": f"Working directory: {cwd}"})
                    codex_session_id = await _run_code_turn(
                        run_codex_cli, code_prompt, cwd, codex_session_id,
                        emit, abort, conversation, trace_provider=summarize_codex_session,
                    )
                else:
                    emit({"type": "thinking", "content": f"Claude Code i {cwd}..."})
                    emit({"type": "context", "content": f"Working directory: {cwd}"})
                    claude_session_id = await _run_code_turn(
                        run_codex, code_prompt, cwd, claude_session_id, emit, abort, conversation
                    )

        persist()
        turn_status = _diagnostic_turn_status(diagnostic_events, turn_status, abort.is_set())
        try:
            append_turn_diagnostic(
                session_id=session_id,
                turn=turn,
                route=route,
                model=coordinator_model,
                events=diagnostic_events,
                status=turn_status,
                final_source=_final_source(diagnostic_events),
            )
        except OSError:
            pass

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
                route_mode = stored.get("route_mode", "auto")
                # (Re-)register this connection so the scheduler can push fired
                # jobs to it. Move the registration if the session id changed.
                if registered_session and registered_session != session_id:
                    unregister(registered_session, deliver_turn)
                    registered_session = None
                if session_id:
                    register(session_id, deliver_turn)
                    registered_session = session_id
                await websocket.send_json(
                    {"type": "history", "messages": conversation, "turn": turn_counter}
                )
                await send_projects()
                await send_jobs()

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

            elif msg_type == "select_route":
                requested = str(msg.get("route_mode", "auto"))
                if requested in ("auto", "chat", "computer", "code"):
                    route_mode = requested
                    persist()
                await send_projects()

            elif msg_type == "add_job":
                if session_id:
                    schedule = msg.get("schedule") or {}
                    payload = str(msg.get("payload", "")).strip()
                    kind = "task" if msg.get("kind") == "task" else "reminder"
                    if schedule.get("type") and payload:
                        create_job(
                            session_id=session_id,
                            title=str(msg.get("title") or payload)[:60],
                            payload=payload,
                            schedule=schedule,
                            kind=kind,
                        )
                await send_jobs()

            elif msg_type in ("pause_job", "resume_job"):
                set_enabled(str(msg.get("id", "")), msg_type == "resume_job")
                await send_jobs()

            elif msg_type == "delete_job":
                delete_job(str(msg.get("id", "")))
                await send_jobs()

    except WebSocketDisconnect:
        current_abort.set()
        if turn_task and not turn_task.done():
            turn_task.cancel()
        if registered_session:
            unregister(registered_session, deliver_turn)


def _fmt_next(ts: float | None) -> str:
    """Format a next-run epoch as local 'YYYY-MM-DD HH:MM' (or em dash)."""
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _with_refined_prompt(refined: str, original: str) -> str:
    """Combine the gateway-refined instruction with the user's verbatim request.

    The code agent gets the clearer (English) instruction but keeps the original
    so it can fall back to the user's exact words if refinement drifted.
    """
    if refined.strip() == original.strip():
        return original
    return f"{refined}\n\n(User's original request: {original})"


def _final_source(events: list[dict]) -> str | None:
    for event in reversed(events):
        if event.get("type") == "action" and event.get("tool"):
            return str(event["tool"])
        if event.get("type") == "consult" and event.get("model"):
            return f"consult:{event['model']}"
    return None


def _diagnostic_turn_status(events: list[dict], default: str, aborted: bool) -> str:
    if aborted and default == "done":
        return "aborted"
    if any(event.get("type") == "error" for event in events) and default == "done":
        return "error"
    return default


def _friendly_agent_error(text: str) -> str | None:
    """Turn a raw coding-agent failure into a clear, actionable message.

    The external agents surface things like a raw usage-limit JSON or
    "[Codex exited 2 with no events]" — dumping those at the user (session
    42cdda5a) is useless. Map the known ones to a Swedish message that points at
    the local fallback. Returns None for unrecognised text (shown as-is)."""
    t = (text or "").lower()
    if any(k in t for k in ("usage limit", "rate limit", "quota", "too many requests", "429")):
        return (
            "Kodagenten har nått sin användningsgräns just nu. Jag kan ta det lokalt "
            "istället — be mig läsa/ändra filerna direkt, eller försök igen senare."
        )
    if "not logged in" in t or "/login" in t or "apikeysource" in t:
        return (
            "Kodagenten är inte inloggad. Logga in på dess CLI en gång, eller låt mig "
            "ta uppgiften lokalt istället."
        )
    if ("exited" in t and "no events" in t) or "no output" in t:
        return (
            "Kodagenten avslutades utan svar (troligen otillgänglig eller slut på krediter). "
            "Jag kan ta det lokalt istället."
        )
    return None


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
                emit({"type": "error", "content": _friendly_agent_error(error_text) or error_text})
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        emit({"type": "error", "content": _friendly_agent_error(error_text) or error_text})

    streamed = "".join(parts).strip()
    # Prefer real output; otherwise surface a friendly message instead of raw
    # usage-limit JSON / "exited with no events".
    reply = (
        streamed
        or _friendly_agent_error(error_text or result_text or "")
        or result_text
        or error_text
        or "(no output)"
    )
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

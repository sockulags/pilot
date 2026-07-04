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
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

import model_settings
from agents.coordinator import run_coordinator
from agents.gateway import refine_query
from agents.model_inventory import get_model_inventory
from agents.orchestrator import classify_turn, compose_reply
from agents.routing import build_routing_decision
from agents.turn_policy import (
    build_task_context,
    resolve_task_contract_intent,
    select_agent_for_intent,
    tool_task,
)
from code_verification import git_status_snapshot, verify_code_run
from codex_logs import summarize_codex_session
from diagnostics import append_turn_diagnostic
from config import (
    AGENT_ROLE_LABELS,
    AGENT_ROLE_MODELS,
    OLLAMA_MODEL,
    OLLAMA_MODELS,
    PILOT_AUTH_TOKEN,
    WS_TURN_TIMEOUT_SECONDS,
    is_known_model,
)
from connections import register, unregister
from jobs import (
    create_job,
    delete_job,
    describe_schedule,
    list_jobs,
    parse_job_command,
    set_enabled,
    valid_schedule,
)
from memory import format_for_prompt, search_memories
from projects import add_project, list_projects, path_for_id, remove_project
from store import clear_session, load_session, save_session
from tools import run_codex, run_codex_cli


def model_catalog() -> list[dict]:
    return [
        {"id": mid, "label": meta["label"], "hint": meta["hint"]}
        for mid, meta in OLLAMA_MODELS.items()
    ]


def agent_role_catalog() -> list[dict]:
    """Effective model per agent role, for the UI.

    Settings assignments (settings page) win over the env defaults; a cloud
    assignment is labelled with its provider. ``source`` says where the value
    came from so the UI can show inheritance.
    """
    roles = []
    for role, env_model in AGENT_ROLE_MODELS.items():
        assigned = model_settings.resolve_role_model(role)
        inherited = model_settings.resolve_role_model("default_agent")
        model = assigned or inherited or env_model
        source = "role" if assigned else ("default" if inherited else "env")
        parsed = model_settings.parse_cloud_model_id(model)
        if parsed:
            provider_id, model_name = parsed
            entry = model_settings.cloud_provider(provider_id)
            provider_label = (entry or {}).get("label", provider_id)
            model_label = f"{model_name} ({provider_label})"
            available = entry is not None
        else:
            meta = OLLAMA_MODELS.get(model, {})
            model_label = meta.get("label", model)
            available = is_known_model(model) or source != "env"
        roles.append({
            "role": role,
            "label": AGENT_ROLE_LABELS.get(role, role.replace("_", " ").title()),
            "model": model,
            "model_label": model_label,
            "available": available,
            "source": source,
        })
    return roles


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
    # Fail closed: when a token is configured, nothing but a successful "hello"
    # may be processed. The WS drives run_command and desktop input, and the
    # server binds 0.0.0.0 — an unauthenticated message must never reach a turn.
    authenticated = not PILOT_AUTH_TOKEN

    # Single-writer outbox: Starlette websockets are not safe for concurrent
    # send_json calls, and fire-and-forget tasks lose ordering under load
    # (streamed assistant_delta events must arrive in emit order). Everything
    # goes through the queue; one sender task owns the socket.
    outbox: asyncio.Queue = asyncio.Queue()
    sender_dead = False

    async def _sender() -> None:
        nonlocal sender_dead
        while True:
            event = await outbox.get()
            try:
                if not sender_dead:
                    await websocket.send_json(event)
            except Exception:
                # Client is gone; keep draining so flush() never blocks.
                sender_dead = True
            finally:
                outbox.task_done()

    sender_task = asyncio.create_task(_sender())

    def send(event: dict):
        outbox.put_nowait(event)

    async def flush() -> None:
        """Wait until every queued event has been handed to the socket."""
        await outbox.join()

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

    async def send_projects():
        send({
            "type": "projects",
            "projects": list_projects(),
            "selected": cwd,
            "agent": agent,
            "model_mode": model_mode,
            "models": model_catalog(),
            "agent_roles": agent_role_catalog(),
            "route_mode": route_mode,
        })

    async def send_jobs():
        # Each job carries a ready-made Swedish schedule summary so the UI (and
        # the /job list reply) need not re-derive it.
        jobs = [
            {**j, "summary": describe_schedule(j["schedule"]), "next_run_label": _fmt_next(j["next_run"])}
            for j in list_jobs(session_id)
        ]
        send({"type": "jobs", "jobs": jobs})

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
                permissions=spec.get("permissions"),
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
        task_context = build_task_context(prior, text)
        effective_task = tool_task(text, task_context)

        project = os.path.basename(cwd.rstrip("\\/")) if cwd else None
        # Recall relevant long-term memories for this turn (degrades to "" on failure).
        memories = format_for_prompt(
            await search_memories(text, session_id=session_id, project=project)
        )
        # route_mode "auto" lets the classifier decide; otherwise the user has
        # pinned the route (Läge toggle) and we skip classification entirely.
        if route_mode == "auto":
            decision = await classify_turn(prior, text, project=project, model_mode=model_mode)
        else:
            decision = {"route": route_mode, "prompt": text, "thinking": f"forced route: {route_mode}"}
        route = decision["route"]
        # Health-check the local model fleet once for this turn (fail closed on
        # discovery failure) so routing and expert advertising consult what is
        # actually installed, not just what is configured.
        inventory = await get_model_inventory()
        # The coordinator (front brain) is fast gemma4 in auto mode; a pin makes
        # the chosen model the lead. It consults installed experts as needed.
        agent_selection = select_agent_for_intent(
            model_mode,
            task_context,
            available_models=set(inventory.healthy),
            installed_all=set(inventory.installed_all),
        )
        coordinator_model = agent_selection.model

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
            "agent_role": agent_selection.role,
            "agent_role_model": agent_selection.configured_model,
            "agent_role_fallback": agent_selection.fallback_reason,
        })

        # Local-first: a code turn only reaches the external agent on an explicit
        # offload signal (Läge=code, or "use codex/claude" in the message). All
        # other turns — chat, computer, and a locally-kept code turn — run through
        # the in-turn coordinator. The RoutingDecision consolidates this logic and
        # explains it; it is surfaced before any expensive/risky action starts.
        routing = build_routing_decision(
            route_mode=route_mode,
            classified_route=route,
            agent=agent,
            text=text,
            project=project,
            cwd=cwd,
        )
        emit(routing.to_event())
        offload = routing.is_offload()

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
                if task_context.requires_current_sources:
                    intent += (
                        " This task needs current or externally verified information: use "
                        "web_research with a standalone query and ground the answer in the "
                        "sources you fetched."
                    )
                if task_context.creates_file:
                    intent += (
                        " The user expects a local output file: gather the needed data, "
                        "write the requested file with the write_file tool, and report "
                        "the exact path."
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
            required_first_tool = None
            if task_context.intent == "project_analysis":
                intent += (
                    " This is a project/backend flow analysis: inspect local files with "
                    "list_dir/search_files/read_file before answering. Do not answer from "
                    "general knowledge or claim no project data is available until you have "
                    "tried the file tools."
                )
                required_first_tool = {"tool": "list_dir", "args": {"path": "."}}
            if task_context.creates_file:
                intent += (
                    " This task requires a local output file. Do not answer as complete "
                    "until you have written the file with the write_file tool (which verifies it)."
                )
            outcome = await run_coordinator(
                effective_task, emit, abort, prior, project_cwd=cwd,
                coordinator_model=coordinator_model, intent_hint=intent,
                memories=memories, session_id=session_id,
                required_first_tool=required_first_tool,
                require_file_output=task_context.creates_file,
                task_contract_intent=resolve_task_contract_intent(task_context),
                inventory=inventory,
            )
            turn_status = outcome.status
            if outcome.status == "needs_input":
                # The coordinator judged the request too vague — its clarifying
                # question IS the reply; no synthesis, no orchestration ran.
                emit({"type": "assistant_delta", "content": outcome.detail})
                conversation.append({
                    "role": "assistant",
                    "content": outcome.detail,
                    "meta": _turn_meta(
                        turn, route, coordinator_model, diagnostic_events,
                        outcome.status, task_context.intent,
                        runtime_state=outcome.runtime_state,
                        routing=routing,
                    ),
                })
                emit({"type": "done"})
            else:
                grounding = outcome if (outcome.action_log or task_context.needs_tools) else None
                reply_source = compose_reply(
                    conversation, grounding, _reply_model(coordinator_model), memories
                )
                if task_context.creates_file:
                    reply = await _collect_text(reply_source, abort)
                    if not _runtime_file_output_verified(outcome.runtime_state):
                        path = _write_fallback_markdown_report(
                            outcome.action_log or reply,
                            output_dir=Path(cwd) if cwd else Path.cwd(),
                            session_id=session_id or "session",
                            turn=turn,
                        )
                        if path.exists():
                            if outcome.runtime_state is not None:
                                outcome.runtime_state.record_tool_result(
                                    "run_command",
                                    {"cmd": f"Set-Content -LiteralPath '{path}'"},
                                    f"Command: Set-Content -LiteralPath '{path}'\nOutput:\n",
                                    ok=True,
                                )
                                outcome.runtime_state.record_tool_result(
                                    "run_command",
                                    {"cmd": f"Test-Path -LiteralPath '{path}'"},
                                    f"Command: Test-Path -LiteralPath '{path}'\nOutput:\nTrue",
                                    ok=True,
                                    artifact_verified=True,
                                )
                            diagnostic_events.append({
                                "type": "action",
                                "tool": "fallback_write_file",
                                "args": {"path": str(path)},
                                "turn": turn,
                                "route": route,
                            })
                            reply = (
                                f"Jag kunde inte få modellen att skapa filen via shell-kommandot, "
                                f"så backend skrev en fallback-rapport och verifierade att den finns:\n\n"
                                f"`{path}`"
                            )
                        else:
                            reply = _missing_file_output_reply(outcome.action_log)
                    reply = _append_verified_artifact_paths(reply, outcome.runtime_state)
                    emit({"type": "assistant_delta", "content": reply})
                else:
                    if grounding is not None:
                        reply = await _collect_text(reply_source, abort)
                        reply = _guard_tool_backed_reply(reply, outcome)
                        if reply:
                            emit({"type": "assistant_delta", "content": reply})
                    else:
                        reply = await _stream_text(reply_source, emit, abort)
                conversation.append({
                    "role": "assistant",
                    "content": reply or _fallback_visible_reply(outcome),
                    "meta": _turn_meta(
                        turn, route, coordinator_model, diagnostic_events,
                        outcome.status, task_context.intent,
                        runtime_state=outcome.runtime_state,
                        routing=routing,
                    ),
                })
                emit({"type": "done"})

        else:  # explicit offload to the external coding agent
            # The route/execution-engine reason rides along on the persisted
            # offloaded turn so every turn carries an explainable decision.
            code_meta = {
                "turn": turn,
                "route": route,
                "execution_engine": routing.execution_engine,
                "routing_reason": routing.reason,
                "required_permissions": list(routing.required_permissions),
            }
            if not cwd:
                turn_status = "blocked"
                msg = ("Välj en projektmapp först (dropdown ovanför inmatningen) för att "
                       "offloada till kodagenten.")
                emit({"type": "assistant_delta", "content": msg})
                conversation.append({"role": "assistant", "content": msg, "meta": code_meta})
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
                        meta=code_meta,
                    )
                else:
                    emit({"type": "thinking", "content": f"Claude Code i {cwd}..."})
                    emit({"type": "context", "content": f"Working directory: {cwd}"})
                    claude_session_id = await _run_code_turn(
                        run_codex, code_prompt, cwd, claude_session_id, emit, abort, conversation,
                        meta=code_meta,
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

    async def run_turn(text: str, turn: int, abort: asyncio.Event):
        """Run one turn under a wall-clock watchdog.

        Mirrors how the scheduler bounds task jobs (JOB_MAX_RUNTIME_SECONDS): a
        hung Ollama call or a stalled external coding agent would otherwise leave
        the client spinning forever. On timeout the turn is cancelled, its abort
        event is set, and the client gets a friendly done/error event instead of
        an eternal spinner. WS_TURN_TIMEOUT_SECONDS = 0 disables the bound. The
        serialization in the `message`/`abort`/`reset` handlers still cancels and
        awaits *this* task, so no extra locking is introduced.
        """
        if WS_TURN_TIMEOUT_SECONDS <= 0:
            await handle_message(text, turn, abort)
            return
        try:
            await asyncio.wait_for(
                handle_message(text, turn, abort), timeout=WS_TURN_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            # Stop the in-flight work cooperatively, then tell the client the turn
            # ended rather than leaving it spinning. handle_message was cancelled
            # by wait_for; abort halts any straggling generator on the next check.
            abort.set()
            send({
                "type": "error",
                "turn": turn,
                "content": (
                    f"Turen tog för lång tid (över {WS_TURN_TIMEOUT_SECONDS}s) och avbröts. "
                    "Modellen eller en extern agent svarade inte — försök igen."
                ),
            })
            send({"type": "done", "turn": turn, "summary": "Timeout"})

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type != "hello" and not authenticated:
                send({"type": "error", "content": "unauthorized"})
                await flush()
                await websocket.close()
                return

            if msg_type == "hello":
                token = str(msg.get("token") or "")
                if PILOT_AUTH_TOKEN and not secrets.compare_digest(token, PILOT_AUTH_TOKEN):
                    send({"type": "error", "content": "unauthorized"})
                    await flush()
                    await websocket.close()
                    return
                authenticated = True
                session_id = msg.get("session_id") or None
                stored = load_session(session_id) if session_id else dict(load_session(""))
                conversation = list(stored["messages"])
                turn_counter = stored["turn"]
                cwd = stored.get("cwd")
                claude_session_id = stored.get("claude_session_id")
                codex_session_id = stored.get("codex_session_id")
                agent = stored.get("agent", "claude")
                stored_model_mode = stored.get("model_mode", "auto")
                model_mode = (
                    stored_model_mode
                    if stored_model_mode == "auto" or is_known_model(stored_model_mode)
                    else "auto"
                )
                stored_route_mode = stored.get("route_mode", "auto")
                route_mode = (
                    stored_route_mode
                    if stored_route_mode in ("auto", "chat", "computer", "code")
                    else "auto"
                )
                # (Re-)register this connection so the scheduler can push fired
                # jobs to it. Move the registration if the session id changed.
                if registered_session and registered_session != session_id:
                    unregister(registered_session, deliver_turn)
                    registered_session = None
                if session_id:
                    register(session_id, deliver_turn)
                    registered_session = session_id
                send({"type": "history", "messages": conversation, "turn": turn_counter})
                await send_projects()
                await send_jobs()

            elif msg_type == "message":
                # Serialize turns: cancel and AWAIT the in-flight turn before
                # starting a new one, so two handle_message coroutines never
                # mutate the shared conversation/session concurrently. Abort is
                # cooperative and can lag inside a multi-second model call, so a
                # fixed sleep is not a synchronization primitive (review
                # 2026-07-04).
                if turn_task and not turn_task.done():
                    current_abort.set()
                    turn_task.cancel()
                    await asyncio.gather(turn_task, return_exceptions=True)
                current_abort = asyncio.Event()
                turn_counter += 1
                turn_task = asyncio.create_task(
                    run_turn(msg.get("text", ""), turn_counter, current_abort)
                )

            elif msg_type == "abort":
                current_abort.set()
                # Cancel and await the in-flight turn so its tail (append +
                # persist) cannot run after we report it aborted.
                if turn_task and not turn_task.done():
                    turn_task.cancel()
                    await asyncio.gather(turn_task, return_exceptions=True)
                send({"type": "done", "turn": turn_counter, "summary": "Avbruten"})

            elif msg_type == "reset":
                current_abort.set()
                # Cancel and await the in-flight turn BEFORE clearing state:
                # otherwise the dying turn's tail appends its assistant message to
                # the fresh (empty) conversation and persist() re-creates the
                # session file we just cleared (review 2026-07-04).
                if turn_task and not turn_task.done():
                    turn_task.cancel()
                    await asyncio.gather(turn_task, return_exceptions=True)
                conversation = []
                turn_counter = 0
                claude_session_id = None  # keep cwd/agent; fresh coding sessions next turn
                codex_session_id = None
                if session_id:
                    clear_session(session_id)
                    persist()
                send({"type": "reset_ok"})

            elif msg_type == "add_project":
                _projects, error = add_project(msg.get("path", ""))
                if error:
                    send({"type": "error", "content": error})
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
                    # Validate the client-supplied schedule at the boundary so a
                    # malformed one is rejected with feedback instead of being
                    # persisted and crashing the scheduler tick.
                    if not payload:
                        send({"type": "error", "content": "Jobbet saknar text."})
                    elif not valid_schedule(schedule):
                        send({"type": "error", "content": "Ogiltigt schema för jobbet."})
                    else:
                        create_job(
                            session_id=session_id,
                            title=str(msg.get("title") or payload)[:60],
                            payload=payload,
                            schedule=schedule,
                            kind=kind,
                            permissions=msg.get("permissions"),
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
    finally:
        sender_task.cancel()


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


def _turn_meta(
    turn: int,
    route: str,
    model: str,
    events: list[dict],
    status: str,
    intent: str = "",
    runtime_state=None,
    routing=None,
) -> dict:
    turn_start = next((event for event in events if event.get("type") == "turn_start"), {})
    meta = {
        "turn": turn,
        "route": route,
        "model": model,
        "final_source": _final_source(events),
        "tools": [
            str(event.get("tool"))
            for event in events
            if event.get("type") == "action" and event.get("tool")
        ],
        "status": status,
        "intent": intent,
    }
    for key in ("agent_role", "agent_role_model", "agent_role_fallback"):
        if turn_start.get(key):
            meta[key] = turn_start[key]
    # Every turn carries an explainable route/execution-engine reason.
    if routing is not None:
        meta["execution_engine"] = routing.execution_engine
        meta["routing_reason"] = routing.reason
        meta["required_permissions"] = list(routing.required_permissions)
    else:
        routing_event = next(
            (event for event in events if event.get("type") == "routing_decision"), {}
        )
        if routing_event:
            meta["execution_engine"] = routing_event.get("execution_engine")
            meta["routing_reason"] = routing_event.get("reason")
            meta["required_permissions"] = list(routing_event.get("required_permissions") or [])
    if runtime_state is not None:
        meta.update(runtime_state.to_meta())
    return meta


def _reply_model(coordinator_model: str) -> str:
    """Use the stable default model for final user-visible synthesis.

    Specialist/coordinator models can be chosen for tool decisions, but the final
    answer layer should prioritize reliable visible content over hidden thinking.
    """
    return OLLAMA_MODEL


def _file_output_verified(events: list[dict]) -> bool:
    wrote_file = any(
        event.get("type") == "action"
        and event.get("tool") == "run_command"
        and _command_writes_file(str((event.get("args") or {}).get("cmd") or ""))
        for event in events
    )
    verified_file = any(
        event.get("type") == "action"
        and event.get("tool") == "run_command"
        and _command_verifies_file(str((event.get("args") or {}).get("cmd") or ""))
        for event in events
    )
    return wrote_file and verified_file


def _runtime_file_output_verified(runtime_state) -> bool:
    if runtime_state is None:
        return False
    return any(
        bool(artifact.get("verified")) and bool(str(artifact.get("path") or "").strip())
        for artifact in getattr(runtime_state, "artifacts", [])
    )


def _append_verified_artifact_paths(reply: str, runtime_state) -> str:
    text = reply or ""
    if runtime_state is None:
        return text
    paths = [
        str(artifact.get("path") or "").strip()
        for artifact in getattr(runtime_state, "artifacts", [])
        if artifact.get("verified") and str(artifact.get("path") or "").strip()
    ]
    missing = [path for path in paths if path not in text]
    if not missing:
        return text
    suffix = "\n".join(f"Verifierad fil: `{path}`" for path in missing)
    return f"{text.rstrip()}\n\n{suffix}" if text.strip() else suffix


def _command_writes_file(cmd: str) -> bool:
    lowered = cmd.lower()
    return any(
        token in lowered
        for token in (
            "set-content",
            "out-file",
            "new-item",
            " add-content",
            ">>",
            ">",
            "tee-object",
            "python -c",
        )
    )


def _command_verifies_file(cmd: str) -> bool:
    lowered = cmd.lower()
    return any(
        token in lowered
        for token in (
            "test-path",
            "get-item",
            "get-childitem",
            "dir ",
        )
    )


def _missing_file_output_reply(action_log: str) -> str:
    suffix = f"\n\nUnderlag som hann samlas:\n{action_log}" if action_log else ""
    return (
        "Jag kunde inte verifiera att den begärda filen faktiskt skapades. "
        "Jag ska inte påstå att filen finns förrän ett skrivkommando och en "
        "verifiering har körts."
        f"{suffix}"
    )


def _fallback_visible_reply(outcome) -> str:
    return (
        str(getattr(outcome, "detail", "") or "").strip()
        or str(getattr(outcome, "action_log", "") or "").strip()
        or "Klar"
    )


def _guard_tool_backed_reply(reply: str, outcome) -> str:
    text = (reply or "").strip()
    if not text or _is_low_information_reply(text) or _looks_like_raw_tool_log(text):
        return _evidence_summary_reply(outcome)
    return text


def _is_low_information_reply(text: str) -> bool:
    return text.strip().lower() in {"klar", "klart", "klar.", "klart."}


def _looks_like_raw_tool_log(text: str) -> bool:
    return bool(
        "web_research(" in text
        or "run_command(" in text
        or "read_file(" in text
        or text.lstrip().startswith("- web_research")
        or "Research results for" in text
    )


def _evidence_summary_reply(outcome) -> str:
    state = getattr(outcome, "runtime_state", None)
    if state is not None:
        sources = getattr(state, "sources", [])
        if sources:
            urls = []
            for source in sources:
                urls.extend(source.get("urls") or [])
            fetched = sources[0].get("sources_fetched")
            source_count = f"{fetched} källor" if fetched is not None else "källor"
            if urls:
                return f"Jag hämtade {source_count}. Källor: {', '.join(urls)}"
            return f"Jag hämtade {source_count}."
        artifacts = [
            str(artifact.get("path") or "").strip()
            for artifact in getattr(state, "artifacts", [])
            if artifact.get("verified") and str(artifact.get("path") or "").strip()
        ]
        if artifacts:
            return "\n".join(f"Verifierad fil: `{path}`" for path in artifacts)
        files_read = [str(path) for path in getattr(state, "files_read", []) if str(path)]
        if files_read:
            return "Jag läste relevanta filer med read_file: " + ", ".join(files_read)
        commands = getattr(state, "commands", [])
        if commands:
            command = str(commands[-1].get("cmd") or "").strip()
            summary = str(commands[-1].get("summary") or "").strip()
            return f"Körde kommando: `{command}`\n\n{summary}".strip()
    return _fallback_visible_reply(outcome)


def _write_fallback_markdown_report(
    content: str,
    output_dir: Path,
    session_id: str,
    turn: int,
) -> Path:
    safe_session = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in session_id)[:48]
    path = output_dir / f"pilot_report_{safe_session}_{turn}.md"
    text = content.strip() or "Ingen rapporttext genererades."
    if not text.startswith("#"):
        text = f"# Pilotrapport\n\n{text}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return path


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
    meta=None,
) -> str | None:
    """Drive a coding agent (Claude Code or Codex) for one turn.

    ``runner`` is run_codex or run_codex_cli — both yield the same typed events.
    ``meta`` is merged into the persisted assistant message so every offloaded
    turn carries its route/execution-engine reason. Returns the (possibly new)
    coding-agent session id for resume.
    """
    parts: list[str] = []
    result_text: str | None = None
    error_text: str | None = None
    session_id = resume_id

    # Snapshot the working tree BEFORE the agent runs so we can isolate what
    # THIS turn changed (best-effort; empty/non-git cwd just yields no baseline).
    try:
        before_snapshot = await git_status_snapshot(cwd)
    except Exception:
        before_snapshot = None

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
    if meta:
        message["meta"] = dict(meta)

    # Independently inspect the repo after the agent finishes: changed files,
    # unexpected changes outside the project, and (opt-in) a verification run.
    # This is additive and best-effort — never let it break the turn.
    try:
        code_run = await verify_code_run(cwd, before=before_snapshot)
    except Exception as exc:
        code_run = {
            "cwd": cwd,
            "error": f"{type(exc).__name__}: {exc}",
            "verification": {
                "ran": False,
                "passed": None,
                "command": None,
                "reason": "verification helper failed",
                "returncode": None,
            },
        }
    message["code_run"] = code_run
    emit({"type": "code_verification", "code_run": code_run})

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


async def _collect_text(source, abort: asyncio.Event) -> str:
    parts: list[str] = []
    async for chunk in source:
        if abort.is_set():
            break
        if chunk:
            parts.append(chunk)
    return "".join(parts).strip()

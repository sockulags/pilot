"""Background scheduler loop — fires due jobs and routes them to a session.

Started once from main.py's lifespan. Each tick it asks the jobs store what is
owed (``due_jobs``), produces each job's message, delivers it to a live
connection for its session when one exists, otherwise writes the message into
the session store so it appears in history on the next reconnect. Either way
``mark_ran`` rolls the schedule forward (and disables one-shot / exhausted jobs).

Two job kinds:
- ``reminder`` — deliver the reminder text. Instant, run inline in the tick.
- ``task`` — run the full coordinator (consult experts / use OS tools) on the
  job's instruction and deliver the composed result. Slow, so each runs as its
  own asyncio task and an in-flight guard stops a long task from re-firing on
  the next tick before it finishes.
"""

from __future__ import annotations

import asyncio
import logging

from config import JOBS_TICK_SECONDS
from connections import deliver_to_session
from jobs import due_jobs, mark_ran, reconcile_on_start, reminder_content
from store import load_session, save_session

logger = logging.getLogger(__name__)

# Task-kind jobs currently executing — prevents a slow task re-firing each tick.
_running: set[str] = set()


async def run_scheduler() -> None:
    """Tick forever, firing due jobs. Cancelled on backend shutdown."""
    reconcile_on_start()
    logger.info("job scheduler started (tick %ss)", JOBS_TICK_SECONDS)
    while True:
        try:
            for job in due_jobs():
                if job["id"] in _running:
                    continue
                if job.get("kind") == "task":
                    _running.add(job["id"])
                    asyncio.create_task(_run_task(job))
                else:
                    _deliver(job, reminder_content(job))
                    mark_ran(job["id"], result="delivered")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("scheduler tick failed: %s", exc)
        await asyncio.sleep(JOBS_TICK_SECONDS)


def _deliver(job: dict, content: str) -> bool:
    """Deliver to a live client, else persist for offline pickup. Returns live?"""
    delivered = deliver_to_session(job.get("session_id"), content, job.get("title", ""))
    if not delivered:
        _store_offline(job, content)
    return delivered


async def _run_task(job: dict) -> None:
    """Execute a task-kind job via the coordinator and deliver its result."""
    try:
        content = await _execute_task(job)
        delivered = _deliver(job, content)
        mark_ran(job["id"], result="delivered" if delivered else "stored")
    except Exception as exc:
        logger.warning("task job %s failed: %s", job.get("id"), exc)
        mark_ran(job["id"], result=f"error: {exc}")
    finally:
        _running.discard(job["id"])


async def _execute_task(job: dict) -> str:
    """Run the coordinator on the job's instruction; return the composed reply.

    Mirrors the chat/computer turn in api/ws.py: the coordinator gathers
    grounding over installed experts / OS tools, then compose_reply synthesises
    the answer in the user's language. Intermediate steps are not streamed (a
    background job's user may not be watching) — only the final result is
    delivered, prefixed with the job title.
    """
    from agents.coordinator import run_coordinator
    from agents.orchestrator import compose_reply
    from config import OLLAMA_MODEL, tools_capable_model
    from memory import format_for_prompt, search_memories

    sid = job.get("session_id")
    stored = load_session(sid) if sid else {}
    prior = list(stored.get("messages", []))
    cwd = stored.get("cwd")
    model_mode = stored.get("model_mode", "auto")
    coordinator_model = OLLAMA_MODEL if model_mode == "auto" else tools_capable_model(model_mode)

    task_text = (job.get("payload") or job.get("title") or "").strip()
    memories = format_for_prompt(await search_memories(task_text))
    abort = asyncio.Event()  # never set — background tasks aren't user-abortable

    def _noop(_event: dict) -> None:
        pass

    outcome = await run_coordinator(
        task_text, _noop, abort, prior, project_cwd=cwd,
        coordinator_model=coordinator_model,
        intent_hint="This is a scheduled background task — carry it out and report the result.",
        memories=memories, session_id=sid,
    )
    title = job.get("title", "")
    if outcome.status == "needs_input":
        return f"⏰ {title}: {outcome.detail}"

    conversation = [*prior, {"role": "user", "content": task_text}]
    grounding = outcome if outcome.action_log else None
    parts: list[str] = []
    async for chunk in compose_reply(conversation, grounding, coordinator_model, memories):
        if chunk:
            parts.append(chunk)
    text = "".join(parts).strip() or outcome.detail or "Klar"
    return f"⏰ {title}\n{text}"


def _store_offline(job: dict, content: str) -> None:
    """Append a fired job's message to the session store for an offline client.

    Mirrors save_session's full field set so nothing the live handler persists
    (cwd, agent, model/route mode, coding-agent ids) is lost.
    """
    sid = job.get("session_id")
    if not sid:
        return
    stored = load_session(sid)
    messages = list(stored["messages"])
    messages.append({"role": "assistant", "content": content})
    save_session(
        sid,
        messages,
        stored.get("turn", 0) + 1,
        stored.get("cwd"),
        stored.get("claude_session_id"),
        stored.get("codex_session_id"),
        stored.get("agent", "claude"),
        stored.get("model_mode", "auto"),
        stored.get("route_mode", "auto"),
    )

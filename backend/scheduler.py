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

from config import JOB_MAX_RUNTIME_SECONDS, JOB_MAX_TOOL_CALLS, JOBS_TICK_SECONDS
from connections import deliver_to_session
from jobs import due_jobs, mark_ran, reconcile_on_start, record_audit, reminder_content
from store import load_session, save_session

logger = logging.getLogger(__name__)

# Task-kind jobs currently executing — prevents a slow task re-firing each tick.
_running: set[str] = set()
# In-flight task handles + their abort events, keyed by job id, so a running job
# can be cancelled (cancel_job): sets the abort event and cancels the asyncio.Task.
_running_tasks: dict[str, asyncio.Task] = {}
_abort_events: dict[str, asyncio.Event] = {}


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
                    _running_tasks[job["id"]] = asyncio.create_task(_run_task(job))
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
    """Execute a task-kind job via the coordinator and deliver its result.

    Wraps execution in a hard wall-clock timeout (JOB_MAX_RUNTIME_SECONDS): on
    timeout the abort event is set so the coordinator stops, the task is treated
    as failed, and the timeout is recorded in the audit trail. A structured audit
    record (tool calls, status, errors, output) is persisted for every fire.
    """
    abort = asyncio.Event()
    _abort_events[job["id"]] = abort
    audit: dict = {"permissions": job.get("permissions"), "timed_out": False}
    try:
        content, run_audit = await asyncio.wait_for(
            _execute_task(job, abort), timeout=JOB_MAX_RUNTIME_SECONDS
        )
        audit.update(run_audit)
        delivered = _deliver(job, content)
        result = "delivered" if delivered else "stored"
        audit["status"] = audit.get("status") or result
        mark_ran(job["id"], result=result)
    except asyncio.TimeoutError:
        abort.set()
        logger.warning("task job %s timed out after %ss", job.get("id"), JOB_MAX_RUNTIME_SECONDS)
        audit.update({"status": "timeout", "timed_out": True,
                      "output": f"(cancelled after {JOB_MAX_RUNTIME_SECONDS}s timeout)"})
        mark_ran(job["id"], result="error: timeout")
    except asyncio.CancelledError:
        abort.set()
        audit.update({"status": "cancelled", "output": "(cancelled by user)"})
        mark_ran(job["id"], result="cancelled")
        raise
    except Exception as exc:
        logger.warning("task job %s failed: %s", job.get("id"), exc)
        audit.update({"status": "error", "output": f"error: {exc}",
                      "errors": [*audit.get("errors", []), str(exc)]})
        mark_ran(job["id"], result=f"error: {exc}")
    finally:
        record_audit(job["id"], audit)
        _running.discard(job["id"])
        _running_tasks.pop(job["id"], None)
        _abort_events.pop(job["id"], None)


def cancel_job(job_id: str) -> bool:
    """Cancel a currently-running task job: set its abort event and cancel the
    asyncio task handle. Returns False if the job isn't currently running."""
    abort = _abort_events.get(job_id)
    if abort is not None:
        abort.set()
    task = _running_tasks.get(job_id)
    if task is None:
        return False
    task.cancel()
    return True


def build_audit_record(outcome, output: str) -> dict:
    """Extract a structured audit record from a coordinator LoopOutcome.

    Pulls the tool-call history off the run's runtime_state (its to_prompt_dict
    'actions') so the job's run history shows what was attempted, what was
    allowed/denied, errors, the final status and the final output.
    """
    state = getattr(outcome, "runtime_state", None)
    rs = state.to_prompt_dict() if state is not None else {}
    tool_calls = [
        {
            "tool": a.get("tool"),
            "args": a.get("args"),
            "decision": a.get("decision"),
            "ok": a.get("ok"),
            "summary": a.get("summary", "")[:300],
        }
        for a in rs.get("actions", [])
    ]
    return {
        "status": getattr(outcome, "status", "unknown"),
        "output": output,
        "tool_calls": tool_calls,
        "errors": rs.get("errors", []),
    }


async def _execute_task(job: dict, abort: asyncio.Event) -> tuple[str, dict]:
    """Run the coordinator on the job's instruction; return (reply, audit).

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
    # Background jobs ARE abortable: ``abort`` is set on timeout/cancel by
    # _run_task. The job's permission profile bounds which tools may run, and the
    # per-job tool-call cap stops a runaway loop.
    permissions = job.get("permissions") or "read-only"

    def _noop(_event: dict) -> None:
        pass

    outcome = await run_coordinator(
        task_text, _noop, abort, prior, project_cwd=cwd,
        coordinator_model=coordinator_model,
        intent_hint="This is a scheduled background task — carry it out and report the result.",
        memories=memories, session_id=sid,
        capabilities=permissions, max_tool_calls=JOB_MAX_TOOL_CALLS,
    )
    title = job.get("title", "")
    if outcome.status == "needs_input":
        content = f"⏰ {title}: {outcome.detail}"
        return content, build_audit_record(outcome, content)

    conversation = [*prior, {"role": "user", "content": task_text}]
    grounding = outcome if outcome.action_log else None
    parts: list[str] = []
    async for chunk in compose_reply(conversation, grounding, coordinator_model, memories):
        if chunk:
            parts.append(chunk)
    text = "".join(parts).strip() or outcome.detail or "Klar"
    content = f"⏰ {title}\n{text}"
    return content, build_audit_record(outcome, content)


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

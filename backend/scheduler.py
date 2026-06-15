"""Background scheduler loop — fires due jobs and routes them to a session.

Started once from main.py's lifespan. Each tick it asks the jobs store what is
owed (``due_jobs``), delivers each job to a live connection for its session when
one exists, otherwise writes the message into the session store so it appears in
history on the next reconnect. Either way ``mark_ran`` rolls the schedule forward
(and disables one-shot / exhausted jobs).

Fas 2 handles ``kind == "reminder"`` only — it just delivers the reminder text.
``kind == "task"`` (run the coordinator on a schedule) is wired in Fas 4.
"""

from __future__ import annotations

import asyncio
import logging

from config import JOBS_TICK_SECONDS
from connections import deliver_to_session
from jobs import due_jobs, mark_ran, reconcile_on_start, reminder_content
from store import load_session, save_session

logger = logging.getLogger(__name__)


async def run_scheduler() -> None:
    """Tick forever, firing due jobs. Cancelled on backend shutdown."""
    reconcile_on_start()
    logger.info("job scheduler started (tick %ss)", JOBS_TICK_SECONDS)
    while True:
        try:
            for job in due_jobs():
                await _fire(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("scheduler tick failed: %s", exc)
        await asyncio.sleep(JOBS_TICK_SECONDS)


async def _fire(job: dict) -> None:
    delivered = deliver_to_session(job.get("session_id"), job)
    if not delivered:
        _store_offline(job)
    mark_ran(job["id"], result="delivered" if delivered else "stored")


def _store_offline(job: dict) -> None:
    """Append the reminder to the session store for an offline client.

    Mirrors save_session's full field set so nothing the live handler persists
    (cwd, agent, model/route mode, coding-agent ids) is lost.
    """
    sid = job.get("session_id")
    if not sid:
        return
    stored = load_session(sid)
    messages = list(stored["messages"])
    messages.append({"role": "assistant", "content": reminder_content(job)})
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

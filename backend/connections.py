"""Registry of live WebSocket connections, keyed by session_id.

The chat is otherwise strictly request/response: each WS connection only sends
events back during a turn it is handling. Scheduled jobs need to push events
*outside* a turn, to whichever connection currently owns a given session — this
registry is that missing link.

A connection registers a ``deliver`` callable on `hello` and drops it on
disconnect. The scheduler calls ``deliver_to_session`` when a job fires; if no
connection is live the scheduler falls back to writing into the session store so
the message shows up in history on the next reconnect.

Everything runs on the single asyncio event loop, so the plain dict needs no
lock — registration, delivery and removal never interleave mid-operation.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# session_id -> set of deliver callables (one per live connection to that session).
_hooks: dict[str, set[Callable[[dict], None]]] = {}


def register(session_id: str, deliver: Callable[[dict], None]) -> None:
    _hooks.setdefault(session_id, set()).add(deliver)


def unregister(session_id: str, deliver: Callable[[dict], None]) -> None:
    hooks = _hooks.get(session_id)
    if not hooks:
        return
    hooks.discard(deliver)
    if not hooks:
        _hooks.pop(session_id, None)


def deliver_to_session(session_id: str | None, job: dict) -> bool:
    """Deliver ``job`` to every live connection for ``session_id``.

    Returns True if at least one connection received it (so the scheduler knows
    whether it still needs to persist the message for an offline client).
    """
    if not session_id:
        return False
    delivered = False
    for hook in list(_hooks.get(session_id, ())):
        try:
            hook(job)
            delivered = True
        except Exception as exc:  # one bad connection must not stop the others
            logger.warning("job delivery hook failed: %s", exc)
    return delivered

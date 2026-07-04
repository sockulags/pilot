"""On-disk persistence for chat sessions.

One JSON file per session_id under SESSIONS_DIR. The conversation is the source
of truth on the backend, so a reconnecting client (mobile drops the WebSocket
often) or a backend restart can resume the same conversation.

Stored shape: {"messages": [{"role", "content"}, ...], "turn": int,
"cwd": str|None, "claude_session_id": str|None, "codex_session_id": str|None,
"agent": "claude"|"codex", "model_mode": "auto"|<model id>}
"""

import json
import logging
import os
import re
import tempfile
import time

from config import MAX_PERSISTED_MESSAGES, SESSIONS_DIR, SESSIONS_MAX_AGE_DAYS

logger = logging.getLogger(__name__)

# Accept only safe session ids (uuid-ish) so the id can't escape SESSIONS_DIR.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_EMPTY = {
    "messages": [],
    "turn": 0,
    "cwd": None,
    "claude_session_id": None,
    "codex_session_id": None,
    "agent": "claude",
    "model_mode": "auto",
    "route_mode": "auto",
}


def is_valid_session_id(session_id: str) -> bool:
    return bool(session_id) and bool(_SAFE_ID.match(session_id))


def _path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def load_session(session_id: str) -> dict:
    """Return {"messages": [...], "turn": int}. Empty if unknown/invalid."""
    if not is_valid_session_id(session_id):
        return dict(_EMPTY)
    try:
        with open(_path(session_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "messages": list(data.get("messages", [])),
            "turn": int(data.get("turn", 0)),
            "cwd": data.get("cwd"),
            "claude_session_id": data.get("claude_session_id"),
            "codex_session_id": data.get("codex_session_id"),
            "agent": data.get("agent", "claude"),
            "model_mode": data.get("model_mode", "auto"),
            "route_mode": data.get("route_mode", "auto"),
        }
    except FileNotFoundError:
        return dict(_EMPTY)
    except Exception as exc:
        logger.warning("Could not load session %s: %s", session_id, exc)
        return dict(_EMPTY)


def save_session(
    session_id: str,
    messages: list[dict],
    turn: int,
    cwd: str | None = None,
    claude_session_id: str | None = None,
    codex_session_id: str | None = None,
    agent: str = "claude",
    model_mode: str = "auto",
    route_mode: str = "auto",
) -> None:
    if not is_valid_session_id(session_id):
        return
    if turn <= 0 and not messages:
        return
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    # Bound the persisted history: keep only the most recent N messages so a very
    # long-lived session can't grow the file without limit. The live turn still
    # runs on the full in-memory conversation; only what lands on disk (and is
    # replayed as `history` on resume) is trimmed. `turn` is preserved so the
    # session id and its counter survive — resume still works.
    if MAX_PERSISTED_MESSAGES > 0 and len(messages) > MAX_PERSISTED_MESSAGES:
        messages = messages[-MAX_PERSISTED_MESSAGES:]
    payload = {
        "messages": messages,
        "turn": turn,
        "cwd": cwd,
        "claude_session_id": claude_session_id,
        "codex_session_id": codex_session_id,
        "agent": agent,
        "model_mode": model_mode,
        "route_mode": route_mode,
    }
    try:
        # Atomic write: temp file in the same dir, then replace.
        fd, tmp = tempfile.mkstemp(dir=SESSIONS_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, _path(session_id))
    except Exception as exc:
        logger.warning("Could not save session %s: %s", session_id, exc)
    # Piggy-back a cheap prune of stale session files on the save we just did, so
    # the directory doesn't accumulate abandoned sessions forever. Best-effort:
    # never let it break a save.
    prune_old_sessions()


def prune_old_sessions(max_age_days: int | None = None) -> int:
    """Delete session files untouched for more than `max_age_days` days.

    Cheap mtime scan of SESSIONS_DIR, invoked opportunistically from
    save_session. Returns the number of files removed. Config-gated: a
    non-positive max age disables the prune entirely (the old behaviour). Only
    the current session being written is never touched — it was just saved, so
    its mtime is fresh. Fully best-effort; any error is swallowed.
    """
    if max_age_days is None:
        max_age_days = SESSIONS_MAX_AGE_DAYS
    if max_age_days <= 0:
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        entries = os.scandir(SESSIONS_DIR)
    except FileNotFoundError:
        return 0
    except Exception as exc:
        logger.warning("Could not scan sessions dir for prune: %s", exc)
        return 0
    with entries:
        for entry in entries:
            if not entry.name.endswith(".json"):
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
                    removed += 1
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("Could not prune session file %s: %s", entry.path, exc)
    return removed


def clear_session(session_id: str) -> None:
    if not is_valid_session_id(session_id):
        return
    try:
        os.remove(_path(session_id))
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Could not clear session %s: %s", session_id, exc)

"""On-disk persistence for chat sessions.

One JSON file per session_id under SESSIONS_DIR. The conversation is the source
of truth on the backend, so a reconnecting client (mobile drops the WebSocket
often) or a backend restart can resume the same conversation.

Stored shape: {"messages": [{"role", "content"}, ...], "turn": int}
"""

import json
import logging
import os
import re
import tempfile

from config import SESSIONS_DIR

logger = logging.getLogger(__name__)

# Accept only safe session ids (uuid-ish) so the id can't escape SESSIONS_DIR.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_EMPTY = {"messages": [], "turn": 0}


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
        }
    except FileNotFoundError:
        return dict(_EMPTY)
    except Exception as exc:
        logger.warning("Could not load session %s: %s", session_id, exc)
        return dict(_EMPTY)


def save_session(session_id: str, messages: list[dict], turn: int) -> None:
    if not is_valid_session_id(session_id):
        return
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    payload = {"messages": messages, "turn": turn}
    try:
        # Atomic write: temp file in the same dir, then replace.
        fd, tmp = tempfile.mkstemp(dir=SESSIONS_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, _path(session_id))
    except Exception as exc:
        logger.warning("Could not save session %s: %s", session_id, exc)


def clear_session(session_id: str) -> None:
    if not is_valid_session_id(session_id):
        return
    try:
        os.remove(_path(session_id))
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Could not clear session %s: %s", session_id, exc)

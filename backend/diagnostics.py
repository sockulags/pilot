"""Append-only per-turn diagnostics for offline session analysis."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from config import DIAGNOSTICS_MAX_BYTES

logger = logging.getLogger(__name__)

DIAGNOSTICS_FILE = os.path.join(os.path.dirname(__file__), "data", "turn_diagnostics.jsonl")


def _rotate_if_needed() -> None:
    """Roll the diagnostics log to a single .1 backup once it grows too large.

    Keeps one previous generation (DIAGNOSTICS_FILE.1), so on-disk use is bounded
    at roughly 2x DIAGNOSTICS_MAX_BYTES instead of growing forever. Config-gated:
    a non-positive cap disables rotation. Best-effort — a failed roll never stops
    the append that follows (the row is simply written to the current file).
    """
    if DIAGNOSTICS_MAX_BYTES <= 0:
        return
    try:
        size = os.path.getsize(DIAGNOSTICS_FILE)
    except OSError:
        return
    if size < DIAGNOSTICS_MAX_BYTES:
        return
    backup = DIAGNOSTICS_FILE + ".1"
    try:
        os.replace(DIAGNOSTICS_FILE, backup)
    except OSError as exc:
        logger.warning("Could not rotate diagnostics log: %s", exc)


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    kept = {
        key: event.get(key)
        for key in ("type", "tool", "args", "content", "model", "route")
        if key in event
    }
    content = kept.get("content")
    if isinstance(content, str):
        kept["content"] = content[:800]
    return kept


def append_turn_diagnostic(
    session_id: str | None,
    turn: int,
    route: str,
    model: str,
    events: list[dict[str, Any]],
    status: str,
    final_source: str | None = None,
) -> None:
    """Write a compact JSONL row for one completed turn.

    This intentionally does not affect the user-visible conversation. It gives
    later analysis enough evidence to see chosen route/model, tool arguments,
    errors, retry/recovery notes and final source.
    """
    tools = [
        _compact_event(event)
        for event in events
        if event.get("type") == "action" or event.get("tool")
    ]
    errors = [
        {"type": "error", "content": str(event.get("content", ""))[:800]}
        for event in events
        if event.get("type") == "error"
    ]
    retries = [
        _compact_event(event)
        for event in events
        if event.get("type") == "thinking"
        and any(token in str(event.get("content", "")).lower() for token in ("retry", "repar", "fallback", "requires argument"))
    ]
    row = {
        "ts": time.time(),
        "session_id": session_id,
        "turn": turn,
        "route": route,
        "model": model,
        "status": status,
        "final_source": final_source,
        "tools": tools,
        "errors": errors,
        "retries": retries,
    }
    os.makedirs(os.path.dirname(DIAGNOSTICS_FILE), exist_ok=True)
    _rotate_if_needed()
    line = json.dumps(row, ensure_ascii=False)
    with open(DIAGNOSTICS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

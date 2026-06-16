"""Append-only per-turn diagnostics for offline session analysis."""

from __future__ import annotations

import json
import os
import time
from typing import Any

DIAGNOSTICS_FILE = os.path.join(os.path.dirname(__file__), "data", "turn_diagnostics.jsonl")


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    kept = {
        key: event.get(key)
        for key in ("type", "tool", "args", "content", "model", "route")
        if key in event
    }
    if isinstance(kept.get("content"), str):
        kept["content"] = kept["content"][:800]
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
    line = json.dumps(row, ensure_ascii=False)
    with open(DIAGNOSTICS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

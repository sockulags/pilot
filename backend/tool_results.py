"""Structured tool result envelope used by tool executors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    kind: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    sources: list[dict[str, str]] = field(default_factory=list)

    def to_text(self) -> str:
        return self.text or self.error or ""

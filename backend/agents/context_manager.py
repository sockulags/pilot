"""Deterministic request budgeting and context-pressure recovery.

The estimator is deliberately conservative and dependency-free: UTF-8 bytes are
charged at one token per three bytes (plus message framing), tool schemas
are charged the same way, and each Ollama image reserves 4096 tokens.  Exact
provider counts, when returned, remain the authority for usage reporting; this
module prevents requests that are *known* to exceed Pilot's effective window.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Any


IMAGE_TOKENS = 4096
MESSAGE_OVERHEAD = 12
SUMMARY_MARKER = "[DETERMINISTIC CONTEXT SUMMARY — unverified text remains unverified]"


class ContextBudgetError(ValueError):
    """The mandatory request contract cannot fit the effective context."""


@dataclass(frozen=True)
class ContextReport:
    context_window: int
    completion_reserve: int
    prompt_budget: int
    estimated_prompt_tokens: int
    pressure: str
    compacted: bool
    retry: bool = False
    removed_messages: int = 0
    summarized_messages: int = 0
    trimmed_tool_messages: int = 0
    media_tokens: int = 0
    tool_schema_tokens: int = 0
    decisions: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ManagedRequest:
    messages: list[dict]
    tools: list[dict] | None
    report: ContextReport


def estimate_text_tokens(value: Any) -> int:
    """Conservative tokenizer fallback documented at module level."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return max(1, math.ceil(len(value.encode("utf-8")) / 3))


def _payload_without_media(value: Any) -> Any:
    """Return canonical provider payload metadata without charging image bytes twice."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key == "images" and isinstance(item, list):
                # Preserve cardinality/field framing; binary payload has a fixed media charge.
                cleaned[key] = ["<media>"] * len(item)
            elif key in {"image_url", "image"}:
                cleaned[key] = "<media>"
            elif key == "url" and isinstance(item, str) and item.startswith("data:image/"):
                cleaned[key] = "<media>"
            else:
                cleaned[key] = _payload_without_media(item)
        return cleaned
    if isinstance(value, list):
        return [_payload_without_media(item) for item in value]
    return value


def estimate_message_tokens(message: dict) -> tuple[int, int]:
    # Count the complete serialized provider message (name, tool calls and nested
    # arguments, tool_call_id, retained extension fields), not merely content.
    content = message.get("content", "")
    text = estimate_text_tokens(_payload_without_media(message)) + MESSAGE_OVERHEAD
    images = message.get("images") or []
    media = IMAGE_TOKENS * len(images)
    # OpenAI-compatible multimodal content blocks are also accounted for.
    if isinstance(content, list):
        media += IMAGE_TOKENS * sum(
            1 for part in content
            if isinstance(part, dict) and part.get("type") in {"image", "image_url", "input_image"}
        )
    return text + media, media


def is_context_overflow(exc: BaseException) -> bool:
    """Normalize common Ollama/OpenAI context overflow failures."""
    parts = [str(exc)]
    response = getattr(exc, "response", None)
    if response is not None:
        parts.append(getattr(response, "text", "") or "")
        try:
            parts.append(json.dumps(response.json(), sort_keys=True))
        except Exception:  # noqa: BLE001 - error normalization must never mask the error
            pass
    text = " ".join(parts).lower()
    markers = (
        "context length", "context_length", "context window", "context size",
        "exceeds context", "too many tokens", "maximum context", "prompt is too long",
        "input length", "num_ctx",
    )
    return any(marker in text for marker in markers)


def _pressure(ratio: float) -> str:
    if ratio < 0.70:
        return "normal"
    if ratio < 0.85:
        return "trim_tools"
    if ratio < 0.95:
        return "summarize_history"
    return "essential_only"


def _mandatory(message: dict, index: int, last_user: int) -> bool:
    return bool(
        message.get("pinned")
        or message.get("verified_evidence")
        or message.get("context_kind") in {"safety", "active_task", "pinned_fact", "verified_evidence"}
        or message.get("role") == "system"
        or index == last_user
    )


def _tool_like(message: dict) -> bool:
    return message.get("role") == "tool" or message.get("context_kind") == "tool_output"


def _compact_content(message: dict, limit: int, *, summary: bool) -> dict:
    result = copy.deepcopy(message)
    content = result.get("content", "")
    if not isinstance(content, str) or len(content) <= limit:
        return result
    head = max(0, limit * 2 // 3)
    tail = max(0, limit - head)
    excerpt = content[:head] + ("\n…\n" + content[-tail:] if tail else "")
    result["content"] = f"{SUMMARY_MARKER}\n{excerpt}" if summary else excerpt + "\n[trimmed]"
    return result


def manage_request(
    messages: list[dict],
    *,
    context_window: int,
    tools: list[dict] | None = None,
    completion_reserve: int | None = None,
    force_compact: bool = False,
    retry: bool = False,
) -> ManagedRequest:
    """Fit one request into its prompt budget without mutating caller data."""
    if context_window < 256:
        raise ContextBudgetError("effective context window is too small")
    reserve = completion_reserve or min(2048, max(256, context_window // 4))
    reserve = min(reserve, context_window - 128)
    prompt_budget = context_window - reserve
    copied = copy.deepcopy(messages)
    tool_tokens = estimate_text_tokens(tools) if tools else 0

    def total(items: list[dict]) -> tuple[int, int]:
        counts = [estimate_message_tokens(item) for item in items]
        return sum(item[0] for item in counts) + tool_tokens, sum(item[1] for item in counts)

    initial, initial_media = total(copied)
    ratio = initial / max(1, prompt_budget)
    pressure = "essential_only" if force_compact else _pressure(ratio)
    decisions: list[str] = []
    trimmed = summarized = removed = 0
    last_user = max((i for i, m in enumerate(copied) if m.get("role") == "user"), default=-1)

    # At 70%, shrink verbose prior tool output first.
    if pressure in {"trim_tools", "summarize_history", "essential_only"}:
        for idx, message in enumerate(copied):
            if (
                _tool_like(message)
                and not _mandatory(message, idx, last_user)
                and len(str(message.get("content", ""))) > 600
            ):
                copied[idx] = _compact_content(message, 600, summary=False)
                trimmed += 1
        if trimmed:
            decisions.append(f"trimmed {trimmed} verbose tool message(s)")

    # At 85%, replace older optional history with deterministic, explicitly
    # unverified excerpts. This is extraction, never an LLM-generated fact.
    if pressure in {"summarize_history", "essential_only"}:
        for idx, message in enumerate(copied):
            if not _mandatory(message, idx, last_user) and not _tool_like(message):
                compacted = _compact_content(message, 360, summary=True)
                if compacted != message:
                    copied[idx] = compacted
                    summarized += 1
        if summarized:
            decisions.append(f"summarized {summarized} older message(s)")

    # At 95% (and on overflow retry), retain the contract and recent optional
    # turns, dropping oldest optional context first.
    if pressure == "essential_only":
        optional = [
            i for i, m in enumerate(copied)
            if not _mandatory(m, i, last_user)
        ]
        keep_optional = set(optional[-2:])
        next_messages = []
        for idx, message in enumerate(copied):
            if _mandatory(message, idx, last_user) or idx in keep_optional:
                next_messages.append(message)
            else:
                removed += 1
        copied = next_messages
        if removed:
            decisions.append(f"removed {removed} oldest optional message(s)")

    # If still over budget, deterministically remove remaining optional items.
    # Mandatory material is byte-for-byte preserved: an impossible mandatory
    # contract fails closed before any provider request.
    while True:
        estimated, media = total(copied)
        if estimated <= prompt_budget:
            break
        last_user = max((i for i, m in enumerate(copied) if m.get("role") == "user"), default=-1)
        optional_idx = next(
            (i for i, m in enumerate(copied) if not _mandatory(m, i, last_user)), None
        )
        if optional_idx is not None:
            copied.pop(optional_idx)
            removed += 1
            continue
        raise ContextBudgetError(
            f"mandatory context requires {estimated} tokens but prompt budget is {prompt_budget}"
        )

    if removed and not any(d.startswith("removed") for d in decisions):
        decisions.append(f"removed {removed} optional message(s) to fit budget")
    if retry:
        decisions.append("provider overflow: applied exactly-one compacted retry")
    report = ContextReport(
        context_window=context_window,
        completion_reserve=reserve,
        prompt_budget=prompt_budget,
        estimated_prompt_tokens=estimated,
        pressure=pressure,
        compacted=bool(trimmed or summarized or removed or force_compact),
        retry=retry,
        removed_messages=removed,
        summarized_messages=summarized,
        trimmed_tool_messages=trimmed,
        media_tokens=media,
        tool_schema_tokens=tool_tokens,
        decisions=tuple(decisions),
    )
    return ManagedRequest(copied, copy.deepcopy(tools), report)

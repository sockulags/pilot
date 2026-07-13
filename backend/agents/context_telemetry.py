"""Safe, versioned context telemetry for WebSocket clients and persistence."""

from __future__ import annotations

from agents.context_manager import ContextReport


_CATEGORIES = ("system", "tools", "media", "history", "memory", "evidence")


def _public_call(report: ContextReport, index: int) -> dict:
    """Project a report onto an explicit allowlist of numeric/status fields."""
    return {
        "call_index": index,
        "model": report.model,
        "context_role": report.context_role,
        "declared_max": report.declared_context,
        "effective_limit": report.context_window,
        "prompt_budget": report.prompt_budget,
        "estimated_prompt_tokens": report.estimated_prompt_tokens,
        "actual_prompt_tokens": report.actual_prompt_tokens,
        "actual_completion_tokens": report.actual_completion_tokens,
        "completion_reserve": report.completion_reserve,
        "measurement": "exact" if report.actual_prompt_tokens is not None else "estimated",
        "categories": {name: int(report.categories.get(name, 0)) for name in _CATEGORIES},
        "pressure": report.pressure,
        "compacted": report.compacted,
        "overflow_retry": report.retry,
        "changes": {
            "history": {
                "summarized": report.summarized_categories.get("history", 0),
                "dropped": report.removed_categories.get("history", 0),
            },
            "evidence": {
                "summarized": report.summarized_categories.get("evidence", 0),
                "dropped": report.removed_categories.get("evidence", 0),
            },
            "tools": {"trimmed": report.trimmed_tool_messages},
        },
    }


def build_context_telemetry(reports: list[ContextReport]) -> dict | None:
    """Return per-call telemetry; never sum incompatible request windows."""
    if not reports:
        return None
    calls = [_public_call(report, index) for index, report in enumerate(reports)]
    final_index = len(calls) - 1
    return {
        "version": 1,
        "calls": calls,
        # Different provider calls can have different effective windows. Keep
        # them inspectable and display the final call instead of summing them.
        "primary_call": final_index,
        "final_call": final_index,
        "aggregation": "per_call_not_summed",
        "compacted": any(call["compacted"] for call in calls),
        "overflow_retried": any(call["overflow_retry"] for call in calls),
    }

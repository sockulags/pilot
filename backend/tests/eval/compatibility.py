"""Declarative, network-free compatibility contracts for local inference.

The fixtures in this module describe behavior, not marketing claims.  Pytest
drives them through Pilot's production context manager/provider adapters with
scripted transports; the opt-in live runner imports the same scenario ids and
report schema so offline and live results stay comparable.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal


REPORT_SCHEMA_VERSION = 1
Verdict = Literal["supported", "limited", "unverified", "unsupported", "failed"]


@dataclass(frozen=True)
class ProviderContract:
    id: str
    runtime_kind: Literal["ollama", "openai_compatible"]
    discovery_path: str
    chat_path: str
    embedding_path: str
    stream_protocol: Literal["ndjson", "sse"]
    multimodal_format: Literal["images", "data_url"]
    tool_arguments: Literal["object", "json_string"]


@dataclass(frozen=True)
class CompatibilityScenario:
    id: str
    capability: str
    context_window: int
    completion_reserve: int
    expected: str


PROVIDER_CONTRACTS = (
    ProviderContract(
        "ollama", "ollama", "/api/tags", "/api/chat", "/api/embed",
        "ndjson", "images", "object",
    ),
    ProviderContract(
        "lm-studio-openai", "openai_compatible", "/models",
        "/chat/completions", "/embeddings", "sse", "data_url", "json_string",
    ),
    ProviderContract(
        "llama-cpp-openai", "openai_compatible", "/models",
        "/chat/completions", "/embeddings", "sse", "data_url", "json_string",
    ),
)

SCENARIOS = (
    CompatibilityScenario("text", "chat", 4096, 1024, "dispatch"),
    CompatibilityScenario("native_tool", "tools", 4096, 1024, "normalized_tool_call"),
    CompatibilityScenario("below_budget", "context", 4096, 1024, "dispatch"),
    CompatibilityScenario("above_budget", "context", 4096, 1024, "compact_or_fail_closed"),
    CompatibilityScenario("vision_long_8192", "vision", 8192, 1024, "dispatch_over_4096"),
    CompatibilityScenario("large_tool_schema", "tools", 8192, 1024, "budget_schema"),
    CompatibilityScenario("overflow_retry_once", "context", 4096, 1024, "two_attempts"),
    CompatibilityScenario("overflow_twice", "context", 4096, 1024, "fail_after_two"),
    CompatibilityScenario("embeddings", "embeddings", 4096, 1024, "nonempty_vector"),
)


def scenario_ids() -> tuple[str, ...]:
    return tuple(item.id for item in SCENARIOS)


def canonical_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return stable comparison material, excluding run-specific measurements."""
    value = copy.deepcopy(report)
    value.pop("comparison_key", None)
    run = value.get("run") or {}
    for key in ("timestamp_utc", "git_sha", "git_dirty"):
        run.pop(key, None)

    def strip_volatile(item: Any) -> None:
        if isinstance(item, dict):
            for key in (
                "latency_ms", "token_usage", "detail", "availability_detail",
            ):
                item.pop(key, None)
            for nested in item.values():
                strip_volatile(nested)
        elif isinstance(item, list):
            for nested in item:
                strip_volatile(nested)

    strip_volatile(value)
    return value


def comparison_key(report: dict[str, Any]) -> str:
    payload = json.dumps(canonical_report(report), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def contract_rows() -> list[dict[str, Any]]:
    return [asdict(item) for item in PROVIDER_CONTRACTS]


def scenario_rows() -> list[dict[str, Any]]:
    return [asdict(item) for item in SCENARIOS]

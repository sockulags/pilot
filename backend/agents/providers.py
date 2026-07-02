"""Model backend abstraction: local Ollama or an OpenAI-compatible API.

Pilot is local-first — every model-driven call runs on Ollama by default. This
module lets the *answering/decision* calls (turn classification, the tool-decision
loop, expert consults, final synthesis) optionally run against an
OpenAI-compatible endpoint instead, selected by ``PILOT_ANSWER_BACKEND`` (or the
``backend=`` argument, which the eval runner sets per run). Perception/vision and
memory embeddings are NOT routed here — they stay local.

Two entry points cover every call shape the agent needs:

- :func:`chat_once` — one non-streamed turn, returns a NORMALIZED message dict
  ``{"content": str, "tool_calls": [...]}`` in the SAME shape Ollama returns, so
  ``coordinator._decision_from_message`` consumes either backend unchanged. Used
  by the tool-decision loop and classification.
- :func:`chat_stream` — stream content deltas (the user-facing synthesis and
  expert consults).

Token usage is accumulated per turn via a contextvar (:func:`reset_usage` /
:func:`get_usage`) so the eval runner can report tokens and approximate cost
without threading counters through the agent.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any, AsyncGenerator

import httpx

from agents.json_utils import extract_json_object
from config import (
    ANSWER_BACKEND,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_MODELS,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)

logger = logging.getLogger(__name__)

OLLAMA = "ollama"
OPENAI = "openai"

# Run-level override set by the eval runner (--backend) so a whole suite runs on
# one backend without threading it through every call. None = fall back to the
# PILOT_ANSWER_BACKEND env default.
_backend_override: str | None = None


# --------------------------------------------------------------------------- #
# Backend + model resolution
# --------------------------------------------------------------------------- #


def _norm(backend: str | None) -> str:
    chosen = (backend or OLLAMA).strip().lower()
    return chosen if chosen in (OLLAMA, OPENAI) else OLLAMA


def set_backend(backend: str | None) -> None:
    """Set (or clear, with None) the run-level backend override."""
    global _backend_override
    _backend_override = _norm(backend) if backend else None


def resolve_backend(backend: str | None = None) -> str:
    """Effective backend: explicit arg > run override > PILOT_ANSWER_BACKEND."""
    return _norm(backend or _backend_override or ANSWER_BACKEND)


def answer_model(backend: str | None = None, model: str | None = None) -> str:
    """Resolve the model id for a backend.

    On the OpenAI path a local (Ollama) model id passed by the agent (e.g.
    ``gemma4:12b``) is ignored in favour of ``OPENAI_MODEL`` — so the agent keeps
    passing its coordinator model unchanged and the backend swap "just works".
    """
    be = resolve_backend(backend)
    if be == OPENAI:
        if model and model not in OLLAMA_MODELS:
            return model  # an explicit OpenAI model id
        return OPENAI_MODEL
    return model or OLLAMA_MODEL


def openai_configured() -> bool:
    return bool(OPENAI_API_KEY)


# --------------------------------------------------------------------------- #
# Per-turn token accounting (contextvar so nested calls accumulate)
# --------------------------------------------------------------------------- #

_usage: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_usage", default=None)


def reset_usage() -> None:
    _usage.set({"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "backend": None})


def record_usage(prompt_tokens: int, completion_tokens: int, backend: str) -> None:
    u = _usage.get()
    if u is None:
        return
    u["prompt_tokens"] += int(prompt_tokens or 0)
    u["completion_tokens"] += int(completion_tokens or 0)
    u["calls"] += 1
    u["backend"] = backend


def get_usage() -> dict:
    u = _usage.get()
    return dict(u) if u else {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "backend": None}


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


async def chat_once(
    messages: list[dict],
    model: str | None = None,
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.1,
    backend: str | None = None,
) -> dict:
    """One non-streamed turn. Returns a normalized ``{"content", "tool_calls"}``."""
    be = resolve_backend(backend)
    if be == OPENAI:
        return await _openai_once(messages, answer_model(be, model), tools, temperature)
    return await _ollama_once(messages, answer_model(be, model), tools, temperature)


async def chat_stream(
    messages: list[dict],
    model: str | None = None,
    *,
    temperature: float = 0.2,
    think: bool = False,
    backend: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream content deltas. ``think`` only affects the Ollama backend."""
    be = resolve_backend(backend)
    if be == OPENAI:
        async for piece in _openai_stream(messages, answer_model(be, model), temperature):
            yield piece
    else:
        async for piece in _ollama_stream(messages, answer_model(be, model), temperature, think):
            yield piece


# --------------------------------------------------------------------------- #
# Ollama implementations (preserve existing behaviour, incl. tools 400-retry)
# --------------------------------------------------------------------------- #


async def _ollama_once(
    messages: list[dict], model: str, tools: list[dict] | None, temperature: float
) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if tools:
        payload["tools"] = tools
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        if tools and resp.status_code >= 400:
            # Endpoint/model rejected the tools payload — retry as plain JSON.
            payload.pop("tools", None)
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    record_usage(data.get("prompt_eval_count", 0), data.get("eval_count", 0), OLLAMA)
    return _normalize_message(data.get("message", {}) or {})


async def _ollama_stream(
    messages: list[dict], model: str, temperature: float, think: bool
) -> AsyncGenerator[str, None]:
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": think,
        "options": {"temperature": temperature},
    }
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                chunk = extract_json_object(line, {})
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    record_usage(
                        chunk.get("prompt_eval_count", 0), chunk.get("eval_count", 0), OLLAMA
                    )


# --------------------------------------------------------------------------- #
# OpenAI-compatible implementations
# --------------------------------------------------------------------------- #


def _openai_headers() -> dict:
    return {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}


async def _openai_once(
    messages: list[dict], model: str, tools: list[dict] | None, temperature: float
) -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set; cannot use the openai backend")
    payload: dict = {"model": model, "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{OPENAI_BASE_URL}/chat/completions", json=payload, headers=_openai_headers()
        )
        resp.raise_for_status()
        data = resp.json()
    usage = data.get("usage", {}) or {}
    record_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), OPENAI)
    choices = data.get("choices") or [{}]
    return _normalize_message(choices[0].get("message", {}) or {})


async def _openai_stream(
    messages: list[dict], model: str, temperature: float
) -> AsyncGenerator[str, None]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set; cannot use the openai backend")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST", f"{OPENAI_BASE_URL}/chat/completions", json=payload, headers=_openai_headers()
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                body = line[len("data:"):].strip()
                if body == "[DONE]":
                    break
                chunk = extract_json_object(body, {})
                usage = chunk.get("usage")
                if usage:
                    record_usage(
                        usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), OPENAI
                    )
                choices = chunk.get("choices") or []
                if choices:
                    piece = (choices[0].get("delta", {}) or {}).get("content", "")
                    if piece:
                        yield piece


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def _normalize_message(message: dict) -> dict:
    """Coerce an Ollama or OpenAI assistant message into one shape.

    Returns ``{"content": str, "tool_calls": [{"function": {"name", "arguments"}}]}``
    — exactly what ``coordinator._decision_from_message`` reads, so either backend
    flows through the existing decision parser unchanged. OpenAI already nests
    ``function`` with a JSON-string ``arguments``; Ollama nests ``function`` with a
    dict ``arguments`` — both are handled downstream.
    """
    content = message.get("content") or ""
    raw_calls = message.get("tool_calls") or []
    calls: list[dict] = []
    for call in raw_calls:
        fn = call.get("function", {}) or {}
        name = fn.get("name")
        if not name:
            continue
        calls.append({"function": {"name": name, "arguments": fn.get("arguments", {})}})
    normalized: dict[str, Any] = {"content": content}
    if calls:
        normalized["tool_calls"] = calls
    return normalized

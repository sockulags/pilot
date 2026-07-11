"""Model backend abstraction: local Ollama and OpenAI-compatible cloud providers.

Pilot is local-first — every model-driven call runs on Ollama by default. Three
mechanisms can route a call elsewhere, in precedence order:

1. **Run-level backend override** (``set_backend`` / the ``backend=`` argument)
   — used by the eval runner to force a whole suite onto one backend for a
   clean A/B. When active, role assignments and cloud model ids are ignored so
   the comparison stays pure.
2. **Cloud model ids** — a model id of the form ``cloud:<provider>:<model>``
   (see ``model_settings``) routes that single call to the named
   OpenAI-compatible provider. This is how per-role cloud assignments and cloud
   expert consults flow through the agent unchanged.
3. **Role assignments** — calls tagged with ``role=`` ("classifier", "gateway",
   "synthesis") consult the persisted model settings: an explicit assignment
   for the role wins, then the ``default_agent`` assignment ("default runs
   everything"), then the caller's model argument, then the env default.

Perception/vision and memory embeddings are NOT routed here — they stay local.

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
import json
import logging
from typing import Any, AsyncGenerator

import httpx

import model_settings
from agents.json_utils import extract_json_object
from agents.context_manager import ContextReport, is_context_overflow, manage_request
from agents.model_inventory import resolve_context_budget
from config import (
    ANSWER_BACKEND,
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


def backend_forced() -> bool:
    """True while a run-level override (eval --backend) is active."""
    return _backend_override is not None


def resolve_backend(backend: str | None = None) -> str:
    """Effective backend: explicit arg > run override > PILOT_ANSWER_BACKEND."""
    return _norm(backend or _backend_override or ANSWER_BACKEND)


def answer_model(backend: str | None = None, model: str | None = None) -> str:
    """Resolve the model id for a backend (legacy env path).

    On the OpenAI path a local (Ollama) model id passed by the agent (e.g.
    ``gemma4:12b``) is ignored in favour of ``OPENAI_MODEL`` — so the agent keeps
    passing its coordinator model unchanged and the backend swap "just works".
    """
    be = resolve_backend(backend)
    if be == OPENAI:
        if model and model not in OLLAMA_MODELS and not model_settings.is_cloud_model_id(model):
            return model  # an explicit OpenAI model id
        return OPENAI_MODEL
    return model or OLLAMA_MODEL


def openai_configured() -> bool:
    return bool(OPENAI_API_KEY)


def apply_role(model: str | None, role: str | None) -> str | None:
    """Effective model for a role-tagged call.

    Assignment for the role wins, then the ``default_agent`` assignment
    ("default runs everything"), then the caller's model argument (legacy env
    behaviour). Returns either an Ollama id or a ``cloud:...`` id.
    """
    if not role:
        return model
    assigned = model_settings.resolve_role_model(role)
    if assigned:
        return assigned
    default = model_settings.resolve_role_model("default_agent")
    if default:
        return default
    return model


def _cloud_route(model: str | None) -> tuple[dict, str] | None:
    """(provider entry, model name) when ``model`` is a resolvable cloud id."""
    parsed = model_settings.parse_cloud_model_id(model or "")
    if not parsed:
        return None
    provider_id, model_name = parsed
    entry = model_settings.cloud_provider(provider_id)
    if entry is None:
        logger.warning(
            "cloud model %r references unavailable provider %r; using local default",
            model, provider_id,
        )
        return None
    return entry, model_name


# --------------------------------------------------------------------------- #
# Per-turn token accounting (contextvar so nested calls accumulate)
# --------------------------------------------------------------------------- #

_usage: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_usage", default=None)
_context_reports: contextvars.ContextVar[list[ContextReport] | None] = contextvars.ContextVar(
    "context_reports", default=None
)


def reset_usage() -> None:
    _usage.set({"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "backend": None})
    _context_reports.set([])


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


def get_context_reports() -> list[ContextReport]:
    """Safe per-request diagnostics; reports contain counts, never removed text."""
    return list(_context_reports.get() or [])


def _record_context_report(report: ContextReport) -> None:
    reports = _context_reports.get()
    if reports is not None:
        reports.append(report)


def _response_is_overflow(response: httpx.Response) -> bool:
    if response.status_code < 400:
        return False
    body = getattr(response, "text", "") or ""
    try:
        body += " " + json.dumps(response.json(), sort_keys=True)
    except Exception:  # noqa: BLE001 - compatible response doubles vary
        pass
    if is_context_overflow(RuntimeError(body)):
        return True
    try:
        response.raise_for_status()
    except Exception as exc:  # test doubles and compatible clients may wrap HTTP errors
        return is_context_overflow(exc)
    return False


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
    role: str | None = None,
    context_role: str | None = None,
    fmt: str | None = None,
    schema: dict | None = None,
) -> dict:
    """One non-streamed turn. Returns a normalized ``{"content", "tool_calls"}``.

    ``fmt``/``schema`` request structured (JSON-constrained) output for a
    decision that expects a JSON object: ``fmt="json"`` asks for free-form JSON,
    ``schema=`` (a JSON schema dict) constrains the shape. Ollama takes these via
    the ``/api/chat`` ``format`` field; the OpenAI path maps them to
    ``response_format`` — but ONLY when no tools are being used (the two are
    mutually exclusive on the OpenAI side, and a native tool call already returns
    structured arguments). Both are additive hints: an endpoint that ignores them
    still returns prose the lenient ``extract_json_object`` fallback parses, so no
    current behaviour regresses.
    """
    if backend or backend_forced():
        # Eval A/B: force one backend, ignore role/cloud routing for purity.
        be = resolve_backend(backend)
        base = None if model_settings.is_cloud_model_id(model) else model
        if be == OPENAI:
            return await _openai_once(
                messages, answer_model(be, base), tools, temperature,
                OPENAI_BASE_URL, OPENAI_API_KEY, role=context_role or role,
                fmt=fmt, schema=schema,
            )
        return await _ollama_once(
            messages, answer_model(be, base), tools, temperature,
            role=context_role or role,
            fmt=fmt, schema=schema
        )

    model = apply_role(model, role)
    cloud = _cloud_route(model)
    if cloud:
        entry, model_name = cloud
        return await _openai_once(
            messages, model_name, tools, temperature,
            str(entry.get("base_url") or OPENAI_BASE_URL),
            model_settings.provider_api_key(entry), role=context_role or role,
            fmt=fmt, schema=schema,
        )
    if model_settings.is_cloud_model_id(model):
        model = None  # unresolvable cloud id — fall back to the local default
    if resolve_backend() == OPENAI:
        return await _openai_once(
            messages, answer_model(OPENAI, model), tools, temperature,
            OPENAI_BASE_URL, OPENAI_API_KEY, fmt=fmt, schema=schema,
            role=context_role or role,
        )
    return await _ollama_once(
        messages, model or OLLAMA_MODEL, tools, temperature,
        role=context_role or role,
        fmt=fmt, schema=schema
    )


async def chat_stream(
    messages: list[dict],
    model: str | None = None,
    *,
    temperature: float = 0.2,
    think: bool = False,
    backend: str | None = None,
    role: str | None = None,
    context_role: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream content deltas. ``think`` only affects the Ollama backend."""
    if backend or backend_forced():
        be = resolve_backend(backend)
        base = None if model_settings.is_cloud_model_id(model) else model
        if be == OPENAI:
            async for piece in _openai_stream(
                messages, answer_model(be, base), temperature,
                OPENAI_BASE_URL, OPENAI_API_KEY,
                role=context_role or role,
            ):
                yield piece
        else:
            async for piece in _ollama_stream(
                messages, answer_model(be, base), temperature, think,
                role=context_role or role
            ):
                yield piece
        return

    model = apply_role(model, role)
    cloud = _cloud_route(model)
    if cloud:
        entry, model_name = cloud
        async for piece in _openai_stream(
            messages, model_name, temperature,
            str(entry.get("base_url") or OPENAI_BASE_URL),
            model_settings.provider_api_key(entry),
            role=context_role or role,
        ):
            yield piece
        return
    if model_settings.is_cloud_model_id(model):
        model = None  # unresolvable cloud id — fall back to the local default
    if resolve_backend() == OPENAI:
        async for piece in _openai_stream(
            messages, answer_model(OPENAI, model), temperature,
            OPENAI_BASE_URL, OPENAI_API_KEY,
            role=context_role or role,
        ):
            yield piece
        return
    async for piece in _ollama_stream(
        messages, model or OLLAMA_MODEL, temperature, think,
        role=context_role or role
    ):
        yield piece


# --------------------------------------------------------------------------- #
# Ollama implementations (preserve existing behaviour, incl. tools 400-retry)
# --------------------------------------------------------------------------- #


async def _ollama_once(
    messages: list[dict],
    model: str,
    tools: list[dict] | None,
    temperature: float,
    *,
    role: str | None = None,
    fmt: str | None = None,
    schema: dict | None = None,
) -> dict:
    context_window = resolve_context_budget(model, role)
    managed = manage_request(messages, context_window=context_window, tools=tools)
    _record_context_report(managed.report)
    payload: dict = {
        "model": model,
        "messages": managed.messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_ctx": context_window,
            "num_predict": managed.report.completion_reserve,
        },
    }
    if managed.tools:
        payload["tools"] = managed.tools
    # Structured-output hint: /api/chat "format" is either "json" or a JSON schema
    # (schema wins when both are given). A model/endpoint that ignores it still
    # returns prose the caller's extract_json_object fallback parses.
    if schema is not None:
        payload["format"] = schema
    elif fmt:
        payload["format"] = fmt
    base_url = model_settings.ollama_base_url()
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        used_compat_retry = False
        if tools and resp.status_code >= 400 and not _response_is_overflow(resp):
            # Endpoint/model rejected the tools payload — retry as plain JSON.
            payload.pop("tools", None)
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            used_compat_retry = True
        # Strict two-attempt bound: compatibility fallback and overflow recovery
        # never stack into a third provider call. Overflow classification always
        # wins when deciding the first retry.
        if not used_compat_retry and resp.status_code >= 400 and _response_is_overflow(resp):
            retry = manage_request(
                messages, context_window=context_window, tools=payload.get("tools"),
                force_compact=True, retry=True,
                completion_reserve=managed.report.completion_reserve,
            )
            _record_context_report(retry.report)
            payload["messages"] = retry.messages
            payload["options"]["num_predict"] = retry.report.completion_reserve
            resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    record_usage(data.get("prompt_eval_count", 0), data.get("eval_count", 0), OLLAMA)
    return _normalize_message(data.get("message", {}) or {})


async def _ollama_stream(
    messages: list[dict], model: str, temperature: float, think: bool,
    *, role: str | None = None,
) -> AsyncGenerator[str, None]:
    context_window = resolve_context_budget(model, role)
    managed = manage_request(messages, context_window=context_window)
    _record_context_report(managed.report)
    payload = {
        "model": model,
        "messages": managed.messages,
        "stream": True,
        "think": think,
        "options": {
            "temperature": temperature,
            "num_ctx": context_window,
            "num_predict": managed.report.completion_reserve,
        },
    }
    base_url = model_settings.ollama_base_url()
    emitted = False
    async with httpx.AsyncClient(timeout=180) as client:
        for attempt in range(2):
            try:
                async with client.stream("POST", f"{base_url}/api/chat", json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = extract_json_object(line, {})
                        piece = chunk.get("message", {}).get("content", "")
                        if piece:
                            emitted = True
                            yield piece
                        if chunk.get("done"):
                            record_usage(
                                chunk.get("prompt_eval_count", 0), chunk.get("eval_count", 0), OLLAMA
                            )
                return
            except httpx.HTTPStatusError as exc:
                if attempt or emitted or not is_context_overflow(exc):
                    raise
                retry = manage_request(
                    messages, context_window=context_window, force_compact=True, retry=True,
                    completion_reserve=managed.report.completion_reserve,
                )
                _record_context_report(retry.report)
                payload["messages"] = retry.messages
                payload["options"]["num_predict"] = retry.report.completion_reserve


# --------------------------------------------------------------------------- #
# OpenAI-compatible implementations (env OpenAI or any configured cloud provider)
# --------------------------------------------------------------------------- #


def _openai_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


async def _openai_once(
    messages: list[dict],
    model: str,
    tools: list[dict] | None,
    temperature: float,
    base_url: str,
    api_key: str,
    *,
    role: str | None = None,
    fmt: str | None = None,
    schema: dict | None = None,
) -> dict:
    if not api_key:
        raise RuntimeError(
            "no API key configured for the OpenAI-compatible backend "
            "(set OPENAI_API_KEY or add a cloud provider on the settings page)"
        )
    context_window = resolve_context_budget(OLLAMA_MODEL, role)
    managed = manage_request(messages, context_window=context_window, tools=tools)
    _record_context_report(managed.report)
    payload: dict = {
        "model": model, "messages": managed.messages, "temperature": temperature,
        "max_tokens": managed.report.completion_reserve,
    }
    if managed.tools:
        payload["tools"] = managed.tools
        payload["tool_choice"] = "auto"
    elif schema is not None:
        # json_schema response format (tools and response_format are mutually
        # exclusive; a native tool call already yields structured arguments).
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "decision", "schema": schema},
        }
    elif fmt == "json":
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{base_url}/chat/completions", json=payload, headers=_openai_headers(api_key)
        )
        if resp.status_code >= 400 and _response_is_overflow(resp):
            retry = manage_request(
                messages, context_window=context_window, tools=tools,
                force_compact=True, retry=True,
                completion_reserve=managed.report.completion_reserve,
            )
            _record_context_report(retry.report)
            payload["messages"] = retry.messages
            payload["max_tokens"] = retry.report.completion_reserve
            resp = await client.post(
                f"{base_url}/chat/completions", json=payload, headers=_openai_headers(api_key)
            )
        resp.raise_for_status()
        data = resp.json()
    usage = data.get("usage", {}) or {}
    record_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), OPENAI)
    choices = data.get("choices") or [{}]
    return _normalize_message(choices[0].get("message", {}) or {})


async def _openai_stream(
    messages: list[dict],
    model: str,
    temperature: float,
    base_url: str,
    api_key: str,
    *,
    role: str | None = None,
) -> AsyncGenerator[str, None]:
    if not api_key:
        raise RuntimeError(
            "no API key configured for the OpenAI-compatible backend "
            "(set OPENAI_API_KEY or add a cloud provider on the settings page)"
        )
    context_window = resolve_context_budget(OLLAMA_MODEL, role)
    managed = manage_request(messages, context_window=context_window)
    _record_context_report(managed.report)
    payload = {
        "model": model,
        "messages": managed.messages,
        "temperature": temperature,
        "max_tokens": managed.report.completion_reserve,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    emitted = False
    async with httpx.AsyncClient(timeout=180) as client:
        for attempt in range(2):
            try:
                async with client.stream(
                    "POST", f"{base_url}/chat/completions", json=payload,
                    headers=_openai_headers(api_key),
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
                                emitted = True
                                yield piece
                return
            except httpx.HTTPStatusError as exc:
                if attempt or emitted or not is_context_overflow(exc):
                    raise
                retry = manage_request(
                    messages, context_window=context_window, force_compact=True, retry=True,
                    completion_reserve=managed.report.completion_reserve,
                )
                _record_context_report(retry.report)
                payload["messages"] = retry.messages
                payload["max_tokens"] = retry.report.completion_reserve


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

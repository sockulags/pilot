"""Privacy boundary and adapters for Pilot's local inference runtime.

The runtime configuration is immutable for the lifetime of a call.  Every
outbound local request re-validates the endpoint through the same authority;
an inbound Pilot auth token is deliberately irrelevant to this decision.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
from dataclasses import dataclass, field
from typing import AsyncGenerator, Literal
from urllib.parse import urlsplit

import httpx
from config import OLLAMA_MODELS


Capability = Literal["supported", "unsupported", "unknown"]
RuntimeKind = Literal["ollama", "openai_compatible"]


class LocalRuntimeError(RuntimeError):
    """A safe, typed local runtime failure (message contains no response body)."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RuntimeCapabilities:
    tools: Capability = "unknown"
    vision: Capability = "unknown"
    embeddings: Capability = "unknown"
    structured_output: Capability = "unknown"


@dataclass(frozen=True)
class LocalRuntimeConfig:
    kind: RuntimeKind = "ollama"
    base_url: str = "http://localhost:11434"
    api_key: str = ""
    api_key_env: str = ""
    allow_private_network: bool = False
    chat_model: str = ""
    vision_model: str = ""
    embedding_model: str = ""
    context_overrides: dict[str, int] = field(default_factory=dict)
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)

    @property
    def effective_key(self) -> str:
        return self.api_key or (os.getenv(self.api_key_env, "") if self.api_key_env else "")

    @property
    def fingerprint(self) -> str:
        # The secret itself is never included; whether credentials exist matters
        # because it changes the private-network authorization boundary.
        material = json.dumps({
            "kind": self.kind,
            "base_url": self.base_url.rstrip("/"),
            "credentialed": bool(self.effective_key),
            "allow_private_network": self.allow_private_network,
            "chat_model": self.chat_model,
            "vision_model": self.vision_model,
            "embedding_model": self.embedding_model,
            "context_overrides": self.context_overrides,
            "capabilities": self.capabilities.__dict__,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()[:24]


def _safe_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if address.is_loopback:
        return "loopback"
    if address.is_private and not (
        address.is_unspecified or address.is_link_local or address.is_multicast
        or address.is_reserved
    ):
        return "private"
    return "unsafe"


def validate_local_endpoint(config: LocalRuntimeConfig) -> str:
    """Validate and return a normalized base URL, failing closed.

    Loopback literals and ``localhost`` are accepted.  A non-loopback private
    literal requires both an explicit opt-in and a runtime credential.  Public,
    ambiguous, mixed-DNS, redirectable and proxy-routed endpoints are rejected.
    Non-loopback hostnames are rejected because httpx cannot securely pin the
    address between DNS validation and connection establishment.
    """
    raw = (config.base_url or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise LocalRuntimeError("unsafe_endpoint", "Local runtime URL has an invalid port") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LocalRuntimeError("unsafe_endpoint", "Local runtime URL must be an HTTP(S) URL")
    if port is None:
        raise LocalRuntimeError("unsafe_endpoint", "Local runtime URL must include an explicit port")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LocalRuntimeError("unsafe_endpoint", "Local runtime URL cannot contain credentials, query, or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise LocalRuntimeError("unsafe_endpoint", "Local runtime URL has an invalid port")
    host = parsed.hostname.rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        classes = {_safe_ip(literal)}
    else:
        # Do not accept arbitrary names even when their first lookup happens to
        # return loopback: httpx performs its own lookup later, which would
        # create a DNS-rebinding gap. `localhost` is the sole hostname form.
        if host != "localhost":
            raise LocalRuntimeError(
                "unsafe_endpoint", "Local runtime hostname must be localhost or a trusted IP literal"
            )
        try:
            resolved = {
                ipaddress.ip_address(row[4][0])
                for row in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise LocalRuntimeError("unsafe_endpoint", "Local runtime hostname could not be safely resolved") from exc
        if not resolved:
            raise LocalRuntimeError("unsafe_endpoint", "Local runtime hostname did not resolve")
        classes = {_safe_ip(address) for address in resolved}
        if classes != {"loopback"}:
            # Fail closed for mixed DNS and non-loopback DNS (DNS rebinding/TOCTOU).
            raise LocalRuntimeError("unsafe_endpoint", "Local runtime hostname must resolve only to loopback")

    if classes == {"private"}:
        if not config.allow_private_network or not config.effective_key:
            raise LocalRuntimeError(
                "unsafe_endpoint",
                "Private-network runtime requires explicit opt-in and a runtime credential",
            )
    elif classes != {"loopback"}:
        raise LocalRuntimeError("unsafe_endpoint", "Local runtime endpoint is not a trusted local address")
    return raw.rstrip("/")


def validate_cloud_endpoint(raw: str) -> str:
    """Reject SSRF targets for the settings cloud-provider test path."""
    try:
        parsed = urlsplit((raw or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise LocalRuntimeError("unsafe_endpoint", "Provider URL has an invalid port") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise LocalRuntimeError("unsafe_endpoint", "Cloud provider URL must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LocalRuntimeError("unsafe_endpoint", "Provider URL cannot contain credentials, query, or fragment")
    try:
        addresses = {
            ipaddress.ip_address(row[4][0])
            for row in socket.getaddrinfo(parsed.hostname, port or 443, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise LocalRuntimeError("unsafe_endpoint", "Provider hostname could not be safely resolved") from exc
    if not addresses or any(
        not address.is_global or address.is_private or address.is_loopback
        or address.is_link_local or address.is_multicast or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise LocalRuntimeError("unsafe_endpoint", "Cloud provider hostname resolved to a non-public address")
    return (raw or "").strip().rstrip("/")


def runtime_headers(config: LocalRuntimeConfig) -> dict[str, str]:
    """Headers for any local-runtime request; secrets never enter diagnostics."""
    headers = {"Content-Type": "application/json"}
    if config.effective_key:
        headers["Authorization"] = f"Bearer {config.effective_key}"
    return headers


def client(timeout: float) -> httpx.AsyncClient:
    """A no-proxy, no-redirect client for all local-runtime traffic."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)


def normalize_error(exc: Exception) -> LocalRuntimeError:
    if isinstance(exc, LocalRuntimeError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return LocalRuntimeError("timeout", "Local runtime timed out")
    if isinstance(exc, httpx.ConnectError):
        return LocalRuntimeError("unreachable", "Local runtime is unreachable")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = (exc.response.text or "").lower()
        if status in {400, 413, 422} and any(term in body for term in (
            "context length", "context window", "too many tokens", "maximum context",
        )):
            return LocalRuntimeError("context_overflow", "Local runtime context window was exceeded")
        if status in {401, 403}:
            return LocalRuntimeError("auth", "Local runtime rejected its credential")
        if status == 404:
            return LocalRuntimeError("model_missing", "Local runtime model or endpoint was not found")
        if status == 429:
            return LocalRuntimeError("rate_limit", "Local runtime rate limit reached")
        return LocalRuntimeError("provider_error", f"Local runtime returned HTTP {status}")
    return LocalRuntimeError("invalid_response", "Local runtime returned an invalid response")


def require_capability(config: LocalRuntimeConfig, capability: str) -> None:
    state = getattr(config.capabilities, capability)
    if config.kind == "ollama" and state == "unknown":
        return  # Ollama has authoritative /api/show probing at each consumer.
    if state != "supported":
        raise LocalRuntimeError(
            "unsupported_capability",
            f"Local runtime capability '{capability}' is {state}; configure or verify it first",
        )


_CAPABILITY_CACHE: dict[tuple[str, str, str], Capability] = {}


async def ensure_capability(
    config: LocalRuntimeConfig, model: str, capability: str,
) -> None:
    """Fail-closed capability authority keyed by runtime fingerprint + model."""
    configured = getattr(config.capabilities, capability)
    if configured != "unknown":
        require_capability(config, capability)
        return
    registry_name = "embedding" if capability == "embeddings" else capability
    if config.kind == "ollama" and OLLAMA_MODELS.get(model, {}).get(registry_name) is True:
        return
    if config.kind != "ollama":
        require_capability(config, capability)
        return
    key = (config.fingerprint, model, capability)
    state = _CAPABILITY_CACHE.get(key)
    if state is None:
        base = validate_local_endpoint(config)
        try:
            async with client(10) as http:
                if config.effective_key:
                    response = await http.post(
                        base + "/api/show", json={"model": model},
                        headers=runtime_headers(config),
                    )
                else:
                    response = await http.post(base + "/api/show", json={"model": model})
                response.raise_for_status()
                caps = response.json().get("capabilities")
            state = (
                "supported" if isinstance(caps, list) and registry_name in caps
                else "unsupported" if isinstance(caps, list) else "unknown"
            )
        except Exception as exc:  # noqa: BLE001
            raise normalize_error(exc) from exc
        _CAPABILITY_CACHE[key] = state
    if state != "supported":
        raise LocalRuntimeError(
            "unsupported_capability",
            f"Local runtime capability '{capability}' is {state} for model {model!r}",
        )


def openai_messages(messages: list[dict]) -> list[dict]:
    """Convert Ollama image arrays to OpenAI-compatible data-URL content."""
    converted: list[dict] = []
    for message in messages:
        item = {k: v for k, v in message.items() if k != "images"}
        images = message.get("images") or []
        if images:
            parts: list[dict] = [{"type": "text", "text": str(message.get("content") or "")}]
            parts.extend({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image}"},
            } for image in images)
            item["content"] = parts
        converted.append(item)
    return converted


async def discover(config: LocalRuntimeConfig) -> list[str]:
    base = validate_local_endpoint(config)
    path = "/api/tags" if config.kind == "ollama" else "/models"
    try:
        async with client(10) as http:
            headers = runtime_headers(config)
            response = await http.get(base + path, headers=headers) if config.effective_key else await http.get(base + path)
            if 300 <= getattr(response, "status_code", 200) < 400:
                raise LocalRuntimeError("unsafe_endpoint", "Local runtime redirects are not allowed")
            response.raise_for_status()
            data = response.json()
        rows = data.get("models", []) if config.kind == "ollama" else data.get("data", [])
        return [str(row.get("name") or row.get("id")) for row in rows if isinstance(row, dict) and (row.get("name") or row.get("id"))]
    except Exception as exc:  # noqa: BLE001
        raise normalize_error(exc) from exc


async def embed(config: LocalRuntimeConfig, model: str, texts: list[str]) -> list[list[float]]:
    await ensure_capability(config, model, "embeddings")
    base = validate_local_endpoint(config)
    path = "/api/embed" if config.kind == "ollama" else "/embeddings"
    payload = {"model": model, "input": texts}
    try:
        async with client(30) as http:
            response = await http.post(base + path, json=payload, headers=runtime_headers(config))
            if 300 <= getattr(response, "status_code", 200) < 400:
                raise LocalRuntimeError("unsafe_endpoint", "Local runtime redirects are not allowed")
            response.raise_for_status()
            data = response.json()
        if config.kind == "ollama":
            vectors = data.get("embeddings") or []
        else:
            vectors = [row.get("embedding") for row in sorted(data.get("data") or [], key=lambda row: row.get("index", 0))]
        if not vectors or any(not isinstance(vector, list) for vector in vectors):
            raise LocalRuntimeError("invalid_response", "Local runtime returned invalid embeddings")
        return vectors
    except Exception as exc:  # noqa: BLE001
        raise normalize_error(exc) from exc


async def openai_chat_once(
    config: LocalRuntimeConfig, payload: dict,
) -> tuple[dict, dict]:
    base = validate_local_endpoint(config)
    try:
        async with client(120) as http:
            response = await http.post(base + "/chat/completions", json=payload, headers=runtime_headers(config))
            if 300 <= getattr(response, "status_code", 200) < 400:
                raise LocalRuntimeError("unsafe_endpoint", "Local runtime redirects are not allowed")
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise LocalRuntimeError("invalid_response", "Local runtime returned no assistant message")
        return choices[0]["message"], data.get("usage") or {}
    except Exception as exc:  # noqa: BLE001
        raise normalize_error(exc) from exc


async def openai_chat_stream(config: LocalRuntimeConfig, payload: dict) -> AsyncGenerator[dict, None]:
    base = validate_local_endpoint(config)
    try:
        async with client(180) as http:
            async with http.stream("POST", base + "/chat/completions", json=payload, headers=runtime_headers(config)) as response:
                if 300 <= getattr(response, "status_code", 200) < 400:
                    raise LocalRuntimeError("unsafe_endpoint", "Local runtime redirects are not allowed")
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        return
                    try:
                        yield json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise LocalRuntimeError("invalid_response", "Local runtime returned malformed SSE") from exc
    except Exception as exc:  # noqa: BLE001
        raise normalize_error(exc) from exc

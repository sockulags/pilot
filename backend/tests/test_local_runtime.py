"""Deterministic contracts for pluggable local runtimes and privacy boundary."""

from __future__ import annotations

import asyncio
import json
import socket
from unittest import mock

import httpx
import pytest

import model_settings
import memory
from agents import local_runtime, providers, router


def cfg(url: str, **kwargs) -> local_runtime.LocalRuntimeConfig:
    return local_runtime.LocalRuntimeConfig(base_url=url, **kwargs)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434", "http://127.9.8.7:1234/v1",
    "http://[::1]:8080/v1", "http://localhost:11434",
])
def test_endpoint_accepts_loopback(url):
    assert local_runtime.validate_local_endpoint(cfg(url)) == url


@pytest.mark.parametrize("url", [
    "http://0.0.0.0:1", "http://[::]:1", "http://169.254.169.254/latest",
    "http://224.0.0.1:1", "http://192.0.2.1:1", "https://8.8.8.8/v1",
    "http://user:pass@localhost:1", "http://localhost:1/?x=y",
    "http://localhost:1/#x", "http://localhost:99999", "ftp://localhost:1",
    "http://localhost",
])
def test_endpoint_rejects_unsafe_matrix(url):
    with pytest.raises(local_runtime.LocalRuntimeError) as caught:
        local_runtime.validate_local_endpoint(cfg(url))
    assert caught.value.code == "unsafe_endpoint"


def test_private_literal_requires_both_opt_in_and_runtime_credential():
    url = "http://192.168.1.20:1234/v1"
    for options in ({}, {"allow_private_network": True}, {"api_key": "runtime-secret"}):
        with pytest.raises(local_runtime.LocalRuntimeError):
            local_runtime.validate_local_endpoint(cfg(url, **options))
    assert local_runtime.validate_local_endpoint(cfg(
        url, allow_private_network=True, api_key="runtime-secret",
    )) == url


def test_mixed_dns_dns_failure_and_rebinding_fail_closed(monkeypatch):
    def mixed(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
        ]
    monkeypatch.setattr(socket, "getaddrinfo", mixed)
    with pytest.raises(local_runtime.LocalRuntimeError):
        local_runtime.validate_local_endpoint(cfg("http://runtime.test:80"))


def test_attacker_hostname_resolving_to_loopback_is_rejected_before_dns(monkeypatch):
    resolver = mock.Mock(return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1234)),
    ])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    with pytest.raises(local_runtime.LocalRuntimeError) as caught:
        local_runtime.validate_local_endpoint(cfg("http://attacker.example:1234/v1"))
    assert caught.value.code == "unsafe_endpoint"
    resolver.assert_not_called()


def test_localhost_must_resolve_only_to_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1234)),
    ])
    assert local_runtime.validate_local_endpoint(cfg("http://localhost:1234/v1"))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1234)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 1234)),
    ])
    with pytest.raises(local_runtime.LocalRuntimeError):
        local_runtime.validate_local_endpoint(cfg("http://localhost:1234/v1"))
    monkeypatch.setattr(socket, "getaddrinfo", mock.Mock(side_effect=socket.gaierror()))
    with pytest.raises(local_runtime.LocalRuntimeError):
        local_runtime.validate_local_endpoint(cfg("http://runtime.test:80"))


def test_runtime_fingerprint_changes_for_endpoint_model_and_capability():
    base = cfg("http://127.0.0.1:1234/v1", kind="openai_compatible")
    changed = [
        cfg("http://127.0.0.1:8080/v1", kind="openai_compatible"),
        cfg(base.base_url, kind="openai_compatible", chat_model="other"),
        cfg(base.base_url, kind="openai_compatible", capabilities=local_runtime.RuntimeCapabilities(tools="supported")),
    ]
    assert all(item.fingerprint != base.fingerprint for item in changed)


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False
    async def get(self, url, headers=None): return self.handler("GET", url, None, headers)
    async def post(self, url, json=None, headers=None): return self.handler("POST", url, json, headers)


def response(url, payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


def test_openai_local_discovery_once_embeddings_multimodal_and_auth(monkeypatch):
    calls = []
    def handler(method, url, payload, headers):
        calls.append((method, url, payload, headers))
        if url.endswith("/models"):
            return response(url, {"data": [{"id": "local-model"}]})
        if url.endswith("/embeddings"):
            return response(url, {"data": [{"index": 0, "embedding": [1.0, 2.0]}]})
        return response(url, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}})
    monkeypatch.setattr(local_runtime, "client", lambda *_args: FakeClient(handler))
    runtime = cfg(
        "http://127.0.0.1:1234/v1", kind="openai_compatible", api_key="local-secret",
        capabilities=local_runtime.RuntimeCapabilities(
            tools="supported", vision="supported", embeddings="supported",
            structured_output="supported",
        ),
    )
    assert asyncio.run(local_runtime.discover(runtime)) == ["local-model"]
    assert asyncio.run(local_runtime.embed(runtime, "embed", ["hello"])) == [[1.0, 2.0]]
    message, usage = asyncio.run(local_runtime.openai_chat_once(runtime, {
        "model": "local-model",
        "messages": local_runtime.openai_messages([{"role": "user", "content": "see", "images": ["AAAA"]}]),
    }))
    assert message["content"] == "ok" and usage["prompt_tokens"] == 3
    assert any(call[2] and "data:image/png;base64,AAAA" in json.dumps(call[2]) for call in calls)
    assert all(call[3]["Authorization"] == "Bearer local-secret" for call in calls)


def test_unknown_vision_and_embedding_make_zero_requests(monkeypatch):
    sent = []
    monkeypatch.setattr(local_runtime, "client", lambda *_args: FakeClient(lambda *args: sent.append(args)))
    runtime = cfg("http://127.0.0.1:1234/v1", kind="openai_compatible")
    with pytest.raises(local_runtime.LocalRuntimeError) as vision:
        local_runtime.require_capability(runtime, "vision")
    with pytest.raises(local_runtime.LocalRuntimeError) as embedding:
        asyncio.run(local_runtime.embed(runtime, "embed", ["private text"]))
    assert vision.value.code == embedding.value.code == "unsupported_capability"
    assert sent == []


def test_v1_settings_migrate_and_local_key_is_masked_and_preserved():
    saved, errors = model_settings.save_settings({
        "version": 1, "ollama": {"base_url": "http://localhost:11434"}, "roles": {},
    })
    assert errors == [] and saved["version"] == 2
    assert saved["local_runtime"]["kind"] == "ollama"
    raw = saved
    raw["local_runtime"].update({"api_key": "local-secret-1234"})
    saved, errors = model_settings.save_settings(raw)
    assert errors == []
    masked = model_settings.masked_settings(saved)
    assert masked["local_runtime"]["api_key"] == ""
    assert masked["local_runtime"]["key_hint"] == "…1234"
    merged = model_settings.apply_client_update(masked)
    assert merged["local_runtime"]["api_key"] == "local-secret-1234"


def test_generic_local_role_routes_to_openai_contract_without_stream_options(monkeypatch):
    captured = []
    runtime = cfg(
        "http://127.0.0.1:1234/v1", kind="openai_compatible", chat_model="local-model",
        capabilities=local_runtime.RuntimeCapabilities(tools="supported"),
    )
    monkeypatch.setattr(model_settings, "local_runtime_snapshot", lambda *_args: runtime)
    async def once(_runtime, payload):
        captured.append(payload)
        return {"content": "ok"}, {"prompt_tokens": 1, "completion_tokens": 1}
    monkeypatch.setattr(local_runtime, "openai_chat_once", once)
    result = asyncio.run(providers.chat_once(
        [{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x"}}],
    ))
    assert result["content"] == "ok"
    assert captured[0]["model"] == "local-model"
    assert captured[0]["max_tokens"] > 0


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234/v1",  # LM Studio
    "http://127.0.0.1:8080/v1",  # llama.cpp
])
def test_openai_local_presets_support_tools_and_structured_output(monkeypatch, url):
    captured = []
    runtime = cfg(
        url, kind="openai_compatible", chat_model="loaded-model",
        capabilities=local_runtime.RuntimeCapabilities(
            tools="supported", structured_output="supported",
        ),
    )
    monkeypatch.setattr(model_settings, "local_runtime_snapshot", lambda *_args: runtime)
    async def once(_runtime, payload):
        captured.append(payload)
        return {"content": "{}"}, {}
    monkeypatch.setattr(local_runtime, "openai_chat_once", once)
    asyncio.run(providers.chat_once(
        [{"role": "user", "content": "call"}],
        tools=[{"type": "function", "function": {"name": "read_file"}}],
    ))
    asyncio.run(providers.chat_once(
        [{"role": "user", "content": "json"}], fmt="json",
    ))
    assert captured[0]["tools"][0]["function"]["name"] == "read_file"
    assert captured[0]["tool_choice"] == "auto"
    assert captured[1]["response_format"] == {"type": "json_object"}


class FakeStreamResponse:
    status_code = 200
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False
    def raise_for_status(self): return None
    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\\"path\\\":"}}]}}]}'
        yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\\"README.md\\\"}"}}]}}],"usage":{"prompt_tokens":4,"completion_tokens":2}}'
        yield "data: [DONE]"


class FakeStreamClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False
    def stream(self, *_args, **_kwargs): return FakeStreamResponse()


def test_openai_sse_done_usage_and_fragmented_tool_arguments_are_preserved(monkeypatch):
    monkeypatch.setattr(local_runtime, "client", lambda *_args: FakeStreamClient())
    runtime = cfg("http://127.0.0.1:8080/v1", kind="openai_compatible")
    async def collect():
        return [chunk async for chunk in local_runtime.openai_chat_stream(runtime, {"stream": True})]
    chunks = asyncio.run(collect())
    fragments = [
        chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
        for chunk in chunks
    ]
    assert "".join(fragments) == '{"path":"README.md"}'
    assert chunks[-1]["usage"] == {"prompt_tokens": 4, "completion_tokens": 2}


def test_screenshot_uses_local_generic_runtime_and_data_url_only(monkeypatch):
    runtime = cfg(
        "http://127.0.0.1:1234/v1", kind="openai_compatible", vision_model="vision-local",
        context_overrides={"vision-local": 8192},
        capabilities=local_runtime.RuntimeCapabilities(vision="supported"),
    )
    monkeypatch.setattr(model_settings, "local_runtime_snapshot", lambda *_args: runtime)
    captured = []
    async def once(_runtime, payload):
        captured.append(payload)
        return {"content": "local description"}, {}
    monkeypatch.setattr(local_runtime, "openai_chat_once", once)
    result = asyncio.run(router.analyze_screenshot("task", "PRIVATE_IMAGE_B64", []))
    assert result == "local description"
    serialized = json.dumps(captured)
    assert "http://127.0.0.1:1234/v1" not in serialized  # URL stays adapter metadata
    assert "data:image/png;base64,PRIVATE_IMAGE_B64" in serialized
    assert "cloud:" not in serialized


def test_embedding_sidecar_invalidates_when_runtime_fingerprint_changes(monkeypatch, tmp_path):
    path = str(tmp_path / "memory.json")
    monkeypatch.setattr(memory, "MEMORY_FILE", path)
    first = cfg("http://127.0.0.1:11434", embedding_model="embed-a")
    second = cfg("http://127.0.0.2:11434", embedding_model="embed-a")
    monkeypatch.setattr(model_settings, "local_runtime_snapshot", lambda *_args: first)
    memory._write_json_atomic(memory._embeddings_file(), {
        "version": 2, "fingerprint": memory._embedding_fingerprint(),
        "vectors": {"id": [1.0, 2.0]},
    })
    assert memory._load_embeddings() == {"id": [1.0, 2.0]}
    monkeypatch.setattr(model_settings, "local_runtime_snapshot", lambda *_args: second)
    assert memory._load_embeddings() == {}


def test_redirect_response_is_rejected(monkeypatch):
    def handler(_method, url, _payload, _headers):
        return response(url, {}, status=302)
    monkeypatch.setattr(local_runtime, "client", lambda *_args: FakeClient(handler))
    runtime = cfg("http://127.0.0.1:1234/v1", kind="openai_compatible")
    with pytest.raises(local_runtime.LocalRuntimeError) as caught:
        asyncio.run(local_runtime.discover(runtime))
    assert caught.value.code == "unsafe_endpoint"


def test_authenticated_ollama_show_chat_and_vision_send_runtime_authorization(monkeypatch):
    calls = []
    def handler(method, url, payload, headers):
        calls.append((method, url, payload, headers))
        if url.endswith("/api/show"):
            return response(url, {"capabilities": ["tools"]})
        return response(url, {
            "message": {"content": "ok"}, "prompt_eval_count": 2, "eval_count": 1,
        })
    monkeypatch.setattr(local_runtime, "client", lambda *_args: FakeClient(handler))
    runtime = cfg(
        "http://127.0.0.1:11434", api_key="ollama-runtime-secret",
        vision_model="qwen3.5:9b", context_overrides={"qwen3.5:9b": 8192},
    )
    monkeypatch.setattr(model_settings, "local_runtime_snapshot", lambda *_args: runtime)
    asyncio.run(local_runtime.ensure_capability(runtime, "custom-tools:model", "tools"))
    asyncio.run(providers.chat_once(
        [{"role": "user", "content": "hello"}], "gemma4:12b", backend="ollama",
    ))
    assert asyncio.run(router.analyze_screenshot("task", "IMAGE", [])) == "ok"
    relevant = [call for call in calls if call[1].endswith(("/api/show", "/api/chat"))]
    assert len(relevant) >= 3
    assert all(call[3]["Authorization"] == "Bearer ollama-runtime-secret" for call in relevant)


class AuthOllamaStreamResponse:
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False
    def raise_for_status(self): return None
    async def aiter_lines(self):
        yield '{"message":{"content":"ok"},"done":false}'
        yield '{"done":true,"prompt_eval_count":2,"eval_count":1}'


class AuthOllamaStreamClient:
    def __init__(self): self.headers = None
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False
    def stream(self, _method, _url, *, json, headers=None):
        self.headers = headers
        return AuthOllamaStreamResponse()


def test_authenticated_ollama_stream_sends_runtime_authorization(monkeypatch):
    client = AuthOllamaStreamClient()
    runtime = cfg("http://127.0.0.1:11434", api_key="ollama-stream-secret")
    monkeypatch.setattr(local_runtime, "client", lambda *_args: client)
    monkeypatch.setattr(model_settings, "local_runtime_snapshot", lambda *_args: runtime)
    async def collect():
        return [piece async for piece in providers.chat_stream(
            [{"role": "user", "content": "hello"}], "gemma4:12b", backend="ollama",
        )]
    assert asyncio.run(collect()) == ["ok"]
    assert client.headers["Authorization"] == "Bearer ollama-stream-secret"


def test_runtime_secret_never_appears_in_normalized_error_or_telemetry(monkeypatch):
    secret = "DO_NOT_LEAK_RUNTIME_SECRET"
    runtime = cfg(
        "http://127.0.0.1:1234/v1", kind="openai_compatible", api_key=secret,
        chat_model="local-model",
    )
    def handler(_method, url, _payload, _headers):
        return httpx.Response(
            401, text=f"bad key {secret} and PRIVATE_IMAGE_DATA",
            request=httpx.Request("POST", url),
        )
    monkeypatch.setattr(local_runtime, "client", lambda *_args: FakeClient(handler))
    providers.reset_usage()
    with pytest.raises(local_runtime.LocalRuntimeError) as caught:
        asyncio.run(local_runtime.openai_chat_once(runtime, {
            "model": "local-model", "messages": [{"role": "user", "content": "private"}],
        }))
    safe = f"{caught.value.code}: {caught.value} {providers.get_context_reports()}"
    assert caught.value.code == "auth"
    assert secret not in safe and "PRIVATE_IMAGE_DATA" not in safe

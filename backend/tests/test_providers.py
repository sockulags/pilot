"""Unit tests for the model backend provider (agents/providers.py). No network."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents import providers  # noqa: E402


def teardown_function(_):
    providers.set_backend(None)  # never leak an override between tests


# --------------------------------------------------------------------------- #
# backend + model resolution
# --------------------------------------------------------------------------- #


def test_resolve_backend_default_is_ollama():
    providers.set_backend(None)
    assert providers.resolve_backend() == providers.OLLAMA


def test_explicit_arg_beats_override():
    providers.set_backend("openai")
    assert providers.resolve_backend() == providers.OPENAI
    assert providers.resolve_backend("ollama") == providers.OLLAMA


def test_unknown_backend_falls_back_to_ollama():
    assert providers.resolve_backend("banana") == providers.OLLAMA
    providers.set_backend("banana")
    assert providers.resolve_backend() == providers.OLLAMA


def test_answer_model_ollama_passthrough():
    assert providers.answer_model("ollama", "gemma4:12b") == "gemma4:12b"
    assert providers.answer_model("ollama", None) == providers.OLLAMA_MODEL


def test_answer_model_openai_ignores_local_id():
    # A local Ollama id passed by the agent must not become the OpenAI model.
    assert providers.answer_model("openai", "gemma4:12b") == providers.OPENAI_MODEL
    # An explicit non-local (OpenAI) id is honoured.
    assert providers.answer_model("openai", "gpt-4o") == "gpt-4o"
    assert providers.answer_model("openai", None) == providers.OPENAI_MODEL


# --------------------------------------------------------------------------- #
# normalization
# --------------------------------------------------------------------------- #


def test_normalize_content_only():
    assert providers._normalize_message({"content": "hi"}) == {"content": "hi"}
    assert providers._normalize_message({}) == {"content": ""}


def test_normalize_ollama_tool_call_dict_args():
    msg = {"content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a"}}}]}
    out = providers._normalize_message(msg)
    assert out["tool_calls"] == [{"function": {"name": "read_file", "arguments": {"path": "a"}}}]


def test_normalize_openai_tool_call_str_args():
    msg = {"content": None, "tool_calls": [
        {"id": "x", "type": "function",
         "function": {"name": "run_command", "arguments": '{"cmd": "echo hi"}'}}]}
    out = providers._normalize_message(msg)
    assert out["content"] == ""
    assert out["tool_calls"][0]["function"]["name"] == "run_command"
    assert out["tool_calls"][0]["function"]["arguments"] == '{"cmd": "echo hi"}'


def test_normalize_drops_nameless_tool_calls():
    msg = {"content": "x", "tool_calls": [{"function": {"arguments": {}}}]}
    out = providers._normalize_message(msg)
    assert "tool_calls" not in out


# --------------------------------------------------------------------------- #
# token accounting
# --------------------------------------------------------------------------- #


def test_usage_accumulates_and_resets():
    providers.reset_usage()
    providers.record_usage(10, 5, providers.OPENAI)
    providers.record_usage(3, 2, providers.OPENAI)
    u = providers.get_usage()
    assert u["prompt_tokens"] == 13
    assert u["completion_tokens"] == 7
    assert u["calls"] == 2
    assert u["backend"] == providers.OPENAI
    providers.reset_usage()
    assert providers.get_usage()["prompt_tokens"] == 0


# --------------------------------------------------------------------------- #
# OpenAI client (httpx mocked)
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp):
        self._resp = resp
        self.posted = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.posted = {"url": url, "json": json, "headers": headers}
        return self._resp


def test_openai_once_normalizes_and_records_usage(monkeypatch):
    payload = {
        "choices": [{"message": {"content": "hello",
                                 "tool_calls": [{"function": {"name": "list_dir",
                                                              "arguments": '{"path": "."}'}}]}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
    }
    client = _Client(_Resp(payload))
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(providers, "OPENAI_API_KEY", "sk-test")
    providers.reset_usage()

    out = asyncio.run(providers.chat_once(
        [{"role": "user", "content": "hi"}], "gemma4:12b",
        tools=[{"type": "function", "function": {"name": "list_dir"}}],
        backend="openai",
    ))
    assert out["content"] == "hello"
    assert out["tool_calls"][0]["function"]["name"] == "list_dir"
    # It hit the OpenAI endpoint with the local model id swapped for OPENAI_MODEL.
    assert client.posted["url"].endswith("/chat/completions")
    assert client.posted["json"]["model"] == providers.OPENAI_MODEL
    assert client.posted["json"]["tool_choice"] == "auto"
    u = providers.get_usage()
    assert u["prompt_tokens"] == 20 and u["completion_tokens"] == 8 and u["backend"] == "openai"


def test_openai_once_requires_key(monkeypatch):
    monkeypatch.setattr(providers, "OPENAI_API_KEY", "")
    with_err = None
    try:
        asyncio.run(providers.chat_once([{"role": "user", "content": "x"}], backend="openai"))
    except RuntimeError as exc:
        with_err = str(exc)
    assert with_err and "OPENAI_API_KEY" in with_err

"""Deterministic local-runtime compatibility matrix (no sockets or model server)."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from contextlib import asynccontextmanager

import httpx
import pytest

from agents import local_runtime, providers
from agents.context_manager import (
    ContextBudgetError,
    estimate_message_tokens,
    manage_request,
)
from tests.eval.compatibility import (
    REPORT_SCHEMA_VERSION,
    canonical_report,
    comparison_key,
    contract_rows,
    scenario_ids,
    scenario_rows,
)


pytestmark = pytest.mark.eval


class Response:
    def __init__(self, status: int, data: dict):
        self.status_code = status
        self._data = data
        self.text = json.dumps(data)
        self.request = httpx.Request("POST", "http://localhost:1234/v1/chat/completions")
        self.response = self

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("request failed", request=self.request, response=self)


class ScriptedClient:
    def __init__(self, responses, calls):
        self.responses = responses
        self.calls = calls

    async def post(self, url, **kwargs):
        self.calls.append((url, copy.deepcopy(kwargs)))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class StreamResponse(Response):
    def __init__(self, lines):
        super().__init__(200, {})
        self.lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class StreamingClient:
    def __init__(self, lines):
        self.lines = lines

    def stream(self, _method, _url, **_kwargs):
        return StreamResponse(self.lines)


def runtime(**kwargs):
    caps = local_runtime.RuntimeCapabilities(
        tools="supported", vision="supported", embeddings="supported",
        structured_output="supported",
    )
    return local_runtime.LocalRuntimeConfig(
        kind="openai_compatible", base_url="http://localhost:1234/v1",
        chat_model="fixture-model", context_overrides={"fixture-model": 8192},
        capabilities=caps, **kwargs,
    )


def install_client(monkeypatch, responses):
    calls = []
    scripted = list(responses)

    @asynccontextmanager
    async def fake_client(_timeout):
        yield ScriptedClient(scripted, calls)

    monkeypatch.setattr(local_runtime, "client", fake_client)
    return calls


def test_declarative_matrix_and_live_report_comparison_are_canonical():
    assert {row["id"] for row in contract_rows()} == {
        "ollama", "lm-studio-openai", "llama-cpp-openai",
    }
    required = {
        "text", "native_tool", "below_budget", "above_budget", "vision_long_8192",
        "large_tool_schema", "overflow_retry_once", "overflow_twice", "embeddings",
    }
    assert required <= set(scenario_ids())
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run": {
            "timestamp_utc": "a", "git_sha": "1", "git_dirty": True,
            "source_digest": "source-a",
        },
        "profiles": [{
            "id": "ollama", "availability": "available",
            "availability_detail": "volatile failure detail",
            "model": {"id": "m", "digest": "digest-a"},
            "scenarios": [{
                "id": "text", "verdict": "supported", "latency_ms": 1,
                "token_usage": {"prompt_tokens": 3}, "detail": "first",
                "budget": {"prompt_budget": 100},
            }],
        }],
    }
    report["comparison_key"] = comparison_key(report)
    assert report["comparison_key"] == comparison_key(report)
    later = copy.deepcopy(report)
    later["run"].update(timestamp_utc="b", git_sha="2", git_dirty=False)
    later["profiles"][0]["availability_detail"] = "different"
    scenario = later["profiles"][0]["scenarios"][0]
    scenario.update(latency_ms=999, token_usage={"prompt_tokens": 999}, detail="later")
    assert canonical_report(report) == canonical_report(later)
    assert comparison_key(report) == comparison_key(later)
    for mutate in (
        lambda value: value["profiles"][0]["scenarios"][0].update(verdict="failed"),
        lambda value: value["profiles"][0]["scenarios"][0]["budget"].update(prompt_budget=101),
        lambda value: value["profiles"][0]["model"].update(digest="digest-b"),
        lambda value: value["run"].update(source_digest="source-b"),
    ):
        changed = copy.deepcopy(report)
        mutate(changed)
        assert comparison_key(changed) != report["comparison_key"]
    assert scenario_rows() == sorted(scenario_rows(), key=lambda row: scenario_ids().index(row["id"]))


def test_budget_boundaries_reserve_schema_and_request_immutability():
    messages = [
        {"role": "system", "content": "contract", "context_kind": "safety"},
        {"role": "assistant", "content": "old " * 1600},
        {"role": "user", "content": "active task", "context_kind": "active_task"},
    ]
    tools = [{"type": "function", "function": {
        "name": "big", "description": "schema " * 900,
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}}},
    }}]
    original = copy.deepcopy((messages, tools))
    managed = manage_request(
        messages, tools=tools, context_window=4096, completion_reserve=777,
    )
    assert managed.report.completion_reserve == 777
    assert managed.report.prompt_budget == 3319
    assert managed.report.tool_schema_tokens > 1000
    assert managed.report.estimated_prompt_tokens <= managed.report.prompt_budget
    assert (messages, tools) == original

    impossible = [{"role": "user", "content": "mandatory " * 3000, "context_kind": "active_task"}]
    with pytest.raises(ContextBudgetError):
        manage_request(impossible, context_window=4096, completion_reserve=1024)


def test_mandatory_active_task_exact_prompt_budget_fits_and_plus_one_fails_closed():
    context_window, reserve = 4096, 1024
    prompt_budget = context_window - reserve

    def active_task_at(target: int) -> dict:
        # Search content byte lengths through the production estimator. Its
        # ceil(bytes/3) charge reaches every integer token boundary.
        for length in range(target * 3 - 256, target * 3 + 256):
            message = {
                "role": "user", "content": "x" * length,
                "context_kind": "active_task",
            }
            if estimate_message_tokens(message)[0] == target:
                return message
        raise AssertionError(f"could not construct exact estimate {target}")

    exact = active_task_at(prompt_budget)
    managed = manage_request(
        [exact], context_window=context_window, completion_reserve=reserve,
    )
    assert managed.report.estimated_prompt_tokens == prompt_budget
    assert managed.report.prompt_budget == prompt_budget

    above = active_task_at(prompt_budget + 1)
    with pytest.raises(ContextBudgetError, match="mandatory context"):
        manage_request([above], context_window=context_window, completion_reserve=reserve)


def test_realistic_full_screen_image_plus_long_task_exceeds_4096_and_fits_8192():
    # The binary bytes are irrelevant to deterministic budgeting; one real
    # full-screen capture is conservatively charged as 4096 media tokens.
    messages = [{
        "role": "user", "content": "Inspect the 1920x1080 screen. " + "detail " * 300,
        "images": ["generated-1920x1080-png"], "context_kind": "active_task",
    }]
    raw, media = estimate_message_tokens(messages[0])
    assert media == 4096 and raw > 4096
    managed = manage_request(messages, context_window=8192, completion_reserve=1024)
    assert managed.report.estimated_prompt_tokens > 4096
    assert managed.report.estimated_prompt_tokens <= 7168


def test_openai_multimodal_string_tool_args_and_normalized_text(monkeypatch):
    calls = install_client(monkeypatch, [Response(200, {
        "choices": [{"message": {"content": "done", "tool_calls": [{
            "function": {"name": "inspect", "arguments": "{\"target\":\"screen\"}"},
        }]}}], "usage": {"prompt_tokens": 9, "completion_tokens": 3},
    })])
    messages = [{"role": "user", "content": "look", "images": ["YWJj"]}]
    original = copy.deepcopy(messages)
    result = asyncio.run(providers._local_openai_once(
        runtime(), messages, "fixture-model",
        [{"type": "function", "function": {"name": "inspect", "parameters": {"type": "object"}}}],
        0,
    ))
    assert result["content"] == "done"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"target":"screen"}'
    content = calls[0][1]["json"]["messages"][0]["content"]
    assert content[1]["image_url"]["url"] == "data:image/png;base64,YWJj"
    assert messages == original


def test_openai_fragmented_sse_is_preserved_for_tool_argument_assembly(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"read","arguments":"{\\"pa"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\\":\\"x\\"}"}}]}}]}',
        "data: [DONE]",
    ]

    @asynccontextmanager
    async def fake_client(_timeout):
        yield StreamingClient(lines)

    monkeypatch.setattr(local_runtime, "client", fake_client)

    async def collect():
        seen = []
        async for chunk in local_runtime.openai_chat_stream(runtime(), {"stream": True}):
            seen.append(chunk)
        return seen

    seen = asyncio.run(collect())
    args = "".join(
        part["function"].get("arguments", "")
        for chunk in seen for part in chunk["choices"][0]["delta"]["tool_calls"]
    )
    assert args == '{"path":"x"}'


def test_ollama_contract_normalizes_text_and_object_tool_arguments(monkeypatch):
    import model_settings

    config = local_runtime.LocalRuntimeConfig(
        kind="ollama", base_url="http://localhost:11434", chat_model="qwen3.5:9b",
        context_overrides={"qwen3.5:9b": 8192},
        capabilities=local_runtime.RuntimeCapabilities(tools="supported"),
    )
    monkeypatch.setattr(model_settings, "local_runtime_snapshot", lambda: config)
    calls = install_client(monkeypatch, [Response(200, {
        "message": {"content": "", "tool_calls": [{
            "function": {"name": "read", "arguments": {"path": "README.md"}},
        }]}, "prompt_eval_count": 10, "eval_count": 4,
    })])
    result = asyncio.run(providers._ollama_once(
        [{"role": "user", "content": "read"}], "qwen3.5:9b",
        [{"type": "function", "function": {"name": "read"}}], 0,
    ))
    assert result["tool_calls"][0]["function"] == {
        "name": "read", "arguments": {"path": "README.md"},
    }
    assert calls[0][0].endswith("/api/chat")


def test_live_runner_refuses_accidental_overwrite(monkeypatch, tmp_path):
    from tests.eval import compatibility_live

    output = tmp_path / "existing"
    output.with_suffix(".json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "compatibility_live", "--preset", "ollama", "--output", str(output),
    ])
    with pytest.raises(SystemExit) as caught:
        asyncio.run(compatibility_live.main())
    assert caught.value.code == 2


def test_source_digest_changes_with_git_identity_even_when_diff_is_identical():
    from tests.eval.compatibility_live import _digest_source_state

    files = [("backend/example.py", b"print('same')\n")]
    first = _digest_source_state(b"commit-a", b"same-diff", files)
    assert first == _digest_source_state(b"commit-a", b"same-diff", files)
    assert first != _digest_source_state(b"commit-b", b"same-diff", files)
    assert first != _digest_source_state(b"commit-a", b"changed-diff", files)
    assert first != _digest_source_state(
        b"commit-a", b"same-diff", [("backend/example.py", b"changed")],
    )


@pytest.mark.parametrize(("availability", "verdicts", "expected"), [
    ("unverified", [], "unverified"),
    ("available", ["supported", "supported"], "supported"),
    ("available", ["supported", "unverified"], "limited"),
    ("available", ["failed", "unverified"], "failed"),
    ("available", ["supported", "failed"], "failed"),
    ("available", ["unsupported", "unverified"], "unsupported"),
    ("available", ["unverified", "unverified"], "unverified"),
])
def test_profile_verdict_truth_table(availability, verdicts, expected):
    from tests.eval.compatibility_live import _profile_verdict

    assert _profile_verdict(
        [{"verdict": verdict} for verdict in verdicts], availability,
    ) == expected


def test_generic_live_profile_requires_exact_metadata_before_measurement(monkeypatch):
    from tests.eval import compatibility_live

    async def fake_metadata(_config, model):
        return {
            "id": model, "requested_id": model, "digest": None,
            "quantization": None, "declared_context": None,
        }

    async def fake_version(_config):
        return None

    runs = []

    async def fake_run(*_args):
        runs.append(True)
        return [{
            "id": "text", "verdict": "supported", "latency_ms": 1,
            "budget": None, "token_usage": {}, "detail": "ok",
        }]

    monkeypatch.setattr(compatibility_live, "_metadata", fake_metadata)
    monkeypatch.setattr(compatibility_live, "_runtime_version", fake_version)
    monkeypatch.setattr(compatibility_live, "_run_available", fake_run)

    missing = asyncio.run(compatibility_live._profile(
        "lm-studio", "model-a", "embed-a", 8192,
    ))
    assert missing["availability"] == "available"
    assert missing["metadata_status"] == "missing"
    assert missing["verdict"] == "unverified"
    assert not runs
    assert all(row["verdict"] == "unverified" for row in missing["scenarios"])

    supplied = asyncio.run(compatibility_live._profile(
        "lm-studio", "model-a", "embed-a", 8192, {
            "runtime_version": "1.2.3", "model_digest": "full-digest",
            "quantization": "Q4_K_M", "declared_context": 32768,
        },
    ))
    assert runs == [True]
    assert supplied["runtime"]["version"] == "1.2.3"
    assert supplied["model"] == {
        "id": "model-a", "requested_id": "model-a", "digest": "full-digest",
        "quantization": "Q4_K_M", "declared_context": 32768,
    }
    assert supplied["metadata_status"] == "complete"
    assert set(supplied["metadata_provenance"].values()) == {"user_supplied_cli"}
    assert supplied["verdict"] == "limited"


def test_live_error_details_centrally_redact_urls_secrets_and_user_paths():
    from tests.eval import compatibility_live

    sensitive = (
        "https://user:Bearer-secret@example.test/v1?api_key=hunter2 "
        "C:\\Users\\alice\\private.txt /home/alice/private"
    )
    request = httpx.Request("POST", "https://example.test/v1")
    response = httpx.Response(500, request=request, text=sensitive)
    errors = [
        local_runtime.LocalRuntimeError("provider_error", sensitive),
        httpx.HTTPStatusError(sensitive, request=request, response=response),
        RuntimeError(sensitive),
    ]
    details = [compatibility_live._safe_detail(error) for error in errors]
    joined = " ".join(details)
    for forbidden in (
        "https://", "Bearer", "api_key", "hunter2", "C:\\Users", "/home/", "alice",
    ):
        assert forbidden not in joined
    assert details == [
        "LocalRuntimeError:provider_error", "httpx:HTTPStatusError:status=500",
        "RuntimeError",
    ]

    async def explode():
        raise RuntimeError(sensitive)

    row = asyncio.run(compatibility_live._measure("text", explode, bool))
    assert row["detail"] == "RuntimeError"


def test_exactly_one_successful_overflow_retry_and_no_infinite_loop(monkeypatch):
    providers.reset_usage()
    overflow = Response(400, {"error": {"message": "maximum context length exceeded"}})
    success = Response(200, {
        "choices": [{"message": {"content": "recovered"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 2},
    })
    calls = install_client(monkeypatch, [overflow, success])
    messages = [
        {"role": "assistant", "content": "optional " * 700},
        {"role": "user", "content": "active", "context_kind": "active_task"},
    ]
    result = asyncio.run(providers._local_openai_once(runtime(), messages, "fixture-model", None, 0))
    assert result == {"content": "recovered"}
    assert len(calls) == 2
    reports = providers.get_context_reports()
    assert any(report.retry for report in reports)

    providers.reset_usage()
    calls = install_client(monkeypatch, [overflow, overflow])
    with pytest.raises(local_runtime.LocalRuntimeError) as caught:
        asyncio.run(providers._local_openai_once(runtime(), messages, "fixture-model", None, 0))
    assert caught.value.code == "context_overflow"
    assert len(calls) == 2


@pytest.mark.parametrize("capability", ["tools", "vision", "embeddings", "structured_output"])
@pytest.mark.parametrize("state", ["unknown", "unsupported"])
def test_unknown_and_unsupported_capabilities_fail_closed_without_io(monkeypatch, capability, state):
    caps = {name: "supported" for name in ("tools", "vision", "embeddings", "structured_output")}
    caps[capability] = state
    config = local_runtime.LocalRuntimeConfig(
        kind="openai_compatible", base_url="http://localhost:1234/v1",
        chat_model="m", embedding_model="e",
        capabilities=local_runtime.RuntimeCapabilities(**caps),
    )
    calls = install_client(monkeypatch, [])
    with pytest.raises(local_runtime.LocalRuntimeError) as caught:
        if capability == "embeddings":
            asyncio.run(local_runtime.embed(config, "e", ["x"]))
        else:
            kwargs = {
                "tools": [{"type": "function", "function": {"name": "x"}}] if capability == "tools" else None,
                "schema": {"type": "object"} if capability == "structured_output" else None,
            }
            messages = [{"role": "user", "content": "x", **({"images": ["a"]} if capability == "vision" else {})}]
            asyncio.run(providers._local_openai_once(config, messages, "m", kwargs["tools"], 0, schema=kwargs["schema"]))
    assert caught.value.code == "unsupported_capability"
    assert calls == []

import pytest

from agents.context_manager import (
    ContextBudgetError,
    IMAGE_TOKENS,
    SUMMARY_MARKER,
    is_context_overflow,
    manage_request,
    _pressure,
    estimate_message_tokens,
)


def test_reserves_completion_and_accounts_for_tools_and_media():
    request = manage_request(
        [
            {"role": "system", "content": "safety"},
            {"role": "user", "content": "inspect", "images": ["base64"]},
        ],
        context_window=8192,
        tools=[{"type": "function", "function": {"name": "inspect"}}],
    )
    assert request.report.completion_reserve == 2048
    assert request.report.media_tokens == IMAGE_TOKENS
    assert request.report.tool_schema_tokens > 0
    assert request.report.estimated_prompt_tokens <= request.report.prompt_budget


def test_progressive_compaction_preserves_contract_task_and_verified_evidence():
    messages = [
        {"role": "system", "content": "SAFETY CONTRACT"},
        {"role": "user", "content": "old question " * 600},
        {"role": "assistant", "content": "unverified claim " * 600},
        {"role": "tool", "content": "verbose output " * 800},
        {"role": "user", "content": "ACTIVE TASK", "context_kind": "active_task"},
        {"role": "user", "content": "VERIFIED SOURCE", "verified_evidence": True},
    ]
    request = manage_request(messages, context_window=4096)
    contents = "\n".join(str(m.get("content", "")) for m in request.messages)
    assert "SAFETY CONTRACT" in contents
    assert "ACTIVE TASK" in contents
    assert "VERIFIED SOURCE" in contents
    assert request.report.compacted
    assert request.report.estimated_prompt_tokens <= request.report.prompt_budget


def test_summaries_explicitly_do_not_upgrade_unverified_text():
    request = manage_request(
        [
            {"role": "system", "content": "contract"},
            {"role": "assistant", "content": "untrusted assertion " * 600},
            {"role": "user", "content": "current task"},
        ],
        context_window=1024,
    )
    assert any(SUMMARY_MARKER in str(m.get("content", "")) for m in request.messages)


def test_identical_input_produces_identical_request_and_report():
    messages = [
        {"role": "system", "content": "contract"},
        {"role": "tool", "content": "x" * 9000},
        {"role": "user", "content": "task"},
    ]
    first = manage_request(messages, context_window=2048)
    second = manage_request(messages, context_window=2048)
    assert first == second


def test_impossible_system_contract_fails_closed():
    with pytest.raises(ContextBudgetError):
        manage_request(
            [{"role": "system", "content": "s" * 10000}],
            context_window=512,
        )


@pytest.mark.parametrize("text", [
    "prompt exceeds context size",
    "maximum context length is 4096 tokens",
    "input is too many tokens",
])
def test_provider_overflow_normalization(text):
    assert is_context_overflow(RuntimeError(text))


def test_unrelated_provider_error_is_not_context_overflow():
    assert not is_context_overflow(RuntimeError("connection refused"))


@pytest.mark.parametrize(("ratio", "expected"), [
    (0.699, "normal"), (0.70, "trim_tools"), (0.849, "trim_tools"),
    (0.85, "summarize_history"), (0.949, "summarize_history"),
    (0.95, "essential_only"),
])
def test_pressure_boundaries_are_exact(ratio, expected):
    assert _pressure(ratio) == expected


def test_canonical_tool_schema_is_copied_and_never_mutated():
    tools = [{"type": "function", "function": {"name": "inspect", "parameters": {}}}]
    request = manage_request(
        [{"role": "system", "content": "contract"}, {"role": "user", "content": "task"}],
        context_window=1024,
        tools=tools,
    )
    assert request.tools == tools
    assert request.tools is not tools
    assert request.report.tool_schema_tokens > 0


def test_oversized_verified_evidence_fails_closed_without_extraction():
    evidence = {
        "role": "user", "content": "verified payload " * 1000,
        "verified_evidence": True, "provenance": {"source": "artifact-7"},
    }
    with pytest.raises(ContextBudgetError):
        manage_request(
            [{"role": "system", "content": "contract"}, evidence],
            context_window=1024,
        )


def test_ordinary_evidence_is_categorized_without_verified_preservation_contract():
    request = manage_request(
        [
            {"role": "system", "content": "contract"},
            {"role": "user", "content": "ORDINARY " * 2000, "context_kind": "evidence"},
            {
                "role": "user",
                "content": "VERIFIED SOURCE",
                "context_kind": "verified_evidence",
                "verified_evidence": True,
            },
            {"role": "user", "content": "active task"},
        ],
        context_window=1024,
        force_compact=True,
    )
    contents = "\n".join(str(message.get("content", "")) for message in request.messages)
    assert "ORDINARY " * 2000 not in contents
    assert request.report.summarized_messages > 0
    assert "VERIFIED SOURCE" in contents
    assert request.report.categories["evidence"] > 0


def test_stripped_policy_metadata_does_not_inflate_provider_visible_estimate():
    request = manage_request(
        [
            {"role": "system", "content": "contract"},
            {
                "role": "user",
                "content": "tiny proof",
                "context_kind": "verified_evidence",
                "verified_evidence": True,
                "provenance": {"raw": "not-provider-visible" * 20_000},
            },
            {"role": "user", "content": "active", "context_kind": "active_task"},
        ],
        context_window=512,
    )

    assert request.report.estimated_prompt_tokens < 128
    assert 0 < request.report.categories["evidence"] < 64
    assert all("provenance" not in message for message in request.messages)
    assert all("context_kind" not in message for message in request.messages)


def test_nested_tool_call_arguments_are_fully_charged_and_cannot_sneak_through():
    historical = {
        "role": "assistant",
        "content": "",
        "name": "planner",
        "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": "run", "arguments": "x" * 30_000},
        }],
    }
    tokens, _ = estimate_message_tokens(historical)
    assert tokens > 9_000
    request = manage_request(
        [historical, {"role": "user", "content": "active"}], context_window=1024
    )
    assert historical not in request.messages
    assert request.report.estimated_prompt_tokens <= request.report.prompt_budget


def test_utf8_estimate_is_conservative_for_unicode_and_emoji():
    ascii_tokens, _ = estimate_message_tokens({"role": "user", "content": "a" * 100})
    emoji_tokens, _ = estimate_message_tokens({"role": "user", "content": "😀" * 100})
    assert emoji_tokens > ascii_tokens


@pytest.mark.parametrize("kind, flag", [
    ("active_task", {}),
    ("verified_evidence", {"verified_evidence": True}),
])
def test_mandatory_middle_is_never_destructively_truncated(kind, flag):
    critical = "A" * 10_000 + "CRITICAL_MIDDLE_DO_NOT_DELETE" + "B" * 10_000
    message = {"role": "user", "content": critical, "context_kind": kind, **flag}
    with pytest.raises(ContextBudgetError):
        manage_request(
            [{"role": "system", "content": "contract"}, message],
            context_window=1024,
        )

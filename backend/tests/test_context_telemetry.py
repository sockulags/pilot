import asyncio
import json

from agents import providers
from agents.context_manager import ContextReport, manage_request
from agents.context_telemetry import build_context_telemetry


def _report(**overrides):
    values = {
        "context_window": 8192,
        "completion_reserve": 1024,
        "prompt_budget": 7168,
        "estimated_prompt_tokens": 3000,
        "pressure": "normal",
        "compacted": False,
        "model": "gemma4:12b",
        "context_role": "synthesis",
        "declared_context": 262144,
        "categories": {
            "system": 500,
            "tools": 200,
            "media": 0,
            "history": 2300,
            "memory": 0,
            "evidence": 0,
        },
    }
    values.update(overrides)
    return ContextReport(**values)


def test_multi_call_report_keeps_windows_separate_and_selects_final_call():
    report = build_context_telemetry([
        _report(context_window=4096, context_role="classifier"),
        _report(context_window=16384, context_role="synthesis", actual_prompt_tokens=4100),
    ])
    assert report is not None
    assert report["aggregation"] == "per_call_not_summed"
    assert report["primary_call"] == report["final_call"] == 1
    assert [call["effective_limit"] for call in report["calls"]] == [4096, 16384]
    assert report["calls"][1]["measurement"] == "exact"


def test_payload_is_allowlisted_and_contains_no_sensitive_material():
    payload = build_context_telemetry([_report(decisions=("removed secret prompt",))])
    encoded = json.dumps(payload)
    for forbidden in ("decisions", "messages", "raw_prompt", "image_base64", "args", "secret"):
        assert forbidden not in encoded.lower()


def test_retry_and_category_change_summary_are_exposed_without_removed_text():
    payload = build_context_telemetry([
        _report(
            retry=True,
            compacted=True,
            removed_messages=3,
            summarized_messages=2,
            removed_categories={"history": 3},
            summarized_categories={"history": 2},
            trimmed_tool_messages=1,
        )
    ])
    assert payload is not None
    call = payload["calls"][0]
    assert payload["overflow_retried"] is True
    assert call["changes"] == {
        "history": {"summarized": 2, "dropped": 3},
        "evidence": {"summarized": 0, "dropped": 0},
        "tools": {"trimmed": 1},
    }


def test_vision_and_semantic_categories_are_reported_as_counts_only():
    managed = manage_request(
        [
            {"role": "system", "content": "policy", "context_kind": "safety"},
            {"role": "user", "content": "remember", "context_kind": "memory"},
            {"role": "user", "content": "proof", "verified_evidence": True},
            {"role": "user", "content": "screen", "images": ["private-base64"]},
        ],
        context_window=16384,
    )
    payload = build_context_telemetry([managed.report])
    assert payload is not None
    categories = payload["calls"][0]["categories"]
    assert categories["system"] > 0
    assert categories["memory"] > 0
    assert categories["evidence"] > 0
    assert categories["media"] == 4096
    assert "private-base64" not in json.dumps(payload)


def test_contextvar_reports_are_isolated_between_concurrent_turns():
    async def collect(model: str):
        providers.reset_usage()
        await asyncio.sleep(0)
        providers._record_context_report(_report(model=model))
        await asyncio.sleep(0)
        return [item.model for item in providers.get_context_reports()]

    async def gather():
        return await asyncio.gather(collect("left"), collect("right"))

    left, right = asyncio.run(gather())
    assert left == ["left"]
    assert right == ["right"]


def test_failed_and_cancelled_turns_cannot_contaminate_following_turn():
    async def scenario():
        providers.reset_usage()
        providers._record_context_report(_report(model="failed"))
        try:
            raise RuntimeError("turn failed")
        except RuntimeError:
            pass

        # A new turn in the same task must explicitly start empty after failure.
        providers.reset_usage()
        assert providers.get_context_reports() == []

        ready = asyncio.Event()

        async def cancelled_turn():
            providers.reset_usage()
            providers._record_context_report(_report(model="cancelled"))
            ready.set()
            await asyncio.Future()

        task = asyncio.create_task(cancelled_turn())
        await ready.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # The cancelled child context never leaks, and the next turn resets its
        # own task-local collector before doing provider work.
        providers.reset_usage()
        return providers.get_context_reports()

    assert asyncio.run(scenario()) == []

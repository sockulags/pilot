"""Tests for bounding the compose grounding block (Ollama-free).

A large multi-file turn can gather 25k+ chars of evidence; a small answering
model then emits almost nothing. ``_bounded_structured`` (orchestrator) and
``to_prompt_dict(summary_chars=...)`` (RuntimeState) keep the grounding digestible
by shrinking only free-text summaries — never dropping sources / requirements /
files_read, and never corrupting the JSON. These pin that behaviour without a model.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents import orchestrator  # noqa: E402
from agents.runtime_state import RuntimeState  # noqa: E402


def _read_state(n_files: int, with_source: bool = False, content_chars: int = 4000) -> RuntimeState:
    rs = RuntimeState()
    for i in range(n_files):
        path = f"backend/pkg/module_{i}.py"
        rs.record_tool_result(
            "read_file", {"path": path},
            f"File: {path}\nContent:\n" + ("Z" * content_chars), ok=True,
        )
    if with_source:
        rs.record_tool_result(
            "web_research", {"query": "local llm 16gb"},
            "Research results for 'local llm 16gb':\nSources fetched: 2\n"
            "1. https://example.com/review\n2. https://example.com/bench",
            ok=True,
        )
    return rs


# --------------------------------------------------------------------------- #
# _bounded_structured
# --------------------------------------------------------------------------- #


def test_none_runtime_state_is_empty_object():
    assert orchestrator._bounded_structured(None, 9000) == "{}"


def test_small_evidence_keeps_full_summaries():
    rs = _read_state(1, content_chars=100)
    text = orchestrator._bounded_structured(rs, 9000)
    # Under budget: the full (record-time 1000-cap) summary is retained.
    obj = json.loads(text)
    assert obj["actions"][0]["summary"].startswith("File: backend/pkg/module_0.py")


def test_large_turn_stays_under_budget_and_valid_json():
    rs = _read_state(14, content_chars=4000)
    text = orchestrator._bounded_structured(rs, 9000)
    assert len(text) <= 9000
    json.loads(text)  # must remain valid JSON (regression: front-truncation broke this)


def test_bounding_preserves_sources_and_requirements_and_files():
    # The critical honesty property: a big research/analysis turn must NOT lose
    # its cited URLs or the contract/requirements when the grounding is shrunk.
    rs = _read_state(14, with_source=True, content_chars=4000)
    text = orchestrator._bounded_structured(rs, 9000)
    obj = json.loads(text)
    assert len(text) <= 9000
    # Sources (URLs) survive.
    assert obj["sources"], "web_research sources must be preserved"
    joined = json.dumps(obj, ensure_ascii=False)
    assert "https://example.com/review" in joined
    # Structural grounding survives.
    assert obj["files_read"], "files_read must be preserved"
    assert "actions" in obj and "requirements" in obj


def test_summaries_shrink_before_structure_is_touched():
    rs = _read_state(14, content_chars=4000)
    text = orchestrator._bounded_structured(rs, 9000)
    obj = json.loads(text)
    # Under pressure the per-action summaries are heavily trimmed (<=500)...
    assert all(len(a.get("summary", "")) <= 500 for a in obj["actions"])
    # ...but every action is still present (structure not dropped).
    assert len(obj["actions"]) == 14


def test_oversize_sources_kept_valid_even_if_over_budget():
    # Pathological: summary-less evidence still exceeds a tiny budget. We must
    # return VALID json with sources intact, never corrupt/truncated json.
    rs = _read_state(30, with_source=True, content_chars=6000)
    text = orchestrator._bounded_structured(rs, 500)  # impossibly small
    obj = json.loads(text)  # still valid
    assert obj["sources"], "sources preserved even when over budget"


# --------------------------------------------------------------------------- #
# to_prompt_dict(summary_chars=...)
# --------------------------------------------------------------------------- #


def test_summary_chars_shrinks_summaries_but_keeps_structured_fields():
    rs = RuntimeState()
    big = "File: backend/api/ws.py\nContent:\n" + ("A" * 5000)
    rs.record_tool_result("read_file", {"path": "backend/api/ws.py"}, big, ok=True)
    rs.record_tool_result("web_research", {"query": "q"},
                          "Research results for 'q':\nSources fetched: 2\nhttps://example.com/a", ok=True)

    full = rs.to_prompt_dict()
    trimmed = rs.to_prompt_dict(summary_chars=300)

    assert len(full["actions"][0]["summary"]) > len(trimmed["actions"][0]["summary"])
    assert len(trimmed["actions"][0]["summary"]) == 300
    assert trimmed["actions"][0]["summary"].startswith("File: backend/api/ws.py")
    assert trimmed["files_read"] == ["backend/api/ws.py"]
    assert trimmed["sources"] == full["sources"]
    assert trimmed["sources"], "web_research source must be recorded"


def test_summary_chars_zero_keeps_action_but_empties_summary():
    rs = RuntimeState()
    rs.record_tool_result("read_file", {"path": "backend/api/ws.py"},
                          "File: backend/api/ws.py\nContent:\n" + ("A" * 2000), ok=True)
    trimmed = rs.to_prompt_dict(summary_chars=0)
    assert trimmed["actions"][0]["summary"] == ""
    # The path still survives via args + files_read even with no summary.
    assert trimmed["actions"][0]["args"]["path"] == "backend/api/ws.py"
    assert trimmed["files_read"] == ["backend/api/ws.py"]


def test_default_to_prompt_dict_unchanged():
    rs = RuntimeState()
    rs.record_tool_result("read_file", {"path": "a.py"}, "File: a.py\nContent:\n" + "z" * 2000, ok=True)
    assert len(rs.to_prompt_dict()["actions"][0]["summary"]) == 1000


def test_summary_chars_does_not_mutate_recorded_state():
    rs = RuntimeState()
    rs.record_tool_result("read_file", {"path": "a.py"}, "File: a.py\nContent:\n" + "z" * 2000, ok=True)
    rs.to_prompt_dict(summary_chars=10)  # must not alter self.actions
    assert len(rs.actions[0]["summary"]) == 1000


# --------------------------------------------------------------------------- #
# integration: _build_reply_messages bounds a huge multi-file turn
# --------------------------------------------------------------------------- #


def test_build_reply_messages_bounds_large_grounding_but_keeps_paths_and_sources():
    from agents.loop import LoopOutcome

    rs = _read_state(6, with_source=True, content_chars=4000)
    outcome = LoopOutcome("max_steps", "\n".join(f"- read_file p{i} -> ..." for i in range(6)) * 20, "")
    outcome.runtime_state = rs

    messages = orchestrator._build_reply_messages(
        [{"role": "user", "content": "Förklara backendflödet"}], outcome
    )
    evidence_turn = messages[-1]["content"]

    # Bounded (budget + wrapper + synthesis instructions), not 26k.
    assert len(evidence_turn) < orchestrator.COMPOSE_EVIDENCE_BUDGET + 3000
    # ...yet source files AND cited URLs survive, so the answer can cite them.
    assert "module_0.py" in evidence_turn
    assert "https://example.com/review" in evidence_turn


def test_untrusted_wrapper_intact_after_bounding():
    from agents.loop import LoopOutcome
    from agents.untrusted import CLOSE_TAG

    rs = _read_state(14, with_source=True, content_chars=4000)
    outcome = LoopOutcome("max_steps", "x" * 40000, "")
    outcome.runtime_state = rs
    messages = orchestrator._build_reply_messages(
        [{"role": "user", "content": "hej"}], outcome
    )
    evidence_turn = messages[-1]["content"]
    # Exactly one untrusted-evidence wrapper survives the bounding (#37 defense).
    assert evidence_turn.count("<UNTRUSTED_EVIDENCE") == 1
    assert evidence_turn.count(CLOSE_TAG) == 1

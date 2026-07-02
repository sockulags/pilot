"""Tests for bounding the compose grounding block (Ollama-free).

A large multi-file turn can gather 25k+ chars of evidence; a small answering
model then emits almost nothing. ``_bound_evidence`` (orchestrator) and
``to_prompt_dict(summary_chars=...)`` (RuntimeState) keep the grounding digestible
while preserving every structured field. These pin that behaviour without a model.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents import orchestrator  # noqa: E402
from agents.runtime_state import RuntimeState  # noqa: E402


# --------------------------------------------------------------------------- #
# _bound_evidence
# --------------------------------------------------------------------------- #


def test_small_evidence_passes_through_unchanged():
    struct, log = orchestrator._bound_evidence("small structured", "short log", 9000)
    assert struct == "small structured"
    assert log == "short log"


def test_structured_over_budget_truncates_and_elides_log():
    struct_text = "x" * 12000
    struct, log = orchestrator._bound_evidence(struct_text, "y" * 5000, 9000)
    assert struct.startswith("x" * 9000)
    assert struct.endswith(orchestrator._TRUNC_MARK)
    assert log == orchestrator._LOG_ELIDED
    # structured stays near budget (plus the short marker), never the full 12k.
    assert len(struct) <= 9000 + len(orchestrator._TRUNC_MARK)


def test_log_trimmed_to_remaining_budget():
    struct_text = "s" * 6000
    log_text = "L" * 6000
    struct, log = orchestrator._bound_evidence(struct_text, log_text, 9000)
    assert struct == struct_text  # under budget, untouched
    # remaining = 3000; the log is trimmed to that plus the marker.
    assert log.startswith("L" * 3000)
    assert log.endswith(orchestrator._TRUNC_MARK)
    assert len(struct) + len(log) <= 9000 + len(orchestrator._TRUNC_MARK)


def test_log_fully_elided_when_no_room_left():
    struct_text = "s" * 8950  # remaining = 50 (<200 threshold)
    struct, log = orchestrator._bound_evidence(struct_text, "L" * 4000, 9000)
    assert log == orchestrator._LOG_ELIDED


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

    # The per-action summary shrinks...
    assert len(full["actions"][0]["summary"]) > len(trimmed["actions"][0]["summary"])
    assert len(trimmed["actions"][0]["summary"]) == 300
    # ...but the file path header (grounding) survives the trim.
    assert trimmed["actions"][0]["summary"].startswith("File: backend/api/ws.py")
    # Structured fields are preserved verbatim regardless of summary_chars.
    assert trimmed["files_read"] == ["backend/api/ws.py"]
    assert trimmed["sources"] == full["sources"]
    assert trimmed["sources"], "web_research source must be recorded"


def test_default_to_prompt_dict_unchanged():
    rs = RuntimeState()
    rs.record_tool_result("read_file", {"path": "a.py"}, "File: a.py\nContent:\n" + "z" * 2000, ok=True)
    # No summary_chars -> full behaviour (summary capped at record time to 1000).
    assert len(rs.to_prompt_dict()["actions"][0]["summary"]) == 1000


# --------------------------------------------------------------------------- #
# integration: _build_reply_messages bounds a huge multi-file turn
# --------------------------------------------------------------------------- #


def test_build_reply_messages_bounds_large_grounding_but_keeps_paths():
    from agents.loop import LoopOutcome

    rs = RuntimeState()
    files = [
        "backend/api/ws.py", "backend/agents/orchestrator.py",
        "backend/agents/coordinator.py", "backend/agents/loop.py",
        "backend/store.py", "backend/tools/registry.py",
    ]
    for path in files:
        rs.record_tool_result(
            "read_file", {"path": path},
            f"File: {path}\nContent:\n" + ("Z" * 4000), ok=True,
        )
    outcome = LoopOutcome("max_steps", "\n".join(f"- read_file {p} -> ..." for p in files) * 20, "")
    outcome.runtime_state = rs

    messages = orchestrator._build_reply_messages(
        [{"role": "user", "content": "Förklara backendflödet"}], outcome
    )
    evidence_turn = messages[-1]["content"]

    # The grounding is bounded (budget + wrapper + synthesis instructions), not 26k.
    assert len(evidence_turn) < orchestrator.COMPOSE_EVIDENCE_BUDGET + 3000
    # ...yet the real source files are still named, so the answer can cite them.
    assert "ws.py" in evidence_turn
    assert "coordinator.py" in evidence_turn

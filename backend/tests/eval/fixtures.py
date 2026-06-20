"""Diagnostic trace fixtures for the eval harness (issue #44, optional).

These small, hand-written snapshots make regressions legible: if the structured
runtime-state shape or the routing_decision event shape drifts, a scenario test
can diff against the documented baseline here instead of an opaque failure.

They are illustrative reference shapes (NOT a byte-for-byte golden of a live
run) — the tests assert on the load-bearing keys, not on volatile text.
"""

from __future__ import annotations


def sample_runtime_state_trace() -> dict:
    """A reference ``RuntimeState.to_prompt_dict()`` after a successful research turn.

    Mirrors the structure produced by the ``golden_web_research_with_sources``
    scenario: one web_research source recorded, the research contract satisfied,
    and the verified/gathering phase resolved to ``verified``.
    """
    return {
        "actions": [
            {
                "tool": "web_research",
                "args": {"query": "RTX 5060 Ti 16GB local LLM current", "min_sources": 3},
                "ok": True,
                "decision": "allowed",
            }
        ],
        "artifacts": [],
        "sources": [
            {
                "query": "RTX 5060 Ti 16GB local LLM current",
                "min_sources": 3,
                "sources_fetched": 3,
                "urls": [
                    "https://example.com/rtx-review",
                    "https://example.com/rtx-bench",
                    "https://example.com/llm-fit",
                ],
                "weak": False,
            }
        ],
        "files_read": [],
        "commands": [],
        "errors": [],
        "requirements": {
            "intent": "research",
            "satisfied": True,
            "missing": [],
        },
        "contract_intent": "research",
        "contract_satisfied": True,
        "phase": "verified",
    }


def sample_routing_decision_event() -> dict:
    """A reference ``routing_decision`` event for an offloaded code turn.

    Matches ``RoutingDecision.to_event()`` for a 'använd codex' offload so the
    eval suite documents what the UI receives before the turn acts.
    """
    return {
        "type": "routing_decision",
        "route": "code",
        "execution_engine": "codex",
        "cwd": "/repo",
        "reason": "classifier: code; offload via explicit request -> codex",
        "required_permissions": ["external_agent", "workspace_write"],
    }

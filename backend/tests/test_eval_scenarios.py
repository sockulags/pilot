"""Pytest entry point for the agent-flow + adversarial eval harness (issue #44).

Runs every scenario in ``tests/eval/scenarios.py`` through the deterministic
runner and asserts the declared expectations. The ENTIRE suite here runs under
plain ``pytest`` with NO Ollama and NO network — the runner stubs the classifier,
the coordinator decision stream, tool execution and the final compose step (the
same approach as ``tests/test_ws_scenarios.py`` / ``tests/test_coordinator.py``).

Run just this file::

    xvfb-run -a uv run --with pytest python -m pytest tests/test_eval_scenarios.py -q

Every scenario is also tagged ``@pytest.mark.eval`` so the deterministic suite
can be selected/deselected as a group (``-m eval`` / ``-m 'not eval'``). All
scenarios qualify as the Ollama-free deterministic subset; the marker exists only
to label the group, not to gate any scenario behind extras.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tests.eval.runner import Scenario, assert_scenario, run_scenario  # noqa: E402
from tests.eval.scenarios import (  # noqa: E402
    ADVERSARIAL_SCENARIOS,
    ALL_SCENARIOS,
    GOLDEN_SCENARIOS,
)


def _ids(scenarios):
    return [s.name for s in scenarios]


@pytest.mark.eval
@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_ids(ALL_SCENARIOS))
def test_eval_scenario(scenario: Scenario):
    """Each scenario runs deterministically and meets all its expectations."""
    result = run_scenario(scenario)
    failures = assert_scenario(scenario, result)
    assert not failures, "\n".join(failures)


@pytest.mark.eval
def test_scenario_count_meets_acceptance_floor():
    """Issue #44 requires at least 20 scenarios across golden + adversarial."""
    assert len(ALL_SCENARIOS) >= 20, len(ALL_SCENARIOS)
    assert len(GOLDEN_SCENARIOS) >= 8
    assert len(ADVERSARIAL_SCENARIOS) >= 5


@pytest.mark.eval
def test_scenario_names_are_unique():
    names = [s.name for s in ALL_SCENARIOS]
    assert len(names) == len(set(names)), "duplicate scenario names"


@pytest.mark.eval
def test_runtime_state_trace_fixture_is_legible():
    """A sample RuntimeState.to_prompt_dict() trace is stored for regressions.

    Exercising one golden scenario and snapshotting its structured runtime state
    keeps the evidence/contract shape legible if a regression changes it.
    """
    from tests.eval.fixtures import sample_runtime_state_trace

    golden = next(s for s in GOLDEN_SCENARIOS if s.name == "golden_web_research_with_sources")
    result = run_scenario(golden)
    trace = result.runtime_state.to_prompt_dict()

    expected = sample_runtime_state_trace()
    # The contract/evidence shape must match the stored fixture's structure.
    assert trace["contract_intent"] == expected["contract_intent"]
    assert trace["contract_satisfied"] == expected["contract_satisfied"]
    assert trace["requirements"]["intent"] == expected["requirements"]["intent"]
    assert trace["requirements"]["satisfied"] is True
    assert trace["sources"], "web research should record at least one source"
    assert trace["sources"][0]["sources_fetched"] == 3

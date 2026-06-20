"""Replayable agent-flow + adversarial eval harness (issue #44).

A deterministic scenario runner that feeds messages, STUBS every model/tool
response, and asserts route/tool/evidence/final-answer behavior. Everything runs
under plain ``pytest`` with NO Ollama and NO network — the stubbing approach is
the same one the existing scenario tests use (``tests/test_ws_scenarios.py`` and
``tests/test_coordinator.py``): the classifier, the coordinator decision stream,
tool execution, and the final compose step are all monkeypatched.

This package is purely additive: it does NOT change any production module's
behavior. See ``runner.py`` for the Scenario dataclass and the executor, and
``scenarios.py`` for the golden + adversarial scenario catalog.
"""

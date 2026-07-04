"""Explicit, explainable routing decisions.

Routing used to be spread across ``classify_turn``, ``route_project_bound_message``,
``should_offload_code``, the ``route_mode`` toggle, the selected project and a
handful of keyword checks in ``api/ws.py``. A message could be *classified* as
``code`` yet stay local unless the route was forced or the text mentioned
"Codex"/"Claude"; GitHub/repo terms got force-routed to ``computer`` when a
project was active. The net effect was hard to predict and impossible to surface.

This module consolidates that logic into a single, deterministic builder that
produces a :class:`RoutingDecision`: the user-visible execution engine, the
working directory in effect, a human-readable reason for the choice, and the
coarse capabilities the engine needs. It does NOT call the classifier — it takes
the already-classified route as input — so it is pure and unit-testable without a
network or model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.orchestrator import should_offload_code

# The user-visible execution engines. Each route maps to exactly one engine
# (the ``code`` route splits three ways depending on offload + chosen agent):
#   chat                          -> local_chat
#   computer                      -> local_tools
#   code, kept local              -> local_repo_agent
#   code, offloaded (agent=claude)-> claude_code   (external Claude Code product)
#   code, offloaded (agent=codex) -> codex
LOCAL_CHAT = "local_chat"
LOCAL_TOOLS = "local_tools"
LOCAL_REPO_AGENT = "local_repo_agent"
CLAUDE_CODE = "claude_code"
CODEX = "codex"

OFFLOAD_ENGINES = {CLAUDE_CODE, CODEX}

# Coarse capabilities each engine needs. Deliberately small and documented here;
# this mirrors the spirit of job_permissions.py's capability vocabulary
# ("read"/"shell"/"desktop") without coupling to its scheduled-job profiles,
# which are a different concern. The external agents additionally need an
# "external_agent" grant plus workspace write access.
# NOTE: local_chat carries the FULL tool capability set, not read-only. The chat
# route runs the same coordinator loop as computer (only the intent hint differs;
# the tool allowlist is identical), so a "chat" turn can still run a shell command
# or a desktop action if the model chooses to. Advertising read-only here was
# misleading (review 2026-07-04); until the coordinator actually enforces
# per-engine permissions, the honest advertisement is the real capability.
REQUIRED_PERMISSIONS: dict[str, list[str]] = {
    LOCAL_CHAT: ["read_files", "shell", "desktop"],
    LOCAL_TOOLS: ["read_files", "shell", "desktop"],
    LOCAL_REPO_AGENT: ["read_files", "shell"],
    CLAUDE_CODE: ["external_agent", "workspace_write"],
    CODEX: ["external_agent", "workspace_write"],
}


@dataclass
class RoutingDecision:
    """A single, explainable routing outcome for one user turn.

    ``route`` is the coarse classifier route ("chat" | "computer" | "code").
    ``execution_engine`` is the user-visible engine that will act this turn.
    ``cwd`` is the active project working directory (or None). ``reason`` is a
    human-readable explanation of why this engine was chosen. ``required_permissions``
    are the coarse capabilities the engine needs.
    """

    route: str
    execution_engine: str
    cwd: str | None
    reason: str
    required_permissions: list[str] = field(default_factory=list)

    def is_offload(self) -> bool:
        """Whether this turn delegates to an external coding agent."""
        return self.execution_engine in OFFLOAD_ENGINES

    def to_event(self) -> dict:
        """The ``routing_decision`` event surfaced to the UI before acting."""
        return {
            "type": "routing_decision",
            "route": self.route,
            "execution_engine": self.execution_engine,
            "cwd": self.cwd,
            "reason": self.reason,
            "required_permissions": list(self.required_permissions),
        }


def _offload_engine(agent: str | None) -> str:
    """Map the session's chosen agent to an offload engine."""
    return CODEX if (agent or "").lower() == "codex" else CLAUDE_CODE


def build_routing_decision(
    *,
    route_mode: str,
    classified_route: str,
    agent: str | None,
    text: str,
    project: str | None,
    cwd: str | None,
) -> RoutingDecision:
    """Consolidate the scattered routing logic into one deterministic decision.

    Pure and testable: it takes the already-classified route as input and never
    calls ``classify_turn`` itself. It does consult ``should_offload_code`` (also
    pure) to compute offload exactly as ``api/ws.py`` does today.

    - A non-auto ``route_mode`` is honored verbatim ("forced route_mode=...").
    - For auto, the ``classified_route`` is used. When the classifier emitted
      ``computer`` for an active project with GitHub/repo terms, that reasoning is
      recorded in ``reason`` (``classify_turn`` already applied the force-route via
      ``route_project_bound_message``; we explain it here).
    - Offload is ``route == "code" and should_offload_code(route_mode, text)``,
      identical to the current ws.py boolean; the engine becomes claude_code/codex
      per ``agent`` when offloading, else local_repo_agent.
    """
    forced = route_mode != "auto"
    route = route_mode if forced else classified_route

    offload = route == "code" and should_offload_code(route_mode, text)
    if route == "code":
        engine = _offload_engine(agent) if offload else LOCAL_REPO_AGENT
    elif route == "computer":
        engine = LOCAL_TOOLS
    else:
        engine = LOCAL_CHAT

    reason = _explain(
        forced=forced,
        route_mode=route_mode,
        route=route,
        offload=offload,
        engine=engine,
        text=text,
        project=project,
    )

    return RoutingDecision(
        route=route,
        execution_engine=engine,
        cwd=cwd,
        reason=reason,
        required_permissions=list(REQUIRED_PERMISSIONS.get(engine, [])),
    )


def _explain(
    *,
    forced: bool,
    route_mode: str,
    route: str,
    offload: bool,
    engine: str,
    text: str,
    project: str | None,
) -> str:
    if forced:
        base = f"forced route_mode={route_mode}"
        if route == "code":
            return (
                f"{base}; offload via {engine}"
                if offload
                else f"{base}; kept local in repo agent"
            )
        return base

    if route == "code":
        if offload:
            return f"classifier: code; offload via explicit request -> {engine}"
        return "classifier: code; no offload signal -> kept local in repo agent"

    if route == "computer":
        if project and _mentions_github_terms(text):
            return "project active + GitHub/repo terms -> computer (local gh tools)"
        return "auto-classified computer"

    return "auto-classified chat"


def _mentions_github_terms(text: str) -> bool:
    # Imported lazily to keep this module's import surface small and avoid any
    # circularity surprises; PROJECT_GITHUB_TERMS lives with the classifier.
    import re

    from agents.orchestrator import PROJECT_GITHUB_TERMS

    lowered = text.lower()
    return any(
        re.search(rf"\b{re.escape(term.strip())}\b", lowered) for term in PROJECT_GITHUB_TERMS
    )

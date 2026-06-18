"""Deterministic turn policy helpers for routing, task context and final guards."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from config import OLLAMA_MODEL, resolve_answer_model, tools_capable_model


@dataclass(frozen=True)
class TaskContext:
    intent: str = "chat"
    needs_tools: bool = False
    requires_current_sources: bool = False
    creates_file: bool = False
    standalone_task: str = ""
    search_query: str = ""
    preferred_model: str | None = None
    entities: dict[str, str] = field(default_factory=dict)


_FOLLOWUP_TERMS = (
    "sätt igång",
    "satt igang",
    "gör det",
    "gor det",
    "kör",
    "kor",
    "fortsätt",
    "fortsatt",
    "jag kan vänta",
    "jag kan vanta",
    "do it",
    "go ahead",
)

_CURRENT_TERMS = (
    "aktuella",
    "aktuell",
    "senaste",
    "idag",
    "nu ",
    "juni 2026",
    "2026",
    "current",
    "latest",
    "today",
)

_RESEARCH_TERMS = (
    "sök",
    "sok",
    "hämta",
    "hamta",
    "leta",
    "research",
    "look up",
    "fetch",
    "ta aktuella",
)

_FILE_TERMS = (
    "html-fil",
    "html file",
    "fil",
    "file",
    "graf",
    "graph",
    "csv",
    "spara",
    "skapa",
    "visa det i",
)

_ACTION_TERMS = (
    "kör ",
    "kor ",
    "run ",
    "öppna",
    "oppna",
    "open ",
    "installera",
    "install ",
)


def build_task_context(conversation: list[dict] | None, user_message: str) -> TaskContext:
    """Return a compact, standalone description of what this turn is really asking."""
    conversation = conversation or []
    latest = _clean(user_message)
    resolved = _resolve_followup(conversation, latest)
    text_lc = f" {resolved.lower()} "
    entities = _extract_entities(resolved)

    creates_file = _looks_like_file_output(resolved)
    requires_current = _contains_any(text_lc, _CURRENT_TERMS)
    research = _looks_like_research_request(resolved) or requires_current
    action = _contains_any(text_lc, _ACTION_TERMS)
    project_analysis = _looks_like_project_analysis(resolved)
    needs_tools = research or creates_file or action or project_analysis

    intent = "chat"
    if project_analysis:
        intent = "project_analysis"
    elif research and creates_file:
        intent = "research_and_create_file"
    elif creates_file:
        intent = "create_file"
    elif research:
        intent = "research"
    elif action:
        intent = "computer_action"

    preferred_model = "gpt-oss:20b" if requires_current or research else None
    standalone = _make_standalone_task(resolved, latest)
    query = _make_search_query(standalone, entities, requires_current)

    return TaskContext(
        intent=intent,
        needs_tools=needs_tools,
        requires_current_sources=requires_current or research,
        creates_file=creates_file,
        standalone_task=standalone,
        search_query=query,
        preferred_model=preferred_model,
        entities=entities,
    )


def deterministic_route(
    conversation: list[dict] | None,
    user_message: str,
    project: str | None = None,
    model_mode: str = "auto",
) -> dict | None:
    ctx = build_task_context(conversation, user_message)
    text = f" {ctx.standalone_task.lower()} "

    if _looks_like_git_status(text):
        thinking = "git/status request; using computer route"
        if not project:
            thinking += " (No project folder selected; command cwd must be explicit in output)"
        return {
            "route": "computer",
            "task": ctx.standalone_task,
            "thinking": thinking,
            "model": resolve_answer_model(model_mode, ctx.preferred_model),
        }

    if ctx.creates_file or ctx.intent == "research_and_create_file":
        return {
            "route": "computer",
            "task": ctx.standalone_task,
            "thinking": "task needs local action/research and file output; using computer route",
            "model": resolve_answer_model(model_mode, ctx.preferred_model),
        }

    if ctx.intent == "project_analysis":
        return {
            "route": "computer",
            "task": ctx.standalone_task,
            "thinking": "project/backend flow analysis needs local file inspection; using computer route",
            "model": resolve_answer_model(model_mode, ctx.preferred_model),
        }

    if ctx.requires_current_sources:
        return {
            "route": "computer",
            "task": ctx.standalone_task,
            "thinking": "fresh information needs sourced research; using computer route",
            "model": resolve_answer_model(model_mode, ctx.preferred_model),
        }

    return None


def choose_coordinator_model(model_mode: str, ctx: TaskContext) -> str:
    if model_mode and model_mode != "auto":
        return tools_capable_model(model_mode)
    if ctx.preferred_model:
        return resolve_answer_model("auto", ctx.preferred_model)
    return OLLAMA_MODEL


def tool_task(task: str, ctx: TaskContext | None = None) -> str:
    if ctx and ctx.standalone_task:
        return ctx.standalone_task
    return task


def web_query(task: str, ctx: TaskContext | None = None) -> str:
    if ctx and ctx.search_query:
        return ctx.search_query
    return _make_search_query(task, _extract_entities(task), _contains_any(task.lower(), _CURRENT_TERMS))


def sanitize_final_reply(text: str, had_actions: bool, needs_tools: bool = False) -> str:
    """Block pseudo tool syntax and false action claims from user-visible replies."""
    raw = text or ""
    has_pseudo_tool = bool(
        re.search(r"<\s*/?\s*tool_code\s*>", raw, re.IGNORECASE)
        or re.search(r"\b(?:web_search|web_research|fetch_url|run_command)\s*\(", raw)
    )
    if has_pseudo_tool:
        if needs_tools and not had_actions:
            return (
                "Jag kunde inte utföra verktygssteget i den här turen. "
                "Försök igen, eller välj ett läge som tillåter dator-/webbåtgärder."
            )
        return re.sub(r"(?is)<\s*tool_code\s*>.*?<\s*/\s*tool_code\s*>", "", raw).strip()
    if needs_tools and not had_actions and _claims_action(raw):
        return (
            "Jag kunde inte verifiera att någon åtgärd faktiskt kördes i den här turen. "
            "Jag behöver köra verktyget först innan jag kan ge ett resultat."
        )
    return raw


def _resolve_followup(conversation: list[dict], latest: str) -> str:
    latest_lc = latest.lower()
    if not _contains_any(latest_lc, _FOLLOWUP_TERMS):
        return latest

    user_messages = [
        _clean(m.get("content", ""))
        for m in conversation
        if m.get("role") == "user" and _clean(m.get("content", ""))
    ]
    if not user_messages:
        return latest
    base = user_messages[0]
    refinements = [m for m in user_messages[1:] if _is_refinement(m)]
    suffix = " ".join(refinements)
    return f"{base} {suffix}".strip()


def _is_refinement(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("förtydliga", "fortydliga", "dag för dag", "day by day", "istället", "instead"))


def _make_standalone_task(resolved: str, latest: str) -> str:
    if resolved != latest:
        return resolved
    return resolved.strip()


def _make_search_query(task: str, entities: dict[str, str], requires_current: bool) -> str:
    text = task
    # Convert command wording into a source-oriented query while preserving entities.
    text = re.sub(r"(?i)\b(sök|sok|search|look up|hämta|hamta|kan du|please)\b", " ", text)
    text = re.sub(r"(?i)\bpå webben efter\b", " ", text)
    text = re.split(
        r"(?i)\s+(?:och\s+)?(?:sammanfatta|summera|ge mig|with links|med länkar|med lankar)\b",
        text,
        maxsplit=1,
    )[0]
    text = re.sub(r"(?i)\b(sätt ihop|visa det i|skapa|snygg|visuell|graf|html-fil|html file)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if entities.get("place") and entities["place"].lower() not in text.lower():
        text = f"{text} {entities['place']}"
    if "dag för dag" in task.lower() or "day by day" in task.lower():
        text = f"{text} daily"
    if entities.get("place") and "temperatur" in task.lower():
        text = f"SMHI historical daily temperature {entities['place']} May 2026"
    return text.strip() or task.strip()


def _extract_entities(text: str) -> dict[str, str]:
    entities: dict[str, str] = {}
    if re.search(r"\börebro\b", text, re.IGNORECASE):
        entities["place"] = "Örebro"
    month = re.search(
        r"\b(januari|februari|mars|april|maj|juni|juli|augusti|september|oktober|november|december|may|june)\b",
        text,
        re.IGNORECASE,
    )
    if month:
        entities["period"] = month.group(1)
    return entities


def _looks_like_git_status(text: str) -> bool:
    return " git status " in text or " git-status " in text


def _looks_like_file_output(text: str) -> bool:
    lowered = text.lower()
    if any(token in lowered for token in ("html-fil", "html file", "csv", "graf")):
        return True
    if re.search(r"\b(skapa|skriv|spara|write|create|save)\b", lowered) and re.search(
        r"\b(fil|file|rapport|report|markdown|md)\b", lowered
    ):
        return True
    if re.search(r"\bvisa det i\b", lowered) and re.search(r"\b(fil|html|graf)\b", lowered):
        return True
    return False


def _looks_like_research_request(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\b(sök|sok|research|look up|fetch|hämta|hamta|leta)\b", lowered):
        return True
    if "ta aktuella" in lowered:
        return True
    return False


def _looks_like_project_analysis(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"\b(det här|detta|this)\s+projekt", lowered)
        or "projektets backend" in lowered
        or "backendflöde" in lowered
        or "websocket" in lowered and "tool-call" in lowered
    )


def _claims_action(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "jag har sökt",
            "jag har sokt",
            "jag sökte",
            "jag sokte",
            "jag körde",
            "jag korde",
            "i searched",
            "i ran",
        )
    )


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

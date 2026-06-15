"""Top-level turn orchestrator — the conversational "front brain".

Sits above the tool router (agents/router.py). For each user turn it picks one
of three routes:

- ``chat``     — answer conversationally (streamed straight from Ollama).
- ``computer`` — do something on this machine; runs the existing agent loop
                 (agents/loop.py :: run_agent_loop).
- ``code``     — delegate to the Claude Code / Codex CLI (tools/codex.py).

The brain stays local (Ollama). Routing is one low-temperature classification
call; the chat reply is a separate streamed call so each prompt stays focused,
which small local models handle far more reliably.
"""

import logging
from typing import AsyncGenerator

import httpx

from agents.json_utils import extract_json_object
from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_MODELS,
    OLLAMA_ROUTER_MODEL,
    resolve_answer_model,
)
from tools import registry

logger = logging.getLogger(__name__)

VALID_ROUTES = {"chat", "computer", "code"}
# Safe default: never act on the computer on a parse failure.
ROUTE_DEFAULT = {"route": "chat", "thinking": "parse error — defaulting to chat"}
PROJECT_GITHUB_TERMS = (
    "gh ",
    "github",
    "issue",
    "issues",
    "pull request",
    "pull requests",
    "pr ",
    "repo",
    "repository",
)

CLASSIFY_SYSTEM = """You are the orchestrator for a local AI assistant. The assistant can hold a normal conversation, control THIS computer (open apps, click, type, screenshots, run shell commands, read files/folders), or delegate to a coding agent (Claude Code) to work on software projects.

Given the conversation so far and the latest user message, decide how to handle the latest message. Choose exactly one route:

- "chat": Answer conversationally yourself. Use for questions, greetings, explanations, brainstorming — anything that does NOT require acting on the computer or editing/running code in a project.
- "computer": The user wants to control the computer's GUI or run a quick one-off command — open/focus an app, click a button, type into a window, take a screenshot, run a single shell command, or inspect a file/folder ad hoc. NOT for working on a software project.
- "code": The user wants to work on a software project — create, edit, explain, refactor, run, fix, or continue code or files (including docs like README) in a project. Provide a "prompt" field: the instruction to pass to the coding agent.

Prefer "chat" when unsure. Only choose "computer" or "code" when the user clearly wants an action taken. When a project folder is active, lean toward "code" for anything about that project's files or code.

When a model menu is provided in the context, also pick the local model best suited to ANSWER this message and put its exact id in a "model" field. Match the message to the model's described strength; if unsure pick the first one.

Respond ONLY with valid JSON, no prose:
{"route": "chat" | "computer" | "code", "task": "<only for computer>", "prompt": "<only for code>", "model": "<model id from the menu, if one was given>", "thinking": "<short reason>"}"""

CHAT_SYSTEM = (
    "You are Pilot, a helpful local assistant running on the user's computer. "
    "You can hold a conversation, control the computer (open apps, click, type, "
    "screenshots, run commands, inspect and search files/folders), and delegate "
    "coding tasks. For this reply, answer conversationally and concisely. Nothing "
    "was gathered this turn — no tool has run yet. If the user asks what you can "
    "do or to list your tools, describe your capabilities from the list below — "
    "never say you have no tools. Never claim you ran a tool, searched, or "
    "navigated this turn, and never invent a 'technical error' to excuse not "
    "acting; if you haven't run something, say so plainly or just answer. "
    "Match the user's language (they often write Swedish)."
)

REPLY_SYSTEM = (
    "You are Pilot, a helpful local assistant on the user's computer. To answer "
    "this turn you gathered help — consulting specialist models, looking at the "
    "screen, and/or running tools. Reply to the user in THEIR language (they "
    "often write Swedish), conversationally and concisely — as a real answer, not "
    "a status report. Ground every claim in the activity log below (including any "
    "expert answers); never invent results that aren't shown there. If something "
    "failed or looks wrong, say so honestly and suggest the fix."
)


def _recent(conversation: list[dict], limit: int = 10) -> list[dict]:
    return conversation[-limit:] if conversation else []


def _model_menu() -> str:
    """Render the selectable models as a labelled menu for the classifier."""
    return "\n".join(
        f'- "{mid}": {meta["hint"]}' for mid, meta in OLLAMA_MODELS.items()
    )


def _conversation_text(conversation: list[dict]) -> str:
    if not conversation:
        return "(no prior messages)"
    lines = []
    for msg in _recent(conversation):
        role = msg.get("role", "user")
        content = str(msg.get("content", ""))[:1200]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def classify_turn(
    conversation: list[dict],
    user_message: str,
    project: str | None = None,
    model_mode: str = "auto",
) -> dict:
    """Decide how to handle the latest user turn. Returns a route dict.

    ``conversation`` is the history BEFORE the latest user message (list of
    {"role", "content"}). ``user_message`` is the latest user text. ``project``
    is the name of the active project folder, if one is selected — it biases
    code-related requests toward the "code" route. ``model_mode`` is "auto"
    (the classifier picks the answering model) or a pinned model id; the result
    always carries a resolved ``model`` for the answering step.

    Classification itself always runs on OLLAMA_ROUTER_MODEL (fast, tools-capable)
    regardless of which model ends up answering.
    """
    auto = model_mode == "auto"

    forced = route_project_bound_message(user_message, project)
    if forced:
        forced["model"] = resolve_answer_model(model_mode, None)
        return forced

    project_line = (
        f"\n\nActive project folder: {project!r}. If the latest message is about working on "
        "this project's code or files, choose \"code\"."
        if project
        else "\n\nNo project folder is selected."
    )
    model_line = (
        f"\n\nModel menu — pick the best \"model\" id to answer this message:\n{_model_menu()}"
        if auto
        else ""
    )
    context = (
        f"Conversation so far:\n{_conversation_text(conversation)}{project_line}{model_line}\n\n"
        f"Latest user message:\n{user_message}"
    )
    messages = [
        {"role": "system", "content": CLASSIFY_SYSTEM},
        {"role": "user", "content": context},
    ]

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": OLLAMA_ROUTER_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
            )
            resp.raise_for_status()
            content = resp.json()["message"]["content"].strip()
    except Exception as exc:
        logger.warning("classify_turn request failed: %s", exc)
        decision = dict(ROUTE_DEFAULT)
        decision["model"] = resolve_answer_model(model_mode, None)
        return decision

    decision = extract_json_object(content, ROUTE_DEFAULT)
    normalized = _normalize_decision(decision, user_message, model_mode)

    # The code route delegates to an external CLI in a project folder. Without a
    # project selected it dead-ends, so a coding *question* with no project is
    # answered locally instead — the coordinator can consult the coder model.
    if normalized["route"] == "code" and not project:
        normalized["route"] = "chat"
        normalized.pop("prompt", None)
        normalized["thinking"] = "coding question with no active project — answering locally"
    return normalized


def route_project_bound_message(user_message: str, project: str | None) -> dict | None:
    if not project:
        return None

    text = f" {user_message.lower()} "
    if not any(term in text for term in PROJECT_GITHUB_TERMS):
        return None

    return {
        "route": "code",
        "prompt": user_message.strip(),
        "thinking": "project GitHub/repository request with an active project; routing to code",
    }


def _normalize_decision(decision: dict, user_message: str, model_mode: str = "auto") -> dict:
    route = str(decision.get("route", "chat")).strip().lower()
    if route not in VALID_ROUTES:
        route = "chat"

    normalized: dict = {
        "route": route,
        "thinking": decision.get("thinking", ""),
        # Pin wins when set; otherwise honour the classifier's suggestion.
        "model": resolve_answer_model(model_mode, decision.get("model")),
    }
    if route == "computer":
        # Fall back to the raw user message if the model didn't extract a task.
        normalized["task"] = str(decision.get("task") or user_message).strip()
    elif route == "code":
        normalized["prompt"] = str(decision.get("prompt") or user_message).strip()
    return normalized


async def _stream_ollama_chat(
    messages: list[dict], model: str | None = None
) -> AsyncGenerator[str, None]:
    """Stream content chunks from Ollama's /api/chat for the given messages."""
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": model or OLLAMA_MODEL, "messages": messages, "stream": True},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                chunk = extract_json_object(line, {})
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece


def _build_reply_messages(conversation: list[dict], outcome=None, memories: str = "") -> list[dict]:
    """Messages for the shared conversational layer.

    With ``outcome=None`` this is a plain chat reply. With an ``outcome`` (a
    LoopOutcome from the coordinator) the activity log is appended as a final
    user turn so the model answers grounded in what was actually done.
    ``memories`` are recalled long-term facts injected as system context.
    """
    system = CHAT_SYSTEM if outcome is None else REPLY_SYSTEM
    system += (
        "\n\nYour capabilities (tools you can use on this computer):\n"
        f"{registry.capability_manifest()}"
    )
    if memories:
        system += (
            "\n\nLong-term memory about the user (use when relevant; do not contradict "
            f"or repeat verbatim unless asked):\n{memories}"
        )
    messages = [{"role": "system", "content": system}]
    messages.extend(
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in _recent(conversation, limit=20)
    )
    if outcome is not None:
        messages.append({
            "role": "user",
            "content": (
                "Underlag jag (assistenten) samlade denna tur (expertsvar, "
                "skärmobservationer, verktygsresultat):\n"
                f"{outcome.action_log or '(inget registrerades)'}\n\n"
                f"Status: {outcome.status}\n\n"
                "Väv ihop detta till ett svar på användarens språk. Hitta inte på "
                "resultat som inte syns ovan."
            ),
        })
    return messages


def _fallback_reply(outcome, exc: Exception) -> str:
    if outcome is None:
        return f"[chat error: {exc}]"
    return outcome.detail or outcome.action_log or "Klart."


async def compose_reply(
    conversation: list[dict], outcome=None, model: str | None = None, memories: str = ""
) -> AsyncGenerator[str, None]:
    """Stream the user-facing assistant reply — the single output layer.

    ``conversation`` should already include the latest user message as the last
    entry. ``outcome`` is None for the chat route, or a LoopOutcome for the
    computer route (its activity log grounds the reply). ``model`` is the local
    model chosen for this turn (auto-picked or pinned); falls back to
    OLLAMA_MODEL. On model failure a fallback built from the outcome is yielded
    so the message is never lost.
    """
    messages = _build_reply_messages(conversation, outcome, memories)
    yielded = False
    try:
        async for piece in _stream_ollama_chat(messages, model):
            yielded = True
            yield piece
    except Exception as exc:
        logger.warning("compose_reply request failed: %s", exc)
        if not yielded:
            yield _fallback_reply(outcome, exc)

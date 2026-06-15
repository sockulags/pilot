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
from config import OLLAMA_BASE_URL, OLLAMA_MODEL

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

Respond ONLY with valid JSON, no prose:
{"route": "chat" | "computer" | "code", "task": "<only for computer>", "prompt": "<only for code>", "thinking": "<short reason>"}"""

CHAT_SYSTEM = (
    "You are Pilot, a helpful local assistant running on the user's computer. "
    "You can also control the computer and delegate coding tasks, but for this "
    "reply just answer conversationally and concisely. No tool, Codex, gh, or "
    "computer action has run in this chat route. If the user asks whether a tool "
    "was run and there is no activity log for this turn, say exactly: "
    "'Jag har inte kört något verktyg än.' Match the user's language "
    "(they often write Swedish)."
)

REPLY_SYSTEM = (
    "You are Pilot, a helpful local assistant on the user's computer. You just "
    "acted on the user's machine on their behalf. Reply to the user in THEIR "
    "language (they often write Swedish), conversationally and concisely — as a "
    "real answer, not a status report. Ground every claim in the activity log "
    "below; never invent results that aren't shown there. If something failed or "
    "looks wrong, say so honestly and suggest the fix."
)


def _recent(conversation: list[dict], limit: int = 10) -> list[dict]:
    return conversation[-limit:] if conversation else []


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
    conversation: list[dict], user_message: str, project: str | None = None
) -> dict:
    """Decide how to handle the latest user turn. Returns a route dict.

    ``conversation`` is the history BEFORE the latest user message (list of
    {"role", "content"}). ``user_message`` is the latest user text. ``project``
    is the name of the active project folder, if one is selected — it biases
    code-related requests toward the "code" route.
    """
    forced = route_project_bound_message(user_message, project)
    if forced:
        return forced

    project_line = (
        f"\n\nActive project folder: {project!r}. If the latest message is about working on "
        "this project's code or files, choose \"code\"."
        if project
        else "\n\nNo project folder is selected."
    )
    context = (
        f"Conversation so far:\n{_conversation_text(conversation)}{project_line}\n\n"
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
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
            )
            resp.raise_for_status()
            content = resp.json()["message"]["content"].strip()
    except Exception as exc:
        logger.warning("classify_turn request failed: %s", exc)
        return dict(ROUTE_DEFAULT)

    decision = extract_json_object(content, ROUTE_DEFAULT)
    return _normalize_decision(decision, user_message)


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


def _normalize_decision(decision: dict, user_message: str) -> dict:
    route = str(decision.get("route", "chat")).strip().lower()
    if route not in VALID_ROUTES:
        route = "chat"

    normalized: dict = {"route": route, "thinking": decision.get("thinking", "")}
    if route == "computer":
        # Fall back to the raw user message if the model didn't extract a task.
        normalized["task"] = str(decision.get("task") or user_message).strip()
    elif route == "code":
        normalized["prompt"] = str(decision.get("prompt") or user_message).strip()
    return normalized


async def _stream_ollama_chat(messages: list[dict]) -> AsyncGenerator[str, None]:
    """Stream content chunks from Ollama's /api/chat for the given messages."""
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": True},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                chunk = extract_json_object(line, {})
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece


def _build_reply_messages(conversation: list[dict], outcome=None) -> list[dict]:
    """Messages for the shared conversational layer.

    With ``outcome=None`` this is a plain chat reply. With an ``outcome`` (a
    LoopOutcome from the computer route) the activity log is appended as a final
    user turn so the model answers grounded in what was actually done.
    """
    system = CHAT_SYSTEM if outcome is None else REPLY_SYSTEM
    messages = [{"role": "system", "content": system}]
    messages.extend(
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in _recent(conversation, limit=20)
    )
    if outcome is not None:
        messages.append({
            "role": "user",
            "content": (
                "Aktivitetslogg för det jag (assistenten) just gjorde på datorn "
                f"denna tur:\n{outcome.action_log or '(inga åtgärder registrerades)'}\n\n"
                f"Status: {outcome.status}\n\n"
                "Svara nu användaren på deras språk utifrån detta. Hitta inte på "
                "resultat som inte syns i loggen."
            ),
        })
    return messages


def _fallback_reply(outcome, exc: Exception) -> str:
    if outcome is None:
        return f"[chat error: {exc}]"
    return outcome.detail or outcome.action_log or "Klart."


async def compose_reply(
    conversation: list[dict], outcome=None
) -> AsyncGenerator[str, None]:
    """Stream the user-facing assistant reply — the single output layer.

    ``conversation`` should already include the latest user message as the last
    entry. ``outcome`` is None for the chat route, or a LoopOutcome for the
    computer route (its activity log grounds the reply). On model failure a
    fallback built from the outcome is yielded so the message is never lost.
    """
    messages = _build_reply_messages(conversation, outcome)
    yielded = False
    try:
        async for piece in _stream_ollama_chat(messages):
            yielded = True
            yield piece
    except Exception as exc:
        logger.warning("compose_reply request failed: %s", exc)
        if not yielded:
            yield _fallback_reply(outcome, exc)

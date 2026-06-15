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
    "reply just answer conversationally and concisely. Match the user's language "
    "(they often write Swedish)."
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


async def stream_chat(
    conversation: list[dict], user_message: str
) -> AsyncGenerator[str, None]:
    """Stream a conversational reply from Ollama, yielding text chunks.

    ``conversation`` should already include the latest user message as the last
    entry (role "user").
    """
    messages = [{"role": "system", "content": CHAT_SYSTEM}]
    messages.extend(
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in _recent(conversation, limit=20)
    )

    try:
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
    except Exception as exc:
        logger.warning("stream_chat request failed: %s", exc)
        yield f"[chat error: {exc}]"

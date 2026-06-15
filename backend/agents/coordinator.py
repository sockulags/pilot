"""Single-turn multi-model coordinator — the front brain.

The coordinator model (gemma4 by default, or the pinned model) receives the turn
and runs a short agentic loop *within that one turn*. At each step it may:

- ``consult`` a specialist local model (e.g. qwen2.5-coder for code, deepseek-r1
  for hard reasoning) with a focused sub-question, and weave the answer in;
- ``perceive`` the screen (screenshot + Set-of-Marks element list) — the
  text-based "vision" path, no multimodal model needed;
- ``tool`` — run an OS/desktop tool (run_command, read_file, click_element, …);
- ``answer`` — stop gathering; the final reply is then synthesised by
  orchestrator.compose_reply grounded in everything gathered.

Only the models actually installed in Ollama are offered as experts, so the
auto-orchestration adapts to what's available. Every step's reasoning and each
expert hand-off is emitted so the user sees the work live.

Returns a LoopOutcome (same contract as agents.loop.run_agent_loop) so the
WebSocket layer reuses the existing ``outcome -> compose_reply`` flow.
"""

import asyncio
import logging
from typing import Callable

import httpx

from agents import loop as agent_loop
from agents.json_utils import extract_json_object
from agents.loop import LoopOutcome, make_event
from agents.safety import unsafe_tool_block_reason
from config import (
    COORDINATOR_MAX_STEPS,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_MODELS,
)
from memory import save_memory

logger = logging.getLogger(__name__)

# OS/desktop tools the coordinator may drive (delegated to loop.execute_tool).
COORDINATOR_TOOLS = {
    "run_command",
    "read_file",
    "list_dir",
    "find_file",
    "list_windows",
    "focus_window",
    "screenshot",
    "get_screen_size",
    "open_app",
    "click_element",
    "click",
    "type_text",
    "key_press",
    "hotkey",
    "scroll",
}

_TOOL_MENU = (
    "- run_command(cmd, cwd?): run a shell command\n"
    "- read_file(path) / list_dir(path?) / find_file(name, root?): inspect files\n"
    "- list_windows() / focus_window(title): desktop windows\n"
    "- open_app(name): open an application\n"
    "- click_element(element_id) / type_text(text) / key_press(key) / hotkey(keys): "
    "act on the screen (perceive first)"
)

VALID_ACTIONS = {"consult", "perceive", "tool", "remember", "answer"}


def _system_prompt(intent_hint: str) -> str:
    return (
        "You are the coordinator — the front brain of a local assistant. You answer "
        "the user yourself, but you can call on help when it genuinely improves the "
        "answer. Work step by step. Pick ONE next action each step:\n\n"
        '- "consult": hand the user\'s request to a specialist model (give "model"). The '
        "user's request is forwarded automatically — you only choose WHO. Use for parts "
        "outside your strength: code, hard math/reasoning.\n"
        '- "perceive": look at the screen (screenshot + element list). Use when the task '
        "needs to know what's on screen.\n"
        '- "tool": run an OS/desktop tool (give "tool" and "args").\n'
        '- "remember": save a durable fact about the user for future sessions (give '
        '"text"). Use when the user asks you to remember something, or shares a lasting '
        "preference / personal fact / ongoing-project detail. Write the fact in the "
        "user's OWN language and wording — do NOT translate it (memory recall is "
        "language-sensitive). Do NOT save trivia or one-off task wording.\n"
        '- "answer": you have everything needed; stop and let the final answer be written.\n\n'
        "Be economical: for a simple question, choose \"answer\" immediately — do not "
        "consult or act when it adds nothing. Never consult the same model twice. "
        f"{intent_hint}\n\n"
        'Respond ONLY with JSON: {"action": "consult|perceive|tool|remember|answer", '
        '"model": "<expert id>", "tool": "<name>", "args": {...}, "text": "<fact to '
        'remember>", "thinking": "<short reason>"}'
    )

ANSWER_DEFAULT = {"action": "answer", "thinking": "defaulting to answer"}


async def available_expert_models(coordinator_model: str) -> dict[str, dict]:
    """Registry models actually installed in Ollama, minus the coordinator itself."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            resp.raise_for_status()
            installed = {m["name"] for m in resp.json().get("models", [])}
    except Exception as exc:
        logger.warning("expert discovery failed, assuming registry: %s", exc)
        installed = set(OLLAMA_MODELS)
    return {
        mid: meta
        for mid, meta in OLLAMA_MODELS.items()
        if mid in installed and mid != coordinator_model
    }


def _expert_menu(experts: dict[str, dict]) -> str:
    if not experts:
        return "(no specialist models available — answer yourself or use tools)"
    return "\n".join(f'- "{mid}": {meta["hint"]}' for mid, meta in experts.items())


def _build_decision_context(
    task: str,
    conversation: list[dict] | None,
    experts: dict[str, dict],
    notes: list[str],
    memories: str = "",
) -> str:
    parts = []
    if memories:
        parts.append(
            "Long-term memory about the user (recalled — use if relevant, don't "
            f"re-save):\n{memories}\n"
        )
    if conversation:
        recent = conversation[-6:]
        convo = "\n".join(
            f"{m.get('role', 'user')}: {str(m.get('content', ''))[:500]}" for m in recent
        )
        parts.append(f"Conversation so far:\n{convo}\n")
    parts.append(f"User's latest message:\n{task}\n")
    parts.append(f"Specialist models you can consult:\n{_expert_menu(experts)}\n")
    parts.append(f"OS/desktop tools:\n{_TOOL_MENU}\n")
    if notes:
        parts.append("What you've gathered so far this turn:\n" + "\n".join(notes[-8:]))
    else:
        parts.append("You have not gathered anything yet this turn.")
    return "\n".join(parts)


async def _decide_step(
    coordinator_model: str, system: str, context: str
) -> dict:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": context},
    ]
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": coordinator_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.1},
            },
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()
    return extract_json_object(content, ANSWER_DEFAULT)


async def _consult_expert(
    model: str,
    task: str,
    conversation: list[dict] | None,
    emit: Callable[[dict], None],
    abort: asyncio.Event,
) -> str:
    """Stream a specialist model's answer to the user's request.

    The expert answers the user's VERBATIM request — the small coordinator only
    chooses which expert to call, it does NOT re-author the task (an 8B model
    paraphrases unreliably, and the expert would then follow the drifted wording
    onto the wrong errand). Recent conversation is included for context. The
    expert's tokens stream as ``expert_delta`` events so the user watches the
    hand-off live. Returns the full answer.
    """
    context = ""
    if conversation:
        recent = "\n".join(
            f"{m.get('role', 'user')}: {str(m.get('content', ''))[:400]}"
            for m in conversation[-4:]
        )
        context = f"Recent conversation for context:\n{recent}\n\n"
    messages = [
        {
            "role": "system",
            "content": (
                "You are a specialist assistant consulted to help answer the user's "
                "request. Answer it directly, precisely and concisely. Match the user's "
                "language."
            ),
        },
        {"role": "user", "content": f"{context}User's request:\n{task}"},
    ]
    parts: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_BASE_URL}/api/chat",
                json={"model": model, "messages": messages, "stream": True},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if abort.is_set():
                        break
                    if not line.strip():
                        continue
                    chunk = extract_json_object(line, {})
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        parts.append(piece)
                        emit(make_event("expert_delta", model=model, content=piece))
    except Exception as exc:
        emit(make_event("error", content=f"Consult {model} failed: {exc}"))
        return f"(consulting {model} failed: {exc})"
    return "".join(parts).strip()


async def run_coordinator(
    task: str,
    emit: Callable[[dict], None],
    abort: asyncio.Event,
    conversation: list[dict] | None = None,
    project_cwd: str | None = None,
    coordinator_model: str | None = None,
    intent_hint: str = "",
    memories: str = "",
    session_id: str | None = None,
) -> LoopOutcome:
    """Run the in-turn coordinator loop. See module docstring."""
    coordinator_model = coordinator_model or OLLAMA_MODEL
    experts = await available_expert_models(coordinator_model)
    system = _system_prompt(intent_hint)

    notes: list[str] = []  # human-readable evidence gathered this turn (grounds the reply)
    history: list[dict] = []  # for perceive() + desktop-tool safety gating
    last_observation = ""
    consulted: set[str] = set()
    steps = 0

    while steps < COORDINATOR_MAX_STEPS and not abort.is_set():
        steps += 1
        context = _build_decision_context(task, conversation, experts, notes, memories)
        try:
            decision = await _decide_step(coordinator_model, system, context)
        except Exception as exc:
            emit(make_event("error", content=f"Coordinator error: {exc}"))
            return LoopOutcome("error", _render_notes(notes), f"Coordinator error: {exc}")

        action = str(decision.get("action", "answer")).strip().lower()
        if action not in VALID_ACTIONS:
            action = "answer"
        thinking = decision.get("thinking", "")
        if thinking:
            emit(make_event("thinking", content=thinking))

        if action == "answer":
            return LoopOutcome("done", _render_notes(notes))

        if action == "consult":
            model = decision.get("model", "")
            if model not in experts:
                notes.append(f"(tried to consult unavailable model {model!r}; skipped)")
                continue
            if model in consulted and any(model in n for n in notes):
                notes.append(f"(already consulted {model}; not repeating)")
                continue
            consulted.add(model)
            # Show the real request, not the coordinator's (possibly drifted) paraphrase.
            emit(make_event("consult", model=model, content=task))
            answer = await _consult_expert(model, task, conversation, emit, abort)
            notes.append(f"{model} answered: {answer[:1200]}")
            continue

        if action == "remember":
            fact = str(decision.get("text") or "").strip()
            if fact:
                mem_id = await save_memory(fact, kind="fact", session_id=session_id)
                if mem_id:
                    emit(make_event("memory", content=fact))
                    notes.append(f"Saved to long-term memory: {fact}")
            continue

        if action == "perceive":
            last_observation = await agent_loop.perceive(task, history, emit)
            notes.append(f"Screen observation:\n{last_observation[:1200]}")
            continue

        # action == "tool"
        tool = decision.get("tool", "")
        args = decision.get("args", {}) or {}
        if tool not in COORDINATOR_TOOLS:
            notes.append(f"(unknown tool {tool!r}; skipped)")
            continue
        block = unsafe_tool_block_reason(tool, task, last_observation)
        if block:
            emit(make_event("error", content=block))
            notes.append(f"Blocked: {block}")
            continue
        args = agent_loop.apply_project_cwd_to_args(tool, args, project_cwd)
        emit(make_event("action", tool=tool, args=args))
        try:
            result = await agent_loop.execute_tool(tool, args, emit)
        except Exception as exc:
            result = f"Error executing {tool}: {exc}"
            emit(make_event("error", content=result))
        notes.append(f"{tool}({args}) -> {result[:800]}")
        if tool in agent_loop.POST_ACTION_OBSERVE_TOOLS:
            last_observation = await agent_loop.perceive(task, history, emit)
        await asyncio.sleep(0.2)

    return LoopOutcome(
        "aborted" if abort.is_set() else "max_steps",
        _render_notes(notes),
    )


def _render_notes(notes: list[str]) -> str:
    return "\n".join(f"- {n}" for n in notes[-15:]) if notes else ""

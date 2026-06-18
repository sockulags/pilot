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
from agents.gateway import refine_query
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
from skill_library import format_skills, search_skills
from tools import registry

logger = logging.getLogger(__name__)

# The coordinator's tool allowlist and menu come straight from the registry,
# read per turn (not frozen at import) so MCP tools registered at startup are
# included: see registry.coordinator_tool_names() / registry.tool_menu().

VALID_ACTIONS = {"consult", "perceive", "tool", "remember", "clarify", "answer"}


def _system_prompt(intent_hint: str) -> str:
    return (
        "You are the coordinator — the front brain of a local assistant. You answer "
        "the user yourself, but you can call on help when it genuinely improves the "
        "answer. Work step by step. Pick ONE next action each step:\n\n"
        '- "clarify": the request is too vague or ambiguous to act on well — ask the '
        'user ONE short question (give "question" in the user\'s language) instead of '
        "guessing or starting work. Use sparingly, only when truly unclear.\n"
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
        "consult or act when it adds nothing. Never consult the same model twice.\n"
        "If the user asks what you can do or to list your tools, you DO have the tools "
        "and specialist models listed below — describe them; never claim you have none. "
        "Never claim to have run a tool, searched, or navigated unless you actually "
        "took that action this turn; do not invent a 'technical error' to excuse not "
        "acting — either act, or answer honestly.\n"
        "This is a Windows machine: in run_command use Windows/PowerShell commands "
        "(dir, Get-ChildItem, cd) — never 'pwd' or 'ls'; prefer the file tools "
        f"(list_dir/read_file/search_files) over shell for inspecting files.\n{intent_hint}\n\n"
        "Take your next step by EITHER calling exactly one of the provided "
        "tools/functions, OR responding with a single JSON object: "
        '{"action": "clarify|consult|perceive|tool|remember|answer", '
        '"question": "<for clarify>", "model": "<expert id>", "tool": "<name>", '
        '"args": {...}, "text": "<fact to remember>", "thinking": "<short reason>"}'
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
    skills: str = "",
) -> str:
    parts = []
    if skills:
        parts.append(
            "Relevant know-how for this kind of request — follow it (it tells you "
            f"which tool to use and how):\n{skills}\n"
        )
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
    parts.append(f"OS/desktop tools:\n{registry.tool_menu()}\n")
    if notes:
        parts.append("What you've gathered so far this turn:\n" + "\n".join(notes[-8:]))
    else:
        parts.append("You have not gathered anything yet this turn.")
    return "\n".join(parts)


META_ACTIONS = {"answer", "consult", "perceive", "remember", "clarify"}


def _meta_action_schemas(experts: dict[str, dict]) -> list[dict]:
    """Function schemas for the coordinator's non-OS actions, so a tool-calling
    model can pick them structurally alongside the OS tools from the registry."""
    model_schema: dict = {"type": "string", "description": "Specialist model id to consult"}
    if experts:
        model_schema["enum"] = list(experts.keys())

    def fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    return [
        fn("answer", "You have everything needed; stop gathering and let the final "
                     "answer be written.", {}, []),
        fn("consult", "Hand the user's request to a specialist model — you only choose "
                      "WHO; the request is forwarded verbatim. Use for code or hard "
                      "reasoning.", {"model": model_schema}, ["model"]),
        fn("perceive", "Look at the screen (screenshot + element list) when the task "
                       "needs to know what's on screen.", {}, []),
        fn("remember", "Save a durable fact about the user for future sessions, written "
                       "in the user's OWN language.", {"text": {"type": "string"}}, ["text"]),
        fn("clarify", "Ask the user ONE short clarifying question when the request is "
                      "too vague to act on.", {"question": {"type": "string"}}, ["question"]),
    ]


def _map_call_to_decision(name: str, args: dict, thinking: str) -> dict:
    """Map a native tool/function call to the coordinator's decision dict."""
    if name in META_ACTIONS:
        return {"action": name, "thinking": thinking, **args}
    # Any other function name is an OS tool from the registry.
    return {"action": "tool", "tool": name, "args": args, "thinking": thinking}


def _normalize_decision(parsed: dict) -> dict:
    """Coerce the many JSON shapes local models emit into a valid decision.

    Observed live: qwen3 emits native tool_calls (handled before this);
    qwen2.5-coder writes the OpenAI ``{"name","arguments"}`` shape as text;
    gemma4 writes ``{"action":"read_file",...}`` — putting the tool name in the
    action field instead of ``action:"tool"`` (which the old loop silently
    dropped, so gemma never actually read files — see session d76543e3).
    """
    thinking = parsed.get("thinking", "")
    # OpenAI function-call shape emitted as plain text content.
    if "name" in parsed and "action" not in parsed:
        args = parsed.get("arguments", parsed.get("args", {})) or {}
        if isinstance(args, str):
            args = extract_json_object(args, {})
        return _map_call_to_decision(str(parsed["name"]), args, thinking)
    action = str(parsed.get("action", "")).strip()
    # Tool name placed directly in "action" (gemma) — remap to a tool call.
    if action and action not in VALID_ACTIONS and action in registry.coordinator_tool_names():
        return _map_call_to_decision(action, parsed.get("args", {}) or {}, thinking)
    return parsed


def _decision_from_message(msg: dict) -> dict:
    """Turn an Ollama chat message into a coordinator decision.

    Prefers a native tool call; otherwise normalises a JSON action object from
    the content, so every model in the flora (native tool_calls, OpenAI-shape
    text, or action-with-tool-name) yields a valid decision (hardened fallback).
    """
    calls = msg.get("tool_calls") or []
    content = (msg.get("content") or "").strip()
    if calls:
        fn = calls[0].get("function", {}) or {}
        name = str(fn.get("name", "")).strip()
        args = fn.get("arguments", {})
        if isinstance(args, str):
            args = extract_json_object(args, {})
        if name:
            return _map_call_to_decision(name, args or {}, content)
    parsed = extract_json_object(content, {}) if "{" in content else {}
    if parsed:
        normalized = _normalize_decision(parsed)
        if normalized.get("action"):
            return normalized
    return dict(ANSWER_DEFAULT)


async def _decide_step(
    coordinator_model: str,
    system: str,
    context: str,
    experts: dict[str, dict] | None = None,
    use_tools: bool = False,
) -> dict:
    """Ask the front brain for its next step.

    Tools-capable models get native function-calling (registry OS tools + the
    meta-actions); the response is mapped back to a decision dict. Non-tools
    models (and any endpoint that rejects the tools payload) use the plain JSON
    path. Either way a decision dict is returned.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": context},
    ]
    payload: dict = {
        "model": coordinator_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    if use_tools:
        payload["tools"] = registry.tool_schemas() + _meta_action_schemas(experts or {})

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        if use_tools and resp.status_code >= 400:
            # Endpoint/model rejected the tools payload — retry as plain JSON.
            payload.pop("tools", None)
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        msg = resp.json().get("message", {}) or {}

    if use_tools:
        return _decision_from_message(msg)
    return extract_json_object((msg.get("content") or "").strip(), ANSWER_DEFAULT)


async def _consult_expert(
    model: str,
    task: str,
    refined: str,
    conversation: list[dict] | None,
    emit: Callable[[dict], None],
    abort: asyncio.Event,
) -> str:
    """Stream a specialist model's answer to the user's request.

    The expert gets the gateway-``refined`` English instruction (clearer, and
    local experts reason better in English) PLUS the user's verbatim words, which
    stay authoritative if the two ever conflict — a guard against any refinement
    drift. Recent conversation is included for context. The expert's tokens
    stream as ``expert_delta`` events so the user watches the hand-off live.
    Returns the full answer.
    """
    context = ""
    if conversation:
        recent = "\n".join(
            f"{m.get('role', 'user')}: {str(m.get('content', ''))[:400]}"
            for m in conversation[-4:]
        )
        context = f"Recent conversation for context:\n{recent}\n\n"
    request = f"Request:\n{refined}"
    if refined.strip() != task.strip():
        request += f"\n\nUser's original words (authoritative if they conflict):\n{task}"
    messages = [
        {
            "role": "system",
            "content": (
                "You are a specialist assistant consulted to help answer the user's "
                "request. Answer it directly, precisely and concisely. Match the user's "
                "language."
            ),
        },
        {"role": "user", "content": f"{context}{request}"},
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
    required_first_tool: dict | None = None,
    require_file_output: bool = False,
) -> LoopOutcome:
    """Run the in-turn coordinator loop. See module docstring."""
    coordinator_model = coordinator_model or OLLAMA_MODEL
    experts = await available_expert_models(coordinator_model)
    system = _system_prompt(intent_hint)
    # Tools-capable models drive their decisions via native function-calling;
    # others (and a tools-rejecting endpoint) fall back to the JSON action path.
    use_tools = bool(OLLAMA_MODELS.get(coordinator_model, {}).get("tools"))
    # Retrieve task-relevant skills once (the "how" layer); injected each step.
    skills = format_skills(await search_skills(task))

    notes: list[str] = []  # human-readable evidence gathered this turn (grounds the reply)
    history: list[dict] = []  # for perceive() + desktop-tool safety gating
    last_observation = ""
    consulted: set[str] = set()
    refined: str | None = None  # English-pivot instruction, computed lazily on first consult
    steps = 0
    file_output_done = False

    if required_first_tool and not abort.is_set():
        tool = str(required_first_tool.get("tool") or "").strip()
        args = dict(required_first_tool.get("args") or {})
        if tool in registry.coordinator_tool_names():
            args = agent_loop.apply_project_cwd_to_args(tool, args, project_cwd)
            emit(make_event("action", tool=tool, args=args))
            try:
                result = await agent_loop.execute_tool(tool, args, emit)
            except Exception as exc:
                result = f"Error executing {tool}: {exc}"
                emit(make_event("error", content=result))
            notes.append(f"{tool}({args}) -> {result[:800]}")
            if tool == "run_command" and _command_writes_file(str(args.get("cmd") or args.get("command") or "")):
                file_output_done = True
        else:
            notes.append(f"(required first tool {tool!r} unavailable; skipped)")

    while steps < COORDINATOR_MAX_STEPS and not abort.is_set():
        steps += 1
        context = _build_decision_context(task, conversation, experts, notes, memories, skills)
        try:
            decision = await _decide_step(coordinator_model, system, context, experts, use_tools)
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
            if require_file_output and not file_output_done:
                notes.append(
                    "The user requested a local output file, but no file-writing command "
                    "has run yet. Use run_command to write the file and verify it before answering."
                )
                continue
            return LoopOutcome("done", _render_notes(notes))

        if action == "clarify":
            question = str(decision.get("question") or "").strip()
            if question:
                return LoopOutcome("needs_input", _render_notes(notes), question)
            # No question produced — fall through to answering rather than stalling.
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
            # Refine/translate the request once per turn for the expert (English
            # pivot); the verbatim task stays authoritative inside _consult_expert.
            if refined is None:
                refined = await refine_query(conversation, task)
            emit(make_event("consult", model=model, content=refined))
            answer = await _consult_expert(model, task, refined, conversation, emit, abort)
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
        if tool not in registry.coordinator_tool_names():
            notes.append(f"(unknown tool {tool!r}; skipped)")
            continue
        block = unsafe_tool_block_reason(tool, task, last_observation)
        if block:
            emit(make_event("error", content=block))
            notes.append(f"Blocked: {block}")
            continue
        args = agent_loop.apply_project_cwd_to_args(tool, args, project_cwd)
        tool, args, repair_note = agent_loop.repair_web_tool_call(tool, args, task)
        if repair_note:
            emit(make_event("thinking", content=repair_note))
        emit(make_event("action", tool=tool, args=args))
        try:
            result = await agent_loop.execute_tool(tool, args, emit)
        except Exception as exc:
            result = f"Error executing {tool}: {exc}"
            emit(make_event("error", content=result))
        notes.append(f"{tool}({args}) -> {result[:800]}")
        if tool == "run_command" and _command_writes_file(str(args.get("cmd") or args.get("command") or "")):
            file_output_done = True
        if tool in agent_loop.POST_ACTION_OBSERVE_TOOLS:
            last_observation = await agent_loop.perceive(task, history, emit)
        await asyncio.sleep(0.2)

    return LoopOutcome(
        "aborted" if abort.is_set() else "max_steps",
        _render_notes(notes),
    )


def _render_notes(notes: list[str]) -> str:
    return "\n".join(f"- {n}" for n in notes[-15:]) if notes else ""


def _command_writes_file(cmd: str) -> bool:
    lowered = cmd.lower()
    return any(
        token in lowered
        for token in (
            "set-content",
            "out-file",
            "new-item",
            " add-content",
            ">>",
            ">",
            "tee-object",
            "python -c",
        )
    )

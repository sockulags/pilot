import asyncio
import json
import logging
import os
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Callable
from agents.router import route_next_action, analyze_screenshot
from agents.perception import capture_local_webpage, perceive_screen
from agents.context_manager import ContextBudgetError, is_context_overflow
from agents.safety import unsafe_tool_block_reason
from agents.runtime_state import RuntimeState
from agents.turn_policy import build_task_context, web_query
from tools import (
    screenshot, get_screen_size,
    click, click_element, type_text, scroll, move_mouse, key_press, hotkey,
    open_app, run_codex,
    active_window_title, list_dir, read_file, find_file, list_windows, focus_window,
    search_files, github_issues, github_prs, github_repo, web_search, fetch_url, web_research_result,
)
from tools.extras import (
    search_in_files, http_request, read_document, list_processes,
    read_clipboard, write_clipboard,
)
from tools.web import infer_requested_source_count, infer_web_query, task_requires_sources
from tools import registry
from tools.comfyui import generate_image
from tool_results import ToolResult
from config import MAX_AGENT_STEPS, OLLAMA_VISION_ENABLED, PERCEPTION_ENABLED

logger = logging.getLogger(__name__)

# Behaviour sets are derived from the single tool registry (tools/registry.py),
# so a tool's streaming / observe-after / deterministic nature is declared once.
STREAMING_TOOLS = registry.streaming_tool_names()
DESKTOP_TOOLS = registry.desktop_tool_names()
POST_ACTION_OBSERVE_TOOLS = registry.observe_after_tool_names()
DETERMINISTIC_TOOLS = registry.deterministic_tool_names()


def make_event(type_: str, **kwargs) -> dict:
    return {"type": type_, **kwargs}


@dataclass
class LoopOutcome:
    """What the agent loop accomplished this turn.

    The loop no longer owns the user-facing message; it returns this so the
    shared conversational layer (orchestrator.compose_reply) can phrase the
    reply. ``action_log`` grounds that reply; ``detail`` is a block reason or raw
    output that directly answers the task.
    """

    status: str  # "done" | "blocked" | "aborted" | "max_steps" | "error"
    action_log: str = ""
    detail: str = ""
    runtime_state: RuntimeState | None = None


@dataclass(frozen=True)
class PerceptionResult:
    """Trusted control status kept separate from untrusted screen text."""

    observation: str
    context_exhausted: bool = False


def normalize_perception(value: PerceptionResult | str) -> PerceptionResult:
    """Compatibility boundary for existing test doubles and custom integrations."""
    return value if isinstance(value, PerceptionResult) else PerceptionResult(str(value or ""))


def render_action_log(history: list[dict]) -> str:
    """Render the agent's actions this turn as plain text grounding for the reply."""
    items = [
        item
        for item in history
        if item.get("type") in ("action", "blocked", "done_rejected")
    ]
    if not items:
        return ""
    return "\n".join(f"- {item['type']}: {str(item['content'])[:300]}" for item in items[-15:])


async def run_agent_loop(
    task: str,
    emit: Callable[[dict], None],
    abort_event: asyncio.Event,
    conversation: list[dict] | None = None,
    project_cwd: str | None = None,
    model: str | None = None,
) -> LoopOutcome:
    history: list[dict] = []
    runtime_state = RuntimeState()
    failed_tools: set[str] = set()
    command_counts: dict[tuple[str, str], int] = {}
    desktop_target_hint = ""
    last_observation = ""  # persists the latest screen perception across steps
    steps = 0

    emit(make_event("thinking", content=f"Starting task: {task}"))

    while steps < MAX_AGENT_STEPS and not abort_event.is_set():
        steps += 1
        screen_observation = last_observation

        try:
            decision = await route_next_action(
                task,
                history,
                failed_tools,
                screen_observation=screen_observation,
                conversation=conversation,
                model=model,
            )
        except Exception as e:
            emit(make_event("error", content=f"Router error: {e}"))
            runtime_state.record_error(f"Router error: {e}")
            return LoopOutcome("error", render_action_log(history), f"Router error: {e}", runtime_state)

        tool = decision.get("tool", "done")
        args = decision.get("args", {})
        thinking = decision.get("thinking", "")

        if thinking:
            emit(make_event("thinking", content=thinking))

        # A desktop action was chosen but we haven't looked at the screen yet:
        # perceive (elements + optional vision), then re-route so the model can
        # pick the right click_element id. Runs without a vision model — the
        # element list alone is enough.
        if tool in DESKTOP_TOOLS and not screen_observation and PERCEPTION_ENABLED:
            perception = normalize_perception(await perceive(task, history, emit))
            if perception.context_exhausted:
                message = "Perception stopped after context recovery was exhausted."
                runtime_state.record_error(message, "perceive", {})
                return LoopOutcome("error", render_action_log(history), message, runtime_state)
            screen_observation = perception.observation
            last_observation = screen_observation
            try:
                decision = await route_next_action(
                    task,
                    history,
                    failed_tools,
                    screen_observation=screen_observation,
                    conversation=conversation,
                    model=model,
                )
            except Exception as e:
                emit(make_event("error", content=f"Router error: {e}"))
                runtime_state.record_error(f"Router error: {e}")
                return LoopOutcome("error", render_action_log(history), f"Router error: {e}", runtime_state)
            tool = decision.get("tool", "done")
            args = decision.get("args", {})
            thinking = decision.get("thinking", "")
            if thinking:
                emit(make_event("thinking", content=thinking))

        if tool == "done":
            if task_requests_desktop_text_entry(task) and not history_has_desktop_text_entry(history):
                message = "Done rejected: task asks to write text, but no type_text action has run."
                emit(make_event("thinking", content=message))
                history.append({"type": "done_rejected", "content": message})
                await asyncio.sleep(0.3)
                continue

            if abort_event.is_set():
                return LoopOutcome("aborted", render_action_log(history), runtime_state=runtime_state)
            return LoopOutcome("done", render_action_log(history), str(args.get("summary", "")), runtime_state)

        block_reason = unsafe_tool_block_reason(tool, task, screen_observation)
        if block_reason:
            emit(make_event("error", content=block_reason))
            history.append({"type": "blocked", "content": block_reason})
            runtime_state.record_error(block_reason, tool, args)
            return LoopOutcome("blocked", render_action_log(history), block_reason, runtime_state)

        focus_block_reason = desktop_focus_block_reason(tool, desktop_target_hint)
        if focus_block_reason:
            emit(make_event("error", content=focus_block_reason))
            history.append({"type": "blocked", "content": focus_block_reason})
            runtime_state.record_error(focus_block_reason, tool, args)
            return LoopOutcome("blocked", render_action_log(history), focus_block_reason, runtime_state)

        if registry.confirmation_required(tool, args):
            reason = (
                f"Bekräftelse krävs innan jag kör {tool}: "
                f"{registry.confirmation_reason(tool, args)}"
            )
            emit(make_event(
                "confirmation_required",
                tool=tool,
                args=args,
                content=reason,
                risk_level=registry.risk_level_for(tool, args),
            ))
            history.append({"type": "blocked", "content": reason})
            runtime_state.record_confirmation_required(tool, args, reason)
            return LoopOutcome("needs_input", render_action_log(history), reason, runtime_state)

        args = apply_project_cwd_to_args(tool, args, project_cwd)
        tool, args, repair_note = repair_web_tool_call(tool, args, task)
        if repair_note:
            emit(make_event("thinking", content=repair_note))

        if tool == "run_command":
            command_key = normalize_command_key(args)
            if command_counts.get(command_key, 0) >= 2:
                block_reason = (
                    f"Repeated command blocked: {args.get('cmd', '')!r} already ran twice "
                    "without completing the task. Use the existing command output to answer, "
                    "or choose a different command."
                )
                emit(make_event("error", content=block_reason))
                history.append({"type": "blocked", "content": block_reason})
                runtime_state.record_error(block_reason, tool, args)
                return LoopOutcome("blocked", render_action_log(history), block_reason, runtime_state)
            command_counts[command_key] = command_counts.get(command_key, 0) + 1

        emit(make_event("action", tool=tool, args=args))

        try:
            result = await execute_tool(tool, args, emit)
        except Exception as e:
            result = f"Error executing {tool}: {e}"
            failed_tools.add(tool)
            emit(make_event("error", content=result))

        tool_ok = tool_execution_succeeded(tool, result)
        runtime_state.record_tool_result(tool, args, result, tool_ok)
        if not tool_ok:
            failed_tools.add(tool)
            emit(make_event("error", content=result))

        history.append({"type": "action", "content": f"{tool}({args}) -> {result[:1000]}"})
        desktop_target_hint = update_desktop_target_hint(tool, args, result, desktop_target_hint)
        if tool not in STREAMING_TOOLS:
            emit(make_event("result", content=result[:500]))

        completion_summary = command_completion_summary(task, tool, result)
        if not completion_summary and tool_ok:
            completion_summary = deterministic_completion_summary(tool, result)
        if completion_summary:
            return LoopOutcome("done", render_action_log(history), completion_summary, runtime_state)

        if tool in POST_ACTION_OBSERVE_TOOLS:
            perception = normalize_perception(await perceive(task, history, emit))
            if perception.context_exhausted:
                message = "Post-action perception stopped after context recovery was exhausted."
                runtime_state.record_error(message, "perceive", {})
                return LoopOutcome("error", render_action_log(history), message, runtime_state)
            last_observation = perception.observation

        if abort_event.is_set():
            return LoopOutcome("aborted", render_action_log(history), runtime_state=runtime_state)

        await asyncio.sleep(0.3)

    return LoopOutcome(
        "aborted" if abort_event.is_set() else "max_steps",
        render_action_log(history),
        runtime_state=runtime_state,
    )


async def perceive(
    task: str, history: list[dict], emit: Callable[[dict], None]
) -> PerceptionResult:
    """Observe the screen as a Set-of-Marks: enumerate interactive elements, draw
    numbered marks, and return a text observation the router can act on.

    Works without a vision model — the element list is plain text. When
    OLLAMA_VISION_ENABLED is set, a visual description from the annotated image is
    appended. Returns "" only if perception is disabled or fails.
    """
    if not PERCEPTION_ENABLED:
        return PerceptionResult("")

    try:
        emit(make_event("thinking", content="Observerar skärmen (element + bild)..."))
        local_url = _local_webpage_url(task)
        if local_url:
            # A Pilot chat turn makes the chat window active, so a desktop
            # screenshot would often analyze Pilot itself instead of the URL the
            # user named. Render loopback pages directly and fail closed if that
            # capture cannot be produced.
            annotated, observation = await asyncio.to_thread(
                capture_local_webpage, local_url
            )
        else:
            # UIA traversal + PIL annotation are blocking; run off the event loop.
            annotated, _elements, observation = await asyncio.to_thread(perceive_screen)
        emit(make_event("screenshot", image=annotated))

        context_exhausted = False
        if OLLAMA_VISION_ENABLED:
            try:
                description = await analyze_screenshot(task, annotated, history)
                observation = f"{observation}\n\nVisual description:\n{description}"
            except Exception as exc:
                logger.warning("vision analysis unavailable: %s", exc)
                context_exhausted = isinstance(exc, ContextBudgetError) or is_context_overflow(exc)
                if context_exhausted:
                    message = (
                        "Vision context recovery exhausted after one compacted retry; "
                        "the screen was not visually analyzed."
                    )
                else:
                    message = "Vision analysis unavailable: the local vision model returned no usable description."
                emit(make_event("error", content=message))
                # Give the coordinator explicit evidence about the degraded
                # observation so it does not invent a capability limitation.
                observation = f"{observation}\n\n{message}"

        history.append({"type": "screen_observation", "content": observation})
        return PerceptionResult(observation, context_exhausted=context_exhausted)
    except Exception as e:
        emit(make_event("error", content=f"Perception error: {e}"))
        return PerceptionResult("")


def _local_webpage_url(task: str) -> str | None:
    match = re.search(
        r"(?<![\w.-])(?:(?:https?://)?(?:localhost|127\.0\.0\.1|\[::1\])"
        r"(?::\d+)?(?:/[^\s<>\"']*)?)",
        task,
        re.IGNORECASE,
    )
    if not match:
        return None
    url = match.group(0).rstrip(".,;:!?)]}")
    if not url.lower().startswith(("http://", "https://")):
        url = "http://" + url
    return url


def normalize_command_key(args: dict) -> tuple[str, str]:
    cmd = str(args.get("cmd", "")).strip().lower()
    cwd = str(args.get("cwd") or os.getcwd()).strip().lower()
    return cmd, cwd


def apply_project_cwd_to_args(tool: str, args: dict, project_cwd: str | None) -> dict:
    if tool == "write_file":
        # SECURITY: the write_file confirmation gate decides "inside the project?"
        # by resolving the path against cwd — so cwd MUST be trusted, never a
        # model-supplied value. A model could otherwise pass cwd=C:\Windows\Temp
        # with a plain relative path and escape the project with no confirmation
        # (adversarial review 2026-07-03). Force cwd to the trusted base here,
        # overriding any model value; the model may still choose a path WITHIN it.
        return {**args, "cwd": project_cwd or os.getcwd()}

    if not project_cwd:
        return args

    if tool == "run_command" and not args.get("cwd"):
        return {**args, "cwd": project_cwd}

    if tool == "list_dir" and not args.get("path"):
        return {**args, "path": project_cwd}

    if tool == "find_file" and not args.get("root"):
        return {**args, "root": project_cwd}

    if tool == "search_in_files" and not args.get("root"):
        # Content search defaults to the active project so "where is X" answers
        # about the repo the user selected, not their whole home directory.
        return {**args, "root": project_cwd}

    if tool == "read_file" and args.get("path"):
        path = Path(str(args["path"]))
        if not path.is_absolute():
            return {**args, "path": str(Path(project_cwd) / path)}

    if tool == "read_document" and args.get("path"):
        path = Path(str(args["path"]))
        if not path.is_absolute():
            return {**args, "path": str(Path(project_cwd) / path)}

    return args


def repair_web_tool_call(tool: str, args: dict, task: str) -> tuple[str, dict, str | None]:
    """Self-repair obvious web tool issues before executing.

    Missing query arguments and source-heavy requests are mechanical recoveries;
    asking the user to restate them only creates the loop seen in today's
    sessions.
    """
    args = dict(args or {})
    ctx = build_task_context([], task)
    if tool == "web_search" and task_requires_sources(task):
        query = str(args.get("query") or web_query(task, ctx) or infer_web_query(task)).strip()
        min_sources = int(args.get("min_sources") or infer_requested_source_count(task, default=3))
        return "web_research", {"query": query, "task": task, "min_sources": min_sources}, (
            "Reparerar webbanrop: använder web_research eftersom uppgiften kräver källor."
        )
    if tool in {"web_search", "web_research"} and not str(args.get("query") or "").strip():
        query = web_query(task, ctx) or infer_web_query(task)
        if query:
            args["query"] = query
            args.setdefault("task", task)
            return tool, args, f"Reparerar webbanrop: härledde sökfrågan {query!r}."
    return tool, args, None


def task_requests_desktop_text_entry(task: str) -> bool:
    task_lc = task.lower()
    return any(
        token in task_lc
        for token in (
            "skriv",
            "skriva",
            "write",
            "type",
            "enter text",
            "mata in",
        )
    )


def history_has_desktop_text_entry(history: list[dict]) -> bool:
    return any(
        item.get("type") == "action" and str(item.get("content", "")).startswith("type_text(")
        for item in history
    )


def update_desktop_target_hint(tool: str, args: dict, result: str, current_hint: str) -> str:
    if tool == "open_app":
        return str(args.get("name", "")).strip() or current_hint
    if tool == "focus_window":
        focused = result.removeprefix("Focused window:").strip()
        return focused or str(args.get("title", "")).strip() or current_hint
    return current_hint


def desktop_focus_block_reason(tool: str, desktop_target_hint: str) -> str | None:
    if tool not in {"type_text", "key_press", "hotkey"} or not desktop_target_hint:
        return None

    active_title = active_window_title()
    if window_title_matches(active_title, desktop_target_hint):
        return None

    return (
        f"Blocked desktop keyboard action '{tool}': active window is "
        f"{active_title!r}, expected a window matching {desktop_target_hint!r}."
    )


def window_title_matches(active_title: str, expected_hint: str) -> bool:
    active = normalize_window_text(active_title)
    expected = normalize_window_text(expected_hint)
    if not active or not expected:
        return False
    return expected in active or active in expected


def normalize_window_text(value: str) -> str:
    normalized = value.lower().strip()
    for suffix in (".exe", ".app"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


def command_completion_summary(task: str, tool: str, result: str) -> str | None:
    if tool != "run_command":
        return None

    task_lc = task.lower()
    asks_for_location = any(token in task_lc for token in ("mapp", "folder", "directory", "cwd"))
    asks_for_listing = any(token in task_lc for token in ("lista", "list", "filer", "files"))
    has_cwd = "Current working directory:" in result
    has_listing = "Directory of " in result or "\nOutput:\n" in result

    if not (asks_for_location and asks_for_listing and has_cwd and has_listing):
        return None

    return f"Command output answered the task:\n{result[:1800]}"


def deterministic_completion_summary(tool: str, result: str) -> str | None:
    if tool in DETERMINISTIC_TOOLS:
        return result[:3000]
    return None


def tool_execution_succeeded(tool: str, text: str) -> bool:
    if tool == "web_research":
        return text.startswith("Research results for ")
    if tool == "write_file":
        return text.startswith("File written: ")
    return not (
        text.startswith("Error executing")
        or " requires argument(s): " in text
        or text.startswith("web_search failed:")
        or text.startswith("fetch_url failed:")
        or text.startswith("Unknown tool:")
    )


async def execute_tool_result(tool: str, args: dict, emit: Callable[[dict], None]) -> ToolResult:
    if tool == "web_research":
        result = await web_research_result(
            args.get("query", ""),
            args.get("task", ""),
            args.get("min_sources", 3),
        )
        return result

    text = await _execute_tool_text(tool, args, emit)
    ok = tool_execution_succeeded(tool, text)
    return ToolResult(ok=ok, kind=tool, text=text if ok else "", error=None if ok else text)


async def execute_tool(tool: str, args: dict, emit: Callable[[dict], None]) -> str:
    return (await execute_tool_result(tool, args, emit)).to_text()


async def _execute_tool_text(tool: str, args: dict, emit: Callable[[dict], None]) -> str:
    args = args or {}
    # Validate required args up front: a missing one returns a clear, model-
    # actionable message (so the model retries WITH the arg) instead of crashing
    # on args["x"] with a cryptic KeyError — e.g. web_search called with {}.
    spec = registry.get(tool)
    if spec:
        missing = [r for r in spec.required if args.get(r) in (None, "")]
        if missing:
            return f"{tool} requires argument(s): {', '.join(missing)} — provide them and call again."

    if tool == "screenshot":
        img = screenshot()
        emit(make_event("screenshot", image=img))
        return "Screenshot taken"

    elif tool == "get_screen_size":
        size = get_screen_size()
        return f"Screen size: {size['width']}x{size['height']}"

    elif tool == "click":
        return click(args["x"], args["y"], args.get("button", "left"))

    elif tool == "click_element":
        return click_element(args["element_id"], args.get("button", "left"))

    elif tool == "type_text":
        return type_text(args["text"], args.get("interval", 0.02))

    elif tool == "scroll":
        return scroll(args["x"], args["y"], args["amount"])

    elif tool == "move_mouse":
        return move_mouse(args["x"], args["y"])

    elif tool == "key_press":
        return key_press(args["key"])

    elif tool == "hotkey":
        keys = args.get("keys", [])
        if isinstance(keys, str):
            keys = keys.split("+")
        return hotkey(*keys)

    elif tool == "run_command":
        from tools.system import command_hint, shell_name

        cmd = args["cmd"]
        cwd = args.get("cwd")
        effective_cwd = cwd or os.getcwd()
        # State the shell explicitly so the model never guesses the dialect.
        header = (
            f"Command: {cmd}\nShell: {shell_name()}\n"
            f"Current working directory: {effective_cwd}\n"
        )
        emit(make_event("result", content=header))
        output_parts = []
        status: dict = {}
        async for line in run_command_async(cmd, cwd, status=status):
            output_parts.append(line)
            emit(make_event("result", content=line))
        output = "".join(output_parts)
        result = f"{header}Output:\n{output}".strip()
        # A FAILED/confused command teaches the model what to do instead of
        # repeating the mistake — but only on failure, so a successful command
        # whose output merely contains a trigger phrase is never mis-hinted
        # (adversarial review 2026-07-03). Non-zero exit or timeout == failure.
        failed = bool(status.get("timed_out")) or (status.get("returncode") not in (0, None))
        hint = command_hint(output) if failed else ""
        if hint:
            emit(make_event("result", content=hint))
            result = f"{result}\n{hint}"
        return result

    elif tool == "list_dir":
        result = list_dir(args.get("path"))
        lines = [f"Directory: {result['path']}"]
        for entry in result["entries"]:
            marker = "<DIR>" if entry["type"] == "directory" else "     "
            lines.append(f"{marker} {entry['name']}")
        return "\n".join(lines)

    elif tool == "read_file":
        result = read_file(args["path"])
        return f"File: {result['path']}\nContent:\n{result['text']}"

    elif tool == "write_file":
        from tools import write_file

        try:
            result = write_file(
                args["path"],
                str(args.get("content") or ""),
                overwrite=bool(args.get("overwrite")),
                cwd=args.get("cwd"),
            )
        except FileExistsError as exc:
            return f"write_file refused: {exc}"
        verified = "yes" if result["verified"] else "no"
        return (
            f"File written: {result['path']}\nBytes: {result['bytes']}\n"
            f"Verified: {verified}"
        )

    elif tool == "find_file":
        result = find_file(args["name"], args.get("root"))
        return "Matches:\n" + "\n".join(result["matches"])

    elif tool == "search_files":
        result = await asyncio.to_thread(
            search_files, args["query"], args.get("root"), args.get("limit", 40)
        )
        if not result["matches"]:
            return f"No files matching {args['query']!r} under {result['root']}."
        lines = [f"Matches for {args['query']!r} under {result['root']}:"]
        for m in result["matches"]:
            lines.append(f"{m['modified']}  {m['size']:>10} B  {m['path']}")
        if result.get("truncated"):
            lines.append("(more matches omitted)")
        return "\n".join(lines)

    elif tool == "search_in_files":
        result = await asyncio.to_thread(
            search_in_files, args["pattern"], args.get("root"), args.get("glob"),
            regex=bool(args.get("regex")),
        )
        if result.get("error"):
            return f"search_in_files error: {result['error']}"
        if not result["matches"]:
            return f"No content matches for {args['pattern']!r} under {result['root']}."
        lines = [f"Content matches for {args['pattern']!r} under {result['root']}:"]
        for m in result["matches"]:
            lines.append(f"{m['path']}:{m['line']}: {m['text'].strip()}")
        if result.get("truncated"):
            lines.append("(more matches omitted)")
        return "\n".join(lines)

    elif tool == "read_document":
        result = await asyncio.to_thread(read_document, args["path"], args.get("max_chars", 20000))
        if result.get("error"):
            return f"read_document error: {result['error']}"
        header = f"Document: {result['path']}"
        if result.get("pages"):
            header += f" ({result['pages']} pages)"
        note = "\n[...truncated...]" if result.get("truncated") else ""
        return f"{header}\nText:\n{result['text']}{note}"

    elif tool == "http_request":
        result = await asyncio.to_thread(
            http_request, args["url"], args.get("method", "GET"),
            headers=args.get("headers"), json_body=args.get("json_body"),
            params=args.get("params"),
        )
        if result.get("error"):
            return f"http_request error: {result['error']}"
        body = json.dumps(result["json"], ensure_ascii=False)[:4000] if "json" in result else result.get("text", "")
        return f"HTTP {result['status']} {result.get('content_type', '')}\n{body}"

    elif tool == "list_processes":
        result = await asyncio.to_thread(
            list_processes, args.get("filter_name"), args.get("limit", 40)
        )
        if result.get("error"):
            return f"list_processes error: {result['error']}"
        rows = result["processes"]
        if not rows:
            return "No matching processes."
        lines = [f"Processes ({result.get('total', len(rows))} total, showing {len(rows)}):"]
        for r in rows:
            mem = f"{r['memory_kb'] // 1024} MB" if r.get("memory_kb") else "?"
            lines.append(f"{r['pid']:>7}  {mem:>8}  {r['name']}")
        return "\n".join(lines)

    elif tool == "read_clipboard":
        result = await asyncio.to_thread(read_clipboard)
        if result.get("error"):
            return f"read_clipboard error: {result['error']}"
        text = result["text"]
        return f"Clipboard ({len(text)} chars):\n{text[:4000]}" if text else "Clipboard is empty."

    elif tool == "write_clipboard":
        result = await asyncio.to_thread(write_clipboard, args["text"])
        if result.get("error"):
            return f"write_clipboard error: {result['error']}"
        return f"Copied {result['chars']} characters to the clipboard."

    elif tool == "github_issues":
        return await asyncio.to_thread(
            github_issues, args["repo"], args.get("state", "open")
        )

    elif tool == "github_prs":
        return await asyncio.to_thread(
            github_prs, args["repo"], args.get("state", "open")
        )

    elif tool == "github_repo":
        return await asyncio.to_thread(github_repo, args["repo"])

    elif tool == "web_search":
        return await web_search(args["query"], args.get("max_results", 5))

    elif tool == "fetch_url":
        return await fetch_url(args["url"], args.get("max_chars", 4000))

    elif tool == "generate_image":
        return await asyncio.to_thread(
            generate_image,
            args["prompt"],
            width=args.get("width", 1024),
            height=args.get("height", 1024),
            steps=args.get("steps", 25),
            seed=args.get("seed"),
        )

    elif tool.startswith(registry.EXTERNAL_PREFIX):
        # A tool from an external MCP server (e.g. browser control).
        from mcp_client import manager as mcp_manager
        return await mcp_manager.call(tool, args)

    elif tool == "list_windows":
        result = list_windows()
        return "Windows:\n" + "\n".join(window["title"] for window in result["windows"])

    elif tool == "focus_window":
        result = focus_window(args["title"])
        return f"Focused window: {result['focused']}"

    elif tool == "open_app":
        return open_app(args["name"])

    elif tool == "run_codex":
        # run_codex yields TYPED events ({"type": "session"|"text"|"tool"|
        # "result"|"error", ...}), never plain strings — so the items cannot be
        # string-joined. Dispatch on each event's type, mirroring
        # ws._run_code_turn: accumulate streamed text, forward tool calls as
        # their own action events, and surface errors distinctly instead of
        # letting a failure masquerade as normal output.
        parts: list[str] = []
        result_text = ""
        error_text = ""
        async for ev in run_codex(args["prompt"]):
            etype = ev.get("type")
            if etype == "text":
                chunk = ev.get("text", "")
                if chunk:
                    parts.append(chunk)
                    emit(make_event("result", content=chunk))
            elif etype == "tool":
                emit(make_event("action", tool=ev.get("name", "tool"), args=ev.get("input", {})))
            elif etype == "result":
                result_text = ev.get("text", "") or ""
            elif etype == "error":
                raw = ev.get("text", "")
                error_text = raw if isinstance(raw, str) else str(raw)
                emit(make_event("error", content=error_text))
            # "session" events carry no output on this path — ignore them.
        streamed = "".join(parts)
        if error_text:
            # Reflect the failure in the return value so the model (and the
            # coordinator's notes) see it as an error, not a silent success.
            # tool_execution_succeeded treats the "Error executing" prefix as a
            # failed result; keep that prefix intact and truncate only the tail.
            detail = f"{streamed}\n{error_text}" if streamed else error_text
            return f"Error executing run_codex: {detail[-500:]}"
        # Prefer streamed text; fall back to the terminal result payload.
        return (streamed or result_text)[-500:]

    else:
        return f"Unknown tool: {tool}"


async def run_command_async(cmd: str, cwd=None, status: dict | None = None):
    from tools.system import run_command
    from config import COMMAND_TIMEOUT_SECONDS
    async for line in run_command(cmd, cwd, timeout=COMMAND_TIMEOUT_SECONDS, status=status):
        yield line

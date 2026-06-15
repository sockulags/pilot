import asyncio
import os
from pathlib import Path
from dataclasses import dataclass
from typing import AsyncGenerator, Callable
from agents.router import route_next_action, analyze_screenshot, vision_done_summary
from agents.perception import perceive_screen
from agents.safety import unsafe_tool_block_reason
from tools import (
    screenshot, get_screen_size,
    click, click_element, type_text, scroll, move_mouse, key_press, hotkey,
    run_command_sync, open_app, run_codex,
    active_window_title, list_dir, read_file, find_file, list_windows, focus_window,
    search_files, github_issues, github_prs, github_repo, web_search, fetch_url,
)
from tools import registry
from config import MAX_AGENT_STEPS, OLLAMA_VISION_ENABLED, PERCEPTION_ENABLED

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
            return LoopOutcome("error", render_action_log(history), f"Router error: {e}")

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
            screen_observation = await perceive(task, history, emit)
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
                return LoopOutcome("error", render_action_log(history), f"Router error: {e}")
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
                return LoopOutcome("aborted", render_action_log(history))
            return LoopOutcome("done", render_action_log(history), str(args.get("summary", "")))

        block_reason = unsafe_tool_block_reason(tool, task, screen_observation)
        if block_reason:
            emit(make_event("error", content=block_reason))
            history.append({"type": "blocked", "content": block_reason})
            return LoopOutcome("blocked", render_action_log(history), block_reason)

        focus_block_reason = desktop_focus_block_reason(tool, desktop_target_hint)
        if focus_block_reason:
            emit(make_event("error", content=focus_block_reason))
            history.append({"type": "blocked", "content": focus_block_reason})
            return LoopOutcome("blocked", render_action_log(history), focus_block_reason)

        args = apply_project_cwd_to_args(tool, args, project_cwd)

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
                return LoopOutcome("blocked", render_action_log(history), block_reason)
            command_counts[command_key] = command_counts.get(command_key, 0) + 1

        emit(make_event("action", tool=tool, args=args))

        try:
            result = await execute_tool(tool, args, emit)
        except Exception as e:
            result = f"Error executing {tool}: {e}"
            emit(make_event("error", content=result))
            # Mark the tool so the router won't retry it this session.
            failed_tools.add(tool)

        history.append({"type": "action", "content": f"{tool}({args}) -> {result[:1000]}"})
        desktop_target_hint = update_desktop_target_hint(tool, args, result, desktop_target_hint)
        if tool not in STREAMING_TOOLS:
            emit(make_event("result", content=result[:500]))

        completion_summary = command_completion_summary(task, tool, result)
        if not completion_summary:
            completion_summary = deterministic_completion_summary(tool, result)
        if completion_summary:
            return LoopOutcome("done", render_action_log(history), completion_summary)

        if tool in POST_ACTION_OBSERVE_TOOLS:
            last_observation = await perceive(task, history, emit)

        if abort_event.is_set():
            return LoopOutcome("aborted", render_action_log(history))

        await asyncio.sleep(0.3)

    return LoopOutcome(
        "aborted" if abort_event.is_set() else "max_steps",
        render_action_log(history),
    )


async def perceive(task: str, history: list[dict], emit: Callable[[dict], None]) -> str:
    """Observe the screen as a Set-of-Marks: enumerate interactive elements, draw
    numbered marks, and return a text observation the router can act on.

    Works without a vision model — the element list is plain text. When
    OLLAMA_VISION_ENABLED is set, a visual description from the annotated image is
    appended. Returns "" only if perception is disabled or fails.
    """
    if not PERCEPTION_ENABLED:
        return ""

    try:
        emit(make_event("thinking", content="Observerar skärmen (element + bild)..."))
        # UIA traversal + PIL annotation are blocking; run off the event loop.
        annotated, _elements, observation = await asyncio.to_thread(perceive_screen)
        emit(make_event("screenshot", image=annotated))

        if OLLAMA_VISION_ENABLED:
            try:
                description = await analyze_screenshot(task, annotated, history)
                observation = f"{observation}\n\nVisual description:\n{description}"
            except Exception:
                pass

        history.append({"type": "screen_observation", "content": observation})
        return observation
    except Exception as e:
        emit(make_event("error", content=f"Perception error: {e}"))
        return ""


def normalize_command_key(args: dict) -> tuple[str, str]:
    cmd = str(args.get("cmd", "")).strip().lower()
    cwd = str(args.get("cwd") or os.getcwd()).strip().lower()
    return cmd, cwd


def apply_project_cwd_to_args(tool: str, args: dict, project_cwd: str | None) -> dict:
    if not project_cwd:
        return args

    if tool == "run_command" and not args.get("cwd"):
        return {**args, "cwd": project_cwd}

    if tool == "list_dir" and not args.get("path"):
        return {**args, "path": project_cwd}

    if tool == "find_file" and not args.get("root"):
        return {**args, "root": project_cwd}

    if tool == "read_file" and args.get("path"):
        path = Path(str(args["path"]))
        if not path.is_absolute():
            return {**args, "path": str(Path(project_cwd) / path)}

    return args


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


async def execute_tool(tool: str, args: dict, emit: Callable[[dict], None]) -> str:
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
        cmd = args["cmd"]
        cwd = args.get("cwd")
        effective_cwd = cwd or os.getcwd()
        header = f"Command: {cmd}\nCurrent working directory: {effective_cwd}\n"
        emit(make_event("result", content=header))
        output_parts = []
        async for line in run_command_async(cmd, cwd):
            output_parts.append(line)
            emit(make_event("result", content=line))
        output = "".join(output_parts)
        return f"{header}Output:\n{output}".strip()

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
        output_parts = []
        async for chunk in run_codex(args["prompt"]):
            output_parts.append(chunk)
            emit(make_event("result", content=chunk))
        return "".join(output_parts)[-500:]

    else:
        return f"Unknown tool: {tool}"


async def run_command_async(cmd: str, cwd=None):
    from tools.system import run_command
    async for line in run_command(cmd, cwd):
        yield line

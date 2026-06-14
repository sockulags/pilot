import asyncio
import os
from typing import AsyncGenerator, Callable
from agents.router import route_next_action, analyze_screenshot, vision_done_summary, text_done_summary
from agents.safety import unsafe_tool_block_reason
from tools import (
    screenshot, get_screen_size,
    click, type_text, scroll, move_mouse, key_press, hotkey,
    run_command_sync, open_app, run_codex,
    active_window_title, list_dir, read_file, find_file, list_windows, focus_window,
)
from config import MAX_AGENT_STEPS, OLLAMA_VISION_ENABLED

STREAMING_TOOLS = {"run_command", "run_codex"}
DESKTOP_TOOLS = {"click", "type_text", "scroll", "move_mouse", "key_press", "hotkey"}
POST_ACTION_OBSERVE_TOOLS = DESKTOP_TOOLS | {"open_app"}
DETERMINISTIC_TOOLS = {
    "list_dir",
    "read_file",
    "find_file",
    "list_windows",
    "focus_window",
}


def make_event(type_: str, **kwargs) -> dict:
    return {"type": type_, **kwargs}


async def run_agent_loop(
    task: str,
    emit: Callable[[dict], None],
    abort_event: asyncio.Event,
) -> None:
    history: list[dict] = []
    failed_tools: set[str] = set()
    command_counts: dict[tuple[str, str], int] = {}
    desktop_target_hint = ""
    steps = 0

    emit(make_event("thinking", content=f"Starting task: {task}"))

    while steps < MAX_AGENT_STEPS and not abort_event.is_set():
        steps += 1
        screen_observation = ""

        try:
            decision = await route_next_action(
                task,
                history,
                failed_tools,
                screen_observation=screen_observation,
            )
        except Exception as e:
            emit(make_event("error", content=f"Router error: {e}"))
            break

        tool = decision.get("tool", "done")
        args = decision.get("args", {})
        thinking = decision.get("thinking", "")

        if thinking:
            emit(make_event("thinking", content=thinking))

        if tool in DESKTOP_TOOLS and not screen_observation and OLLAMA_VISION_ENABLED:
            screen_observation = await observe_screen(task, history, emit)
            try:
                decision = await route_next_action(
                    task,
                    history,
                    failed_tools,
                    screen_observation=screen_observation,
                )
            except Exception as e:
                emit(make_event("error", content=f"Router error: {e}"))
                break
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

            if not abort_event.is_set():
                if args.get("summary"):
                    summary = args["summary"]
                else:
                    try:
                        summary = await text_done_summary(task, history)
                    except Exception:
                        summary = "Task completed"
            else:
                summary = args.get("summary", "Aborted")
            emit(make_event("done", summary=summary))
            return

        block_reason = unsafe_tool_block_reason(tool, task, screen_observation)
        if block_reason:
            emit(make_event("error", content=block_reason))
            history.append({"type": "blocked", "content": block_reason})
            emit(make_event("done", summary=block_reason))
            return

        focus_block_reason = desktop_focus_block_reason(tool, desktop_target_hint)
        if focus_block_reason:
            emit(make_event("error", content=focus_block_reason))
            history.append({"type": "blocked", "content": focus_block_reason})
            emit(make_event("done", summary=focus_block_reason))
            return

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
                emit(make_event("done", summary=block_reason))
                return
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
            emit(make_event("done", summary=completion_summary))
            return

        if tool in POST_ACTION_OBSERVE_TOOLS:
            await observe_screen(task, history, emit)

        if abort_event.is_set():
            emit(make_event("done", summary="Aborted by user"))
            return

        await asyncio.sleep(0.3)

    if not abort_event.is_set():
        emit(make_event("done", summary=f"Reached max steps ({MAX_AGENT_STEPS})"))


async def observe_screen(task: str, history: list[dict], emit: Callable[[dict], None]) -> str:
    if not OLLAMA_VISION_ENABLED:
        return ""

    try:
        emit(make_event("thinking", content="Tar en skärmbild för att observera skärmen..."))
        img = screenshot()
        emit(make_event("screenshot", image=img))
        observation = await analyze_screenshot(task, img, history)
        history.append({"type": "screen_observation", "content": observation})
        emit(make_event("thinking", content=f"Skärm: {observation}"))
        return observation
    except Exception as e:
        emit(make_event("error", content=f"Screen observation error: {e}"))
        return ""


def normalize_command_key(args: dict) -> tuple[str, str]:
    cmd = str(args.get("cmd", "")).strip().lower()
    cwd = str(args.get("cwd") or os.getcwd()).strip().lower()
    return cmd, cwd


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

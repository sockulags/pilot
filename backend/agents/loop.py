import asyncio
from typing import AsyncGenerator, Callable
from agents.router import route_next_action, analyze_screenshot
from tools import (
    screenshot, get_screen_size,
    click, type_text, scroll, move_mouse, key_press, hotkey,
    run_command_sync, open_app, run_codex,
)
from config import MAX_AGENT_STEPS


def make_event(type_: str, **kwargs) -> dict:
    return {"type": type_, **kwargs}


async def run_agent_loop(
    task: str,
    emit: Callable[[dict], None],
    abort_event: asyncio.Event,
) -> None:
    history: list[dict] = []
    steps = 0

    emit(make_event("thinking", content=f"Starting task: {task}"))

    while steps < MAX_AGENT_STEPS and not abort_event.is_set():
        steps += 1

        try:
            decision = await route_next_action(task, history)
        except Exception as e:
            emit(make_event("error", content=f"Router error: {e}"))
            break

        tool = decision.get("tool", "done")
        args = decision.get("args", {})
        thinking = decision.get("thinking", "")

        if thinking:
            emit(make_event("thinking", content=thinking))

        if tool == "done":
            summary = args.get("summary", "Task completed")
            emit(make_event("done", summary=summary))
            return

        emit(make_event("action", tool=tool, args=args))

        try:
            result = await execute_tool(tool, args, emit)
        except Exception as e:
            result = f"Error executing {tool}: {e}"
            emit(make_event("error", content=result))

        history.append({"type": "action", "content": f"{tool}({args}) -> {result[:300]}"})
        emit(make_event("result", content=result[:500]))

        if abort_event.is_set():
            emit(make_event("done", summary="Aborted by user"))
            return

        await asyncio.sleep(0.3)

    if not abort_event.is_set():
        emit(make_event("done", summary=f"Reached max steps ({MAX_AGENT_STEPS})"))


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
        output_parts = []
        async for line in run_command_async(args["cmd"], args.get("cwd")):
            output_parts.append(line)
            emit(make_event("result", content=line))
        return "".join(output_parts)[-500:]

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

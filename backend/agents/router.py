import json
import logging
import re
import httpx
from config import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

PARSE_ERROR_DEFAULT = {"tool": "done", "args": {"summary": "parse error — agenten kunde inte tolka modellsvaret"}, "thinking": "parse error"}

TOOL_DESCRIPTIONS = """
Available tools:
- screenshot(): Take a screenshot of the current screen. Use when you need to see what's on screen.
- get_screen_size(): Get screen resolution.
- click(x, y, button?): Click at coordinates. button is "left" (default), "right", or "middle".
- type_text(text): Type text using the keyboard.
- scroll(x, y, amount): Scroll at coordinates. Positive amount scrolls up, negative scrolls down.
- move_mouse(x, y): Move mouse cursor.
- key_press(key): Press a key (e.g. "enter", "escape", "tab", "f5").
- hotkey(*keys): Press a keyboard shortcut (e.g. "ctrl", "c").
- run_command(cmd, cwd?): Run a shell command and stream output.
- open_app(name): Open an application by name or path.
- run_codex(prompt): Ask Claude AI to do something and stream the response.
- done(summary): Mark the task as complete with a summary.
"""

ROUTER_SYSTEM = f"""You are a computer automation agent. Given a task and context, decide the next action.
{TOOL_DESCRIPTIONS}

Respond ONLY with valid JSON in this format:
{{"tool": "tool_name", "args": {{"arg1": "value1"}}, "thinking": "Why I chose this"}}

If the task is complete, use: {{"tool": "done", "args": {{"summary": "What was accomplished"}}, "thinking": "Task done"}}
"""


async def route_next_action(task: str, history: list[dict]) -> dict:
    messages = [{"role": "system", "content": ROUTER_SYSTEM}]

    context_parts = [f"Task: {task}"]
    if history:
        context_parts.append("\nHistory of actions taken so far:")
        for item in history[-10:]:
            context_parts.append(f"- {item['type']}: {item['content'][:200]}")

    messages.append({"role": "user", "content": "\n".join(context_parts)})

    async with httpx.AsyncClient(timeout=120) as client:
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
        data = resp.json()
        content = data["message"]["content"].strip()

    return _parse_json(content)


def _parse_json(content: str) -> dict:
    # 1. Markdown code block
    for marker in ("```json", "```"):
        if marker in content:
            inner = content.split(marker)[1].split("```")[0].strip()
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                pass

    # 2. Greedy regex: first { to last } — handles nested objects correctly
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse router response: %r", content[:300])
    return PARSE_ERROR_DEFAULT


async def analyze_screenshot(task: str, image_b64: str, history: list[dict]) -> str:
    messages = [
        {
            "role": "system",
            "content": "You are a computer vision assistant. Describe what you see on the screen and how it relates to the task. Be concise and specific.",
        }
    ]

    context = f"Task: {task}"
    if history:
        context += f"\nLast action: {history[-1]['content'] if history else 'none'}"

    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": context},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ],
    })

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"].strip()

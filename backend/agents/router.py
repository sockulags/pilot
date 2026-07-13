import logging
import os
import platform
import httpx
import model_settings
from agents import providers
from agents.context_manager import is_context_overflow, manage_request
from agents.json_utils import extract_json_object
from agents.model_inventory import resolve_context_budget
from config import OLLAMA_MODEL, OLLAMA_VISION_MODEL

logger = logging.getLogger(__name__)


async def _post_local_vision(messages: list[dict], *, timeout: int) -> dict:
    """Budget a local image request and retry one normalized overflow once."""
    window = resolve_context_budget(OLLAMA_VISION_MODEL, "vision")
    managed = manage_request(messages, context_window=window)
    logger.info("local vision context plan: %s", managed.report)
    payload = {
        "model": OLLAMA_VISION_MODEL,
        "messages": managed.messages,
        "stream": False,
        "think": False,
        "options": {
            "num_ctx": window,
            "num_predict": managed.report.completion_reserve,
        },
    }
    base_url = model_settings.ollama_base_url()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        try:
            resp.raise_for_status()
        except Exception as exc:
            if not is_context_overflow(exc):
                raise
            retry = manage_request(
                messages, context_window=window, force_compact=True, retry=True,
                completion_reserve=managed.report.completion_reserve,
            )
            logger.info("local vision context retry plan: %s", retry.report)
            payload["messages"] = retry.messages
            payload["options"]["num_predict"] = retry.report.completion_reserve
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            resp.raise_for_status()
    return resp.json()

PARSE_ERROR_DEFAULT = {"tool": "done", "args": {"summary": "parse error — agenten kunde inte tolka modellsvaret"}, "thinking": "parse error"}

TOOL_DESCRIPTIONS = """
Available tools:
- list_dir(path?): List files and directories. Prefer this before shell commands for directory listings.
- read_file(path): Read a text file. Prefer this before shell commands for file contents.
- find_file(name, root?): Find files by exact name under a directory. Use when a user names a file but cwd may be wrong.
- list_windows(): List visible desktop windows.
- focus_window(title): Focus a desktop window by partial title match.
- screenshot(): Take a screenshot of the current screen. Use when you need to see what's on screen.
- get_screen_size(): Get screen resolution.
- click_element(element_id, button?): Click a numbered UI element from the current screen observation. PREFER this over click(x,y) whenever the observation lists elements — it is accurate. button is "left" (default), "right", or "middle".
- click(x, y, button?): Click at raw pixel coordinates. Use only when no element list is available (e.g. games/canvas). button is "left" (default), "right", or "middle".
- type_text(text): Type text using the keyboard into the focused control. Click the target field with click_element first.
- scroll(x, y, amount): Scroll at coordinates. Positive amount scrolls up, negative scrolls down.
- move_mouse(x, y): Move mouse cursor.
- key_press(key): Press a key (e.g. "enter", "escape", "tab", "f5").
- hotkey(*keys): Press a keyboard shortcut (e.g. "ctrl", "c").
- run_command(cmd, cwd?): Run a shell command and stream output.
- open_app(name): Open an application by name or path.
- run_codex(prompt): Ask Claude AI to do something and stream the response.
- done(summary): Mark the task as complete with a summary.
"""

SAFETY_RULE = (
    "Do not use click_element, click, type_text, scroll, move_mouse, key_press, or "
    "hotkey unless the current screen observation clearly identifies the active "
    "window or target. When the observation lists numbered elements, prefer "
    "click_element over click(x,y). If visual context is unavailable, use "
    "run_command/open_app/run_codex or done with a clear limitation instead."
)

COMMAND_RULES = (
    "If a command result directly answers the user task, use done immediately. "
    "If the user asks you to write or type text into an app, do not use done until "
    "a type_text action has actually run. Opening the app alone is not enough. "
    "Do not repeat the same command unless the previous output was missing, failed, "
    "or introduced a specific new question. Prefer Windows cmd commands such as "
    "dir, cd, type, where, and echo; do not start with Unix-only commands such as ls. "
    "For ordinary file and window tasks, prefer list_dir/read_file/find_file/"
    "list_windows/focus_window before run_command."
)

ROUTER_SYSTEM = f"""You are a computer automation agent. Given a task and context, decide the next action.
{TOOL_DESCRIPTIONS}

Safety rule: {SAFETY_RULE}
Command rule: {COMMAND_RULES}

Respond ONLY with valid JSON in this format:
{{"tool": "tool_name", "args": {{"arg1": "value1"}}, "thinking": "Why I chose this"}}

If the task is complete, use: {{"tool": "done", "args": {{"summary": "What was accomplished"}}, "thinking": "Task done"}}
"""


def command_environment_context() -> str:
    shell = "Windows cmd" if os.name == "nt" else "POSIX shell"
    return (
        "Command environment:\n"
        f"- OS: {platform.system() or os.name}\n"
        f"- Shell: {shell}\n"
        f"- Current working directory: {os.getcwd()}\n"
        f"- Rules: {COMMAND_RULES}"
    )


def _conversation_block(conversation: list[dict] | None) -> str | None:
    """Render recent user/assistant turns as plain text for cross-turn context."""
    if not conversation:
        return None
    lines = []
    for msg in conversation[-8:]:
        role = msg.get("role", "user")
        content = str(msg.get("content", ""))[:800]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def build_router_messages(
    task: str,
    history: list[dict],
    failed_tools: set[str] | None = None,
    screen_observation: str | None = None,
    conversation: list[dict] | None = None,
) -> list[dict]:
    context_parts = []
    conversation_block = _conversation_block(conversation)
    if conversation_block:
        context_parts.append(
            "Conversation so far (for context — the Task line below is what to do "
            f"now):\n{conversation_block}\n"
        )
    context_parts.append(f"Task: {task}")
    context_parts.append(f"\n{command_environment_context()}")

    if screen_observation:
        context_parts.append(f"\nCurrent screen observation:\n{screen_observation}")
    else:
        context_parts.append("\nCurrent screen observation:\nNo visual context is available.")

    context_parts.append(f"\nSafety rule: {SAFETY_RULE}")
    context_parts.append(f"\nCommand rule: {COMMAND_RULES}")

    if history:
        context_parts.append("\nHistory of actions taken so far:")
        for item in history[-10:]:
            context_parts.append(f"- {item['type']}: {item['content'][:1200]}")

    if failed_tools:
        context_parts.append(
            f"\nUNAVAILABLE TOOLS (do not call these again, they will always fail): {', '.join(sorted(failed_tools))}"
        )

    return [
        {"role": "system", "content": ROUTER_SYSTEM},
        {"role": "user", "content": "\n".join(context_parts)},
    ]


async def route_next_action(
    task: str,
    history: list[dict],
    failed_tools: set[str] | None = None,
    screen_observation: str | None = None,
    conversation: list[dict] | None = None,
    model: str | None = None,
) -> dict:
    messages = build_router_messages(
        task, history, failed_tools, screen_observation, conversation
    )

    # Through the provider layer so per-role model settings and the OpenAI/cloud
    # backend apply to router decisions too. The router has no dedicated role, so
    # no role= is passed: an assigned "default_agent" (or the run-level backend
    # override / a cloud model id) still routes it, otherwise this is the same
    # local Ollama call as before (think stays off — this is the JSON decision).
    result = await providers.chat_once(
        messages, model or OLLAMA_MODEL, temperature=0.1,
        context_role="coordinator",
    )
    content = (result.get("content") or "").strip()

    return extract_json_object(content, PARSE_ERROR_DEFAULT)


async def vision_done_summary(task: str, image_b64: str) -> str:
    """Generate a completion summary using a vision model.

    Uses Ollama's /api/chat image format: base64 strings in the top-level
    ``images`` field of the message, NOT OpenAI's ``image_url`` content blocks.
    Falls back to a text-only request if the vision call fails (e.g. because
    the configured model does not support multimodal input).
    """
    prompt = (
        f"Baserat på denna skärmbild och uppgiften '{task}', "
        "ge ett konkret och specifikt svar. "
        "Lista exakt vad du ser, inte platshållare."
    )
    messages = [
        {"role": "system", "content": "Du är en datorassistent som analyserar skärmbilder och ger konkreta svar."},
        # Ollama image format: images is a top-level list of base64 strings on
        # the message object, not nested inside the content array.
        {
            "role": "user",
            "content": prompt,
            "images": [image_b64],
        },
    ]
    # Perception stays LOCAL by design: raw screenshots must never be sent to a
    # cloud provider, so this is NOT routed through providers.chat_once (which can
    # dispatch to OpenAI/cloud). We only honour a custom Ollama URL via
    # model_settings.ollama_base_url() instead of the hardcoded env default.
    data = await _post_local_vision(messages, timeout=120)
    content = (data.get("message", {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("Vision model returned an empty visible answer")
    return content


async def analyze_screenshot(task: str, image_b64: str, history: list[dict]) -> str:
    # Values are str except the Ollama ``images`` field, which is a list of
    # base64 strings, so the message dicts hold ``str | list[str]``.
    messages: list[dict[str, str | list[str]]] = [
        {
            "role": "system",
            "content": "You are a computer vision assistant. Describe what you see on the screen and how it relates to the task. Be concise and specific.",
        }
    ]

    context = f"Task: {task}"
    if history:
        context += f"\nLast action: {history[-1]['content'] if history else 'none'}"

    # Ollama image format: base64 strings in top-level ``images`` field.
    messages.append({
        "role": "user",
        "content": context,
        "images": [image_b64],
    })

    # Perception stays LOCAL by design: raw screenshots must never leave the
    # machine, so this is NOT routed through providers.chat_once (which can
    # dispatch to a cloud backend). We only honour a custom Ollama URL via
    # model_settings.ollama_base_url() instead of the hardcoded env default.
    data = await _post_local_vision(messages, timeout=120)
    content = (data.get("message", {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("Vision model returned an empty visual description")
    return content

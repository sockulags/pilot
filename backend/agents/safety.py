UNSAFE_DESKTOP_TOOLS = {
    "click",
    "type_text",
    "scroll",
    "move_mouse",
    "key_press",
    "hotkey",
}


def unsafe_tool_block_reason(tool: str, task: str, screen_observation: str | None) -> str | None:
    """Return a reason when a desktop input tool should not be executed."""
    if tool not in UNSAFE_DESKTOP_TOOLS:
        return None

    if screen_observation and screen_observation.strip():
        return None

    return (
        f"Blocked unsafe desktop action '{tool}' for task '{task}': no visual context is "
        "available. Enable OLLAMA_VISION_ENABLED with a multimodal model, or use a "
        "non-desktop tool such as run_command/open_app/run_codex."
    )

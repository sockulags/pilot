UNSAFE_DESKTOP_TOOLS = {
    "click",
    "click_element",
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


# Keyboard/click tools whose target is the foreground window: typing or pressing
# keys against the wrong window is the dangerous case the issue calls out.
_WINDOW_TARGETED_TOOLS = {"type_text", "hotkey", "key_press", "click_element"}


def target_window_changed() -> bool:
    """True when the live foreground window differs from the last perception's.

    Returns False (no change) when nothing has been perceived yet — the
    observation gate in unsafe_tool_block_reason handles the no-observation case.
    """
    # Lazy imports keep safety free of the perception <-> tools import cycle.
    from agents.perception import current_observation_id, observation_active_window
    from tools.os_tools import active_window_title

    if not current_observation_id():
        return False

    observed = (observation_active_window() or "").strip()
    if not observed:
        # We never captured a title; can't prove a mismatch, so don't block.
        return False

    try:
        live = (active_window_title() or "").strip()
    except Exception:
        return False

    return live != observed


def desktop_action_freshness_reason(tool: str) -> str | None:
    """Refuse a window-targeted desktop action when the foreground window no
    longer matches the window the screen was last observed in.

    On mismatch the cached element ids are invalidated so subsequent
    click_element calls also fail until the screen is re-observed.
    """
    if tool not in _WINDOW_TARGETED_TOOLS:
        return None

    if not target_window_changed():
        return None

    from agents.perception import invalidate_observation, observation_active_window

    expected = observation_active_window()
    invalidate_observation()
    return (
        f"Blocked desktop action '{tool}': the active window changed since the last "
        f"screen observation (expected a window matching {expected!r}). "
        "Re-observe the screen before acting."
    )

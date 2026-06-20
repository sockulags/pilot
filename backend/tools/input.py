import pyautogui
import time


def _freshness_block(tool: str) -> str | None:
    """Return a refusal message when the foreground window changed since the
    last screen observation, else None. Lazy import avoids an import cycle
    (agents.safety -> agents.perception -> tools)."""
    from agents.safety import desktop_action_freshness_reason

    reason = desktop_action_freshness_reason(tool)
    if reason:
        return f"{tool} failed: {reason}"
    return None


# Raw-coordinate clicks (click/move_mouse) are higher-risk: they bypass the
# element grounding and aim at fixed pixels, so a window switch silently retargets
# them. They keep the upstream observation gate (agents.safety) but no window
# re-check here, since canvas/game targets legitimately have no UIA window match.
def click(x: int, y: int, button: str = "left") -> str:
    pyautogui.click(x, y, button=button)
    return f"Clicked {button} at ({x}, {y})"


def type_text(text: str, interval: float = 0.02) -> str:
    blocked = _freshness_block("type_text")
    if blocked:
        return blocked
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text}"


def scroll(x: int, y: int, amount: int) -> str:
    pyautogui.scroll(amount, x=x, y=y)
    return f"Scrolled {amount} at ({x}, {y})"


def move_mouse(x: int, y: int) -> str:
    pyautogui.moveTo(x, y)
    return f"Moved mouse to ({x}, {y})"


def key_press(key: str) -> str:
    blocked = _freshness_block("key_press")
    if blocked:
        return blocked
    pyautogui.press(key)
    return f"Pressed key: {key}"


def hotkey(*keys: str) -> str:
    blocked = _freshness_block("hotkey")
    if blocked:
        return blocked
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"

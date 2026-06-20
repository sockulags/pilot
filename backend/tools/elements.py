"""Element-grounded clicking.

click_element resolves an element id from the most recent perception
(agents/perception.py) to the control's known center and clicks there — accurate
by construction, no coordinate guessing. The agents.perception import is lazy to
avoid an import cycle (agents.perception lazily imports tools.screenshot).
"""

from .input import click


def click_element(element_id: int, button: str = "left") -> str:
    from agents.perception import get_element
    from agents.safety import desktop_action_freshness_reason

    # Refuse if the foreground window changed since perception. This also
    # invalidates the cached ids, so the get_element lookup below then fails too.
    freshness_reason = desktop_action_freshness_reason("click_element")
    if freshness_reason:
        return f"click_element failed: {freshness_reason}"

    el = get_element(element_id)
    if el is None:
        return (
            f"click_element failed: no element with id {element_id} in the last screen "
            "perception (it may be stale or from a different window). Re-observe the "
            "screen, or use click(x, y)."
        )
    cx, cy = el.center
    click(cx, cy, button)
    return f"Clicked element [{el.id}] {el.name!r} ({el.control_type}) at ({cx}, {cy})."

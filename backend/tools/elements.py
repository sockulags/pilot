"""Element-grounded clicking.

click_element resolves an element id from the most recent perception
(agents/perception.py) to the control's known center and clicks there — accurate
by construction, no coordinate guessing. The agents.perception import is lazy to
avoid an import cycle (agents.perception lazily imports tools.screenshot).
"""

from .input import click


def click_element(element_id: int, button: str = "left") -> str:
    from agents.perception import get_element

    el = get_element(element_id)
    if el is None:
        return (
            f"click_element failed: no element with id {element_id} in the last screen "
            "perception. Re-observe the screen, or use click(x, y)."
        )
    cx, cy = el.center
    click(cx, cy, button)
    return f"Clicked element [{el.id}] {el.name!r} ({el.control_type}) at ({cx}, {cy})."

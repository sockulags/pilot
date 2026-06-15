"""OS-grounded screen perception (Set-of-Marks).

Instead of asking a model to guess pixel coordinates, we ask Windows UI
Automation for the exact bounding boxes of interactive controls, label them with
numbers on the screenshot, and let the model pick a label. The click then goes to
the control's known center -> accurate, and cheap enough for a small local model
(or even the text router with just the element list).

uiautomation is imported lazily so the backend still runs (perception degrades to
a plain screenshot with no element list) when the dependency or platform is
unavailable.

DPI/multi-monitor note: element rects come from UIA in physical screen
coordinates and the screenshot from ImageGrab; these line up on a single primary
display at 100% scaling. Mixed-DPI multi-monitor setups may need offset handling.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from config import PERCEPTION_MAX_ELEMENTS

logger = logging.getLogger(__name__)

# UIA control types worth clicking/typing into.
INTERACTIVE_TYPES = {
    "ButtonControl",
    "EditControl",
    "HyperlinkControl",
    "MenuItemControl",
    "ListItemControl",
    "CheckBoxControl",
    "ComboBoxControl",
    "TabItemControl",
    "RadioButtonControl",
    "TreeItemControl",
    "SplitButtonControl",
}

# Bound traversal so a deep/expensive UIA tree can't stall a turn.
_MAX_NODES = 1500
_MAX_DEPTH = 14


@dataclass
class Element:
    id: int
    name: str
    control_type: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    center: tuple[int, int]


# Cache of the most recent perception so click_element can resolve ids -> coords.
_LAST_ELEMENTS: dict[int, Element] = {}


def get_element(element_id: int) -> Element | None:
    try:
        return _LAST_ELEMENTS.get(int(element_id))
    except (TypeError, ValueError):
        return None


def _foreground_control():
    """Resolve the UIA control for the foreground window (fallback: desktop)."""
    import ctypes

    import uiautomation as auto

    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if hwnd:
        control = auto.ControlFromHandle(hwnd)
        if control is not None:
            return control
    return auto.GetRootControl()


def enumerate_elements(max_elements: int = PERCEPTION_MAX_ELEMENTS) -> list[Element]:
    """Enumerate interactive controls in the foreground window via UIA."""
    try:
        import uiautomation  # noqa: F401
    except Exception as exc:
        logger.info("uiautomation unavailable; perception runs without elements: %s", exc)
        return []

    try:
        root = _foreground_control()
    except Exception as exc:
        logger.warning("Could not resolve foreground control: %s", exc)
        return []

    elements: list[Element] = []
    visited = 0
    next_id = 1
    stack: list[tuple[object, int]] = [(root, 0)]

    while stack and visited < _MAX_NODES and len(elements) < max_elements:
        control, depth = stack.pop()
        visited += 1
        try:
            if depth > 0 and getattr(control, "ControlTypeName", "") in INTERACTIVE_TYPES:
                rect = control.BoundingRectangle
                offscreen = bool(getattr(control, "IsOffscreen", False))
                if rect and rect.width() > 0 and rect.height() > 0 and not offscreen:
                    name = (control.Name or "").strip()
                    elements.append(
                        Element(
                            id=next_id,
                            name=name[:80] or control.ControlTypeName,
                            control_type=control.ControlTypeName,
                            rect=(rect.left, rect.top, rect.right, rect.bottom),
                            center=(rect.xcenter(), rect.ycenter()),
                        )
                    )
                    next_id += 1
            if depth < _MAX_DEPTH:
                for child in control.GetChildren():
                    stack.append((child, depth + 1))
        except Exception:
            continue

    return elements


def elements_text(elements: list[Element]) -> str:
    if not elements:
        return (
            "No interactive UI elements were detected (the app may not expose "
            "accessibility info). Fall back to coordinate-based click(x, y)."
        )
    lines = ["Interactive elements on screen — click with click_element(element_id):"]
    for el in elements:
        lines.append(f"[{el.id}] {el.name} ({el.control_type}) center=({el.center[0]},{el.center[1]})")
    return "\n".join(lines)


def annotate_screenshot(image_b64: str, elements: list[Element]) -> str:
    """Draw numbered marks + faint outlines for each element on the screenshot."""
    if not elements:
        return image_b64
    try:
        img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    except Exception:
        return image_b64

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 15)
    except Exception:
        font = ImageFont.load_default()

    for el in elements:
        left, top, right, bottom = el.rect
        draw.rectangle([left, top, right, bottom], outline=(255, 64, 64), width=1)
        label = str(el.id)
        lw = 11 * len(label) + 6
        draw.rectangle([left, top, left + lw, top + 18], fill=(220, 32, 32))
        draw.text((left + 3, top + 1), label, fill=(255, 255, 255), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def perceive_screen() -> tuple[str, list[Element], str]:
    """Capture + enumerate + annotate. Returns (annotated_b64, elements, text).

    Caches the elements so click_element can resolve ids to coordinates. Runs
    synchronously (UIA + PIL); call from async code via asyncio.to_thread.
    """
    from tools import screenshot  # lazy import to avoid an import cycle

    img_b64 = screenshot()
    elements = enumerate_elements()

    _LAST_ELEMENTS.clear()
    for el in elements:
        _LAST_ELEMENTS[el.id] = el

    annotated = annotate_screenshot(img_b64, elements)
    return annotated, elements, elements_text(elements)

import pyautogui
import time


def click(x: int, y: int, button: str = "left") -> str:
    pyautogui.click(x, y, button=button)
    return f"Clicked {button} at ({x}, {y})"


def type_text(text: str, interval: float = 0.02) -> str:
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text}"


def scroll(x: int, y: int, amount: int) -> str:
    pyautogui.scroll(amount, x=x, y=y)
    return f"Scrolled {amount} at ({x}, {y})"


def move_mouse(x: int, y: int) -> str:
    pyautogui.moveTo(x, y)
    return f"Moved mouse to ({x}, {y})"


def key_press(key: str) -> str:
    pyautogui.press(key)
    return f"Pressed key: {key}"


def hotkey(*keys: str) -> str:
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"

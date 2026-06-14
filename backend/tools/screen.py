import base64
import io
import pyautogui
from PIL import ImageGrab


def screenshot() -> str:
    img = ImageGrab.grab()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def get_screen_size() -> dict:
    w, h = pyautogui.size()
    return {"width": w, "height": h}

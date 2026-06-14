from __future__ import annotations

import base64
import httpx

from config import OLLAMA_BASE_URL, OLLAMA_VISION_MODEL


_ONE_PIXEL_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
).decode()


async def validate_vision_model() -> tuple[bool, str]:
    payload = {
        "model": OLLAMA_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": "Reply with OK if you can receive this image.",
                "images": [_ONE_PIXEL_PNG],
            }
        ],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
    except Exception as exc:
        return (
            False,
            f"Vision model {OLLAMA_VISION_MODEL!r} did not accept image input: {exc}. "
            "Fallback: ollama pull llama3.2-vision:11b and set OLLAMA_VISION_MODEL=llama3.2-vision:11b.",
        )
    return True, f"Vision model {OLLAMA_VISION_MODEL!r} accepted image input."

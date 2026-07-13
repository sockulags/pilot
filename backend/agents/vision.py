from __future__ import annotations

import base64
import logging
import model_settings
from config import OLLAMA_VISION_MODEL

logger = logging.getLogger(__name__)


_ONE_PIXEL_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
).decode()


async def validate_vision_model() -> tuple[bool, str]:
    # Perception stays LOCAL by design: image input must never be sent to a cloud
    # provider, so this validation probe is NOT routed through the provider layer.
    # We only honour a custom Ollama URL via model_settings.ollama_base_url().
    try:
        from agents.router import _post_local_vision

        runtime = model_settings.local_runtime_snapshot()
        model = runtime.vision_model or OLLAMA_VISION_MODEL
        messages = [
            {
                "role": "user",
                "content": "Reply with OK if you can receive this image.",
                "images": [_ONE_PIXEL_PNG],
            }
        ]
        data = await _post_local_vision(messages, timeout=30, requested_model=model)
        content = (data.get("message", {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("vision model returned an empty response")
    except Exception as exc:
        reason = " ".join(str(exc).split())[:240] or type(exc).__name__
        return (
            False,
            f"Local vision model {model!r} did not accept image input: {reason}. "
            "Configure a verified local vision model; for Ollama: "
            "ollama pull llama3.2-vision:11b.",
        )
    return True, f"Local vision model {model!r} accepted image input."

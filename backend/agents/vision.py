from __future__ import annotations

import base64
import logging
import httpx

import model_settings
from agents.context_manager import is_context_overflow, manage_request
from agents.model_inventory import resolve_context_budget
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
        window = resolve_context_budget(OLLAMA_VISION_MODEL, "vision")
        messages = [
            {
                "role": "user",
                "content": "Reply with OK if you can receive this image.",
                "images": [_ONE_PIXEL_PNG],
            }
        ]
        managed = manage_request(messages, context_window=window)
        logger.info("vision startup context plan: %s", managed.report)
        payload = {
            "model": OLLAMA_VISION_MODEL,
            "messages": managed.messages,
            "stream": False,
            "think": False,
            "options": {
                "num_ctx": window,
                "num_predict": managed.report.completion_reserve,
            },
        }
        base_url = model_settings.ollama_base_url()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            if resp.status_code >= 400 and is_context_overflow(
                RuntimeError(getattr(resp, "text", ""))
            ):
                retry = manage_request(
                    messages, context_window=window, force_compact=True, retry=True,
                    completion_reserve=managed.report.completion_reserve,
                )
                logger.info("vision startup context retry plan: %s", retry.report)
                payload["messages"] = retry.messages
                payload["options"]["num_predict"] = retry.report.completion_reserve
                resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        content = (resp.json().get("message", {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("vision model returned an empty response")
    except Exception as exc:
        reason = " ".join(str(exc).split())[:240] or type(exc).__name__
        return (
            False,
            f"Vision model {OLLAMA_VISION_MODEL!r} did not accept image input: {reason}. "
            "Fallback: ollama pull llama3.2-vision:11b and set OLLAMA_VISION_MODEL=llama3.2-vision:11b.",
        )
    return True, f"Vision model {OLLAMA_VISION_MODEL!r} accepted image input."

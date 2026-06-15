"""Language gateway — refine/translate a request before hand-off.

Local models reason and code better in English and benefit from a sharp,
self-contained instruction. ``refine_query`` turns a possibly-vague, possibly-
non-English user request into one clear English instruction for the next model
(a consulted expert, or the Codex/Claude code agent). The user-facing reply is
still composed in the user's own language downstream, so this only canonicalises
the INTERNAL working query.

This is deliberately a focused translation/sharpening task (not free
paraphrasing): the prompt forbids adding requirements or answering, and callers
always keep the user's verbatim words alongside the refined version as the
authoritative source if the two ever conflict.

The companion "ask instead of guessing when vague" behaviour is NOT here — it
rides on the coordinator's existing decision step (the ``clarify`` action), so
it adds no extra model call.
"""

import logging

import httpx

from config import GATEWAY_REFINE_ENABLED, OLLAMA_BASE_URL, OLLAMA_GATEWAY_MODEL

logger = logging.getLogger(__name__)

_REFINE_SYSTEM = (
    "You refine a user's request into ONE clear, self-contained instruction in "
    "ENGLISH for a specialist model. Translate to English if needed. Preserve the "
    "intent and every concrete detail exactly — names, numbers, code, file paths, "
    "quoted text. Do NOT add requirements, do NOT answer the request, do NOT change "
    "what is asked. Output ONLY the refined instruction, with no preamble or quotes."
)


async def refine_query(
    conversation: list[dict] | None, task: str, model: str | None = None
) -> str:
    """Return a clean English instruction for ``task``. Falls back to ``task``.

    Fails open: any error (or the feature disabled) returns the original task, so
    a hand-off never breaks just because refinement was unavailable.
    """
    task = (task or "").strip()
    if not GATEWAY_REFINE_ENABLED or not task:
        return task

    context = ""
    if conversation:
        recent = "\n".join(
            f"{m.get('role', 'user')}: {str(m.get('content', ''))[:300]}"
            for m in conversation[-4:]
        )
        context = f"Recent conversation (context only):\n{recent}\n\n"

    messages = [
        {"role": "system", "content": _REFINE_SYSTEM},
        {"role": "user", "content": f"{context}User's request:\n{task}"},
    ]
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": model or OLLAMA_GATEWAY_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
            )
            resp.raise_for_status()
            refined = resp.json()["message"]["content"].strip()
    except Exception as exc:
        logger.warning("refine_query failed, using verbatim task: %s", exc)
        return task

    # Guard against an empty or runaway rewrite — fall back to the original.
    if not refined or len(refined) > max(400, len(task) * 6):
        return task
    return refined

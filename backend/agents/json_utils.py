"""Shared helpers for coaxing valid JSON out of local-model responses.

Small Ollama models frequently wrap JSON in markdown fences, emit prose around
it, or leave Windows paths like C:\\Users\\... unescaped inside strings. These
helpers recover a JSON object from such responses. Used by both the tool router
(agents/router.py) and the turn orchestrator (agents/orchestrator.py).
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


def loads_lenient(raw: str) -> dict:
    """json.loads with a repair pass for unescaped backslashes.

    Local Windows paths in model prose often appear as C:\\Users\\... inside
    JSON strings. Escape only backslashes that are not valid JSON escapes.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", raw)
        return json.loads(repaired)


def extract_json_object(content: str, default: dict) -> dict:
    """Extract the first JSON object from a model response.

    Tries a fenced code block first, then a greedy ``{...}`` match. Returns
    ``default`` (a copy) when nothing parses.
    """
    # 1. Markdown code block
    for marker in ("```json", "```"):
        if marker in content:
            inner = content.split(marker)[1].split("```")[0].strip()
            try:
                return loads_lenient(inner)
            except json.JSONDecodeError:
                pass

    # 2. Greedy regex: first { to last } — handles nested objects correctly
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return loads_lenient(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse model JSON response: %r", content[:300])
    return dict(default)

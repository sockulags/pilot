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


# A backslash that does NOT begin a valid JSON escape: not one of \" \\ \/ \b
# \f \n \r \t and not a \uXXXX with four hex digits. The old pattern excluded a
# bare `u` unconditionally, so `C:\users\...` (\u not followed by 4 hex) still
# raised "Invalid \uXXXX escape" and lost the whole decision (review 2026-07-04).
_LONE_BACKSLASH_RE = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')


def loads_lenient(raw: str) -> dict:
    """json.loads with a repair pass for unescaped backslashes.

    Local Windows paths in model prose often appear as C:\\Users\\... inside
    JSON strings. Escape only backslashes that are not valid JSON escapes, so a
    path like ``C:\\users\\lucas`` (whose ``\\u`` is not a valid unicode escape)
    is recovered instead of raising.

    NOTE: a path segment beginning with a valid escape letter (``C:\\temp`` →
    ``\\t`` = TAB) is genuinely ambiguous with an intended control char and is
    not disturbed here — the app avoids it by preferring native tool-calls
    (dict arguments) over model-emitted JSON strings for file paths.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = _LONE_BACKSLASH_RE.sub(r"\\\\", raw)
        return json.loads(repaired)


def _decode_object_at(text: str, start: int) -> dict | None:
    """Parse a JSON object beginning at ``text[start]`` ('{'), ignoring trailing
    content (json.raw_decode). Falls back to the lenient backslash repair. A
    stray unbalanced ``{`` in prose simply fails here and the caller moves on."""
    decoder = json.JSONDecoder()
    for candidate in (text, _LONE_BACKSLASH_RE.sub(r"\\\\", text)):
        try:
            value, _end = decoder.raw_decode(candidate, start)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def extract_json_object(content: str, default: dict) -> dict:
    """Extract the first parseable JSON object from a model response.

    Order of attempts: each fenced code block (```json first), then a
    left-to-right scan that tries to decode a JSON object at every ``{``. The
    FIRST candidate that parses AND is a dict wins, so a fenced *example* that
    fails to parse no longer shadows the real decision, a stray ``{`` in prose no
    longer poisons the scan, and trailing prose containing a ``}`` no longer
    defeats extraction (review 2026-07-04). Returns ``default`` (a copy) when
    nothing parses.
    """
    if not content:
        return dict(default)

    # 1. Every fenced code block (prefer explicit ```json fences).
    fenced: list[str] = []
    for match in re.finditer(r"```(json)?\s*\n?(.*?)```", content, re.DOTALL | re.IGNORECASE):
        block = match.group(2).strip()
        if block:
            fenced.append(block)
    fenced.sort(key=lambda b: not b.lower().startswith("{"))  # object-looking first
    for block in fenced:
        parsed = _try_object(block)
        if parsed is not None:
            return parsed

    # 2. Scan every '{' and try to decode an object there (trailing content ok).
    for i, ch in enumerate(content):
        if ch == "{":
            parsed = _decode_object_at(content, i)
            if parsed is not None:
                return parsed

    logger.warning("Failed to parse model JSON response: %r", content[:300])
    return dict(default)


def _try_object(text: str) -> dict | None:
    try:
        value = loads_lenient(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None

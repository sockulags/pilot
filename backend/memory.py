"""Long-term semantic memory — cross-session facts and preferences.

A flat JSON store of memory items, each with a nomic-embed-text embedding, so
the assistant can recall what it learned in earlier conversations. Retrieval is
cosine similarity over the stored embeddings (the corpus is small — a personal
assistant's memory, not a knowledge base — so a linear scan is plenty).

nomic-embed-text works best with task prefixes: stored items are embedded as
``search_document:`` and queries as ``search_query:``. We keep raw item text for
display and re-embed with the document prefix on save.

Shape on disk: {"items": [{"id", "text", "kind", "session_id", "ts",
"embedding": [float, ...]}]}. The file lives under backend/data/ which is
gitignored.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
import uuid

import httpx

from config import (
    MEMORY_FILE,
    MEMORY_MIN_SCORE,
    MEMORY_TOP_K,
    OLLAMA_BASE_URL,
    OLLAMA_EMBED_MODEL,
)

logger = logging.getLogger(__name__)


async def _embed(text: str, *, is_query: bool) -> list[float] | None:
    """Embed text via Ollama, with the nomic task prefix. None on failure."""
    prefix = "search_query: " if is_query else "search_document: "
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={"model": OLLAMA_EMBED_MODEL, "input": prefix + text},
            )
            resp.raise_for_status()
            vectors = resp.json().get("embeddings") or []
            return vectors[0] if vectors else None
    except Exception as exc:
        logger.warning("embedding failed: %s", exc)
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _load() -> dict:
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("items"), list):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("could not load memory store: %s", exc)
    return {"items": []}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(MEMORY_FILE), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, MEMORY_FILE)
    except Exception as exc:
        logger.warning("could not save memory store: %s", exc)


async def save_memory(text: str, kind: str = "fact", session_id: str | None = None) -> str | None:
    """Embed and persist a memory. De-dupes near-identical text. Returns id or None."""
    text = (text or "").strip()
    if not text:
        return None

    embedding = await _embed(text, is_query=False)
    if embedding is None:
        return None

    data = _load()
    # Skip if an almost-identical memory already exists (cosine >= 0.97).
    for item in data["items"]:
        if _cosine(embedding, item.get("embedding", [])) >= 0.97:
            return item["id"]

    item_id = uuid.uuid4().hex[:12]
    data["items"].append({
        "id": item_id,
        "text": text,
        "kind": kind,
        "session_id": session_id,
        "ts": time.time(),
        "embedding": embedding,
    })
    _save(data)
    return item_id


async def search_memories(query: str, k: int | None = None) -> list[dict]:
    """Return up to k memories most similar to query, above MEMORY_MIN_SCORE.

    Each result: {"id", "text", "kind", "score"}. Empty when nothing is relevant
    or embedding fails (memory degrades gracefully — the turn still works).
    """
    query = (query or "").strip()
    if not query:
        return []
    data = _load()
    if not data["items"]:
        return []

    q = await _embed(query, is_query=True)
    if q is None:
        return []

    scored = [
        {
            "id": item["id"],
            "text": item["text"],
            "kind": item.get("kind", "fact"),
            "score": _cosine(q, item.get("embedding", [])),
        }
        for item in data["items"]
    ]
    scored.sort(key=lambda s: s["score"], reverse=True)
    top = [s for s in scored if s["score"] >= MEMORY_MIN_SCORE]
    return top[: (k or MEMORY_TOP_K)]


def list_memories() -> list[dict]:
    """All stored memories (newest first), without embeddings."""
    items = _load()["items"]
    return [
        {"id": i["id"], "text": i["text"], "kind": i.get("kind", "fact"), "ts": i.get("ts")}
        for i in sorted(items, key=lambda x: x.get("ts", 0), reverse=True)
    ]


def delete_memory(memory_id: str) -> bool:
    data = _load()
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i["id"] != memory_id]
    if len(data["items"]) != before:
        _save(data)
        return True
    return False


def format_for_prompt(memories: list[dict]) -> str:
    """Render retrieved memories as a compact context block (or '')."""
    if not memories:
        return ""
    return "\n".join(f"- {m['text']}" for m in memories)

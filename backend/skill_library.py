"""Retrievable skill files — the "when & how" layer.

Each ``skills/*.md`` file is a small playbook: frontmatter ``triggers`` (what
the user might be asking) plus a body telling the coordinator HOW to handle it
(which tool, with what arguments, and what NOT to do). Per turn we embed the
user's message and the skills' triggers with the same nomic-embed-text model the
long-term memory uses, and inject the top matches into the coordinator's
decision context. This is what stops the model guessing/simulating instead of
reaching for the right tool (the recurring failure in the analysed sessions).

Skill embeddings are computed once and cached in-process. Retrieval degrades
gracefully to "" if embedding fails — the turn still runs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from memory import _cosine, _embed

logger = logging.getLogger(__name__)

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
SKILL_TOP_K = 2
# nomic-embed-text has a high similarity floor (~0.58-0.60 for unrelated text),
# so the bar sits just above it; genuine trigger matches score notably higher.
SKILL_MIN_SCORE = 0.62


@dataclass
class Skill:
    name: str
    triggers: str
    body: str
    embedding: list[float] | None = None


_skills: list[Skill] | None = None
_embedded = False


def _parse(path: str) -> Skill:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    name = os.path.splitext(os.path.basename(path))[0]
    triggers = ""
    body = text.strip()
    if text.lstrip().startswith("---"):
        _, frontmatter, rest = text.split("---", 2)
        body = rest.strip()
        for line in frontmatter.strip().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key, value = key.strip().lower(), value.strip()
            if key == "name":
                name = value
            elif key in ("triggers", "description", "when_to_use"):
                triggers = value
    return Skill(name=name, triggers=triggers or name, body=body)


def load_skills() -> list[Skill]:
    """Load and cache skill files (parsed, not yet embedded)."""
    global _skills
    if _skills is None:
        skills: list[Skill] = []
        if os.path.isdir(SKILLS_DIR):
            for filename in sorted(os.listdir(SKILLS_DIR)):
                if not filename.endswith(".md"):
                    continue
                try:
                    skills.append(_parse(os.path.join(SKILLS_DIR, filename)))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("failed to load skill %s: %s", filename, exc)
        _skills = skills
    return _skills


async def _ensure_embedded() -> None:
    global _embedded
    skills = load_skills()
    if _embedded or not skills:
        return
    for skill in skills:
        if skill.embedding is None:
            skill.embedding = await _embed(skill.triggers, is_query=False)
    _embedded = True


async def search_skills(
    query: str, top_k: int = SKILL_TOP_K, min_score: float = SKILL_MIN_SCORE
) -> list[Skill]:
    """Return the skills whose triggers best match the user's message."""
    query = (query or "").strip()
    skills = load_skills()
    if not query or not skills:
        return []
    await _ensure_embedded()
    q = await _embed(query, is_query=True)
    if q is None:
        return []
    scored = sorted(
        ((s, _cosine(q, s.embedding or [])) for s in skills),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return [skill for skill, score in scored if score >= min_score][:top_k]


def format_skills(skills: list[Skill]) -> str:
    """Render retrieved skills as a context block for the decision prompt (or '')."""
    if not skills:
        return ""
    return "\n\n".join(f"### {s.name}\n{s.body}" for s in skills)

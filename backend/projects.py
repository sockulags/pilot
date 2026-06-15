"""Configured project roots for the chat's `code` route.

A small persisted list of folders the user can run Claude Code in, chosen per
conversation from a dropdown. Stored as JSON at PROJECTS_FILE:
[{"id": uuid, "name": basename, "path": abspath}].
"""

import json
import logging
import os
import tempfile
import uuid

from config import PILOT_PROJECT_ROOTS, PROJECTS_FILE

logger = logging.getLogger(__name__)


def _read() -> list[dict]:
    try:
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("Could not read projects file: %s", exc)
        return []


def _write(projects: list[dict]) -> None:
    os.makedirs(os.path.dirname(PROJECTS_FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PROJECTS_FILE), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROJECTS_FILE)


def _ensure_seeded() -> None:
    """Seed PROJECTS_FILE from PILOT_PROJECT_ROOTS the first time only."""
    if os.path.exists(PROJECTS_FILE) or not PILOT_PROJECT_ROOTS:
        return
    seeded: list[dict] = []
    for raw in PILOT_PROJECT_ROOTS.split(";"):
        path = raw.strip()
        if path and os.path.isdir(path):
            seeded.append(_make_entry(path))
    if seeded:
        _write(seeded)


def _make_entry(path: str) -> dict:
    abspath = os.path.abspath(path)
    return {"id": uuid.uuid4().hex, "name": os.path.basename(abspath.rstrip("\\/")) or abspath, "path": abspath}


def list_projects() -> list[dict]:
    _ensure_seeded()
    return _read()


def add_project(path: str) -> tuple[list[dict], str | None]:
    """Add a project root. Returns (projects, error). error is set on bad path."""
    if not path or not path.strip():
        return list_projects(), "Tom sökväg."
    abspath = os.path.abspath(path.strip())
    if not os.path.isdir(abspath):
        return list_projects(), f"Mappen finns inte: {abspath}"

    projects = list_projects()
    if any(p["path"].lower() == abspath.lower() for p in projects):
        return projects, None  # already present, no-op
    projects.append(_make_entry(abspath))
    _write(projects)
    return projects, None


def remove_project(project_id: str) -> list[dict]:
    projects = [p for p in list_projects() if p["id"] != project_id]
    _write(projects)
    return projects


def path_for_id(project_id: str) -> str | None:
    for p in list_projects():
        if p["id"] == project_id:
            return p["path"]
    return None

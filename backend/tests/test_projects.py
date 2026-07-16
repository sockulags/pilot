"""Tests for backend/projects.py (project-root persistence).

The `code` route's folder dropdown is backed by this small JSON store. These
tests exercise the full CRUD/seed surface in isolation: every test points
`projects.PROJECTS_FILE` at a path under `tmp_path`, so the developer's real
`backend/data/projects.json` is never read or written.

Note on monkeypatching: projects.py does `from config import PILOT_PROJECT_ROOTS,
PROJECTS_FILE`, binding those names inside the `projects` module at import time.
Patching `config.PROJECTS_FILE` / `config.PILOT_PROJECT_ROOTS` after import has
no effect on the module's behaviour, so we patch the names on `projects` itself.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import projects  # noqa: E402


@pytest.fixture
def projects_file(tmp_path, monkeypatch):
    """Point projects.PROJECTS_FILE at a fresh, non-existent path under tmp_path
    and default PILOT_PROJECT_ROOTS to empty (no seeding unless a test opts in)."""
    store = tmp_path / "store" / "projects.json"
    monkeypatch.setattr(projects, "PROJECTS_FILE", str(store))
    monkeypatch.setattr(projects, "PILOT_PROJECT_ROOTS", "")
    return store


def _read_raw(path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --- add_project ------------------------------------------------------------


def test_add_project_valid_dir_persists(projects_file, tmp_path):
    target = tmp_path / "myproj"
    target.mkdir()

    result, error = projects.add_project(str(target))

    assert error is None
    assert len(result) == 1
    entry = result[0]
    assert entry["path"] == os.path.abspath(str(target))
    assert entry["name"] == "myproj"
    assert isinstance(entry["id"], str) and entry["id"]

    # Persisted and re-readable via a fresh list_projects().
    reloaded = projects.list_projects()
    assert reloaded == result
    # And on disk at the monkeypatched location.
    assert _read_raw(projects_file) == result


def test_add_project_nonexistent_path_returns_error(projects_file, tmp_path):
    missing = tmp_path / "does_not_exist"
    result, error = projects.add_project(str(missing))

    assert error is not None
    assert error.startswith("Mappen finns inte:")
    assert os.path.abspath(str(missing)) in error
    # Nothing stored.
    assert result == []
    assert projects.list_projects() == []


def test_add_project_empty_and_whitespace_return_error(projects_file, tmp_path):
    # Pre-populate one valid project so we can confirm the list is left unchanged.
    target = tmp_path / "seed"
    target.mkdir()
    projects.add_project(str(target))
    before = projects.list_projects()

    for bad in ("", "   ", "\t\n"):
        result, error = projects.add_project(bad)
        assert error == "Tom sökväg."
        assert result == before
        assert projects.list_projects() == before


def test_add_project_duplicate_is_noop_case_insensitive(projects_file, tmp_path):
    target = tmp_path / "dup"
    target.mkdir()

    first, err1 = projects.add_project(str(target))
    assert err1 is None
    assert len(first) == 1

    # Same path again -> no-op.
    second, err2 = projects.add_project(str(target))
    assert err2 is None
    assert len(second) == 1

    # Same path, differing only in case -> still a no-op.
    third, err3 = projects.add_project(str(target).upper())
    assert err3 is None
    assert len(third) == 1

    assert len(projects.list_projects()) == 1


# --- remove_project ---------------------------------------------------------


def test_remove_project_known_id_removes_only_that_entry(projects_file, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    projects.add_project(str(a))
    projects.add_project(str(b))

    all_projects = projects.list_projects()
    assert len(all_projects) == 2
    a_id = next(p["id"] for p in all_projects if p["name"] == "a")

    remaining = projects.remove_project(a_id)
    assert len(remaining) == 1
    assert remaining[0]["name"] == "b"

    # Persisted.
    reloaded = projects.list_projects()
    assert len(reloaded) == 1
    assert reloaded[0]["name"] == "b"


def test_remove_project_unknown_id_is_noop(projects_file, tmp_path):
    target = tmp_path / "keep"
    target.mkdir()
    projects.add_project(str(target))
    before = projects.list_projects()

    remaining = projects.remove_project("no-such-id")
    assert remaining == before
    assert projects.list_projects() == before


# --- path_for_id ------------------------------------------------------------


def test_path_for_id_known_and_unknown(projects_file, tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    result, _ = projects.add_project(str(target))
    pid = result[0]["id"]

    assert projects.path_for_id(pid) == os.path.abspath(str(target))
    assert projects.path_for_id("unknown-id") is None


# --- _ensure_seeded (via list_projects) -------------------------------------


def test_seeding_only_includes_existing_dirs(projects_file, tmp_path, monkeypatch):
    real = tmp_path / "real_root"
    real.mkdir()
    missing = tmp_path / "ghost_root"  # not created
    roots = f"{real};{missing}"
    monkeypatch.setattr(projects, "PILOT_PROJECT_ROOTS", roots)

    seeded = projects.list_projects()
    assert len(seeded) == 1
    assert seeded[0]["path"] == os.path.abspath(str(real))
    assert seeded[0]["name"] == "real_root"


def test_seeding_does_not_overwrite_existing_file(projects_file, tmp_path, monkeypatch):
    # Pre-write content that seeding would NOT produce.
    os.makedirs(os.path.dirname(projects_file), exist_ok=True)
    preexisting = [{"id": "fixed-id", "name": "preexisting", "path": "/somewhere"}]
    with open(projects_file, "w", encoding="utf-8") as f:
        json.dump(preexisting, f)

    other = tmp_path / "other_root"
    other.mkdir()
    monkeypatch.setattr(projects, "PILOT_PROJECT_ROOTS", str(other))

    assert projects.list_projects() == preexisting


def test_missing_file_empty_roots_returns_empty(projects_file):
    # PROJECTS_FILE does not exist, PILOT_PROJECT_ROOTS is "" (fixture default).
    assert projects.list_projects() == []

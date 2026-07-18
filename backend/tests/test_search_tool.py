"""Tests for the home-rooted file search tool (tools/search.py).

search_files walks the user's home directory on every call, so the two things
most worth pinning down are where it decides to walk (_resolve_root) and where
it refuses to walk (_SKIP_DIRS pruning, limit, deadline). Every test here builds
its tree under tmp_path and monkeypatches Path.home, so no test can ever touch
the real home directory.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools import search  # noqa: E402


def _names(result):
    return {os.path.basename(m["path"]) for m in result["matches"]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A sandboxed home directory: Path.home() and the CWD both point at tmp_path.

    Pinning the CWD matters for the _resolve_root tests: a bare root name like
    "Downloads" is first tried as a relative path, so an unrelated directory in
    the real CWD could otherwise decide the outcome.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setattr(search.Path, "home", lambda: fake_home)
    monkeypatch.chdir(cwd)
    return fake_home


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #


def test_substring_match_is_case_insensitive(home):
    (home / "MyCV_Final.PDF").write_text("cv", encoding="utf-8")
    (home / "shopping-list.txt").write_text("milk", encoding="utf-8")

    result = search.search_files("cv", root=str(home))

    assert _names(result) == {"MyCV_Final.PDF"}
    assert result["truncated"] is False
    assert result["root"] == str(home)


def test_hit_carries_size_and_modified_time(home):
    (home / "report.txt").write_text("hello", encoding="utf-8")

    (hit,) = search.search_files("report", root=str(home))["matches"]

    assert hit["path"] == str(home / "report.txt")
    assert hit["size"] == 5
    # Formatted via time.strftime("%Y-%m-%d %H:%M", ...).
    assert len(hit["modified"]) == 16
    assert hit["modified"][4] == "-"


def test_glob_query_uses_fnmatch_not_substring(home):
    (home / "notes.txt").write_text("", encoding="utf-8")
    (home / "notes.md").write_text("", encoding="utf-8")
    (home / "archive-txt-backup.bin").write_text("", encoding="utf-8")

    result = search.search_files("*.txt", root=str(home))

    # fnmatch anchors the whole name: the .md and the file merely *containing*
    # "txt" are both excluded, which a substring search would not do.
    assert _names(result) == {"notes.txt"}


def test_glob_pattern_with_question_mark_and_class(home):
    (home / "log1.txt").write_text("", encoding="utf-8")
    (home / "log2.txt").write_text("", encoding="utf-8")
    (home / "log10.txt").write_text("", encoding="utf-8")

    assert _names(search.search_files("log?.txt", root=str(home))) == {
        "log1.txt",
        "log2.txt",
    }
    assert _names(search.search_files("log[1].txt", root=str(home))) == {"log1.txt"}


def test_substring_query_matches_partial_name_a_glob_would_miss(home):
    (home / "quarterly-notes.md").write_text("", encoding="utf-8")

    # No glob metacharacters -> the `needle in name.lower()` branch.
    assert _names(search.search_files("notes", root=str(home))) == {
        "quarterly-notes.md"
    }


def test_no_matches_returns_empty_list(home):
    (home / "unrelated.txt").write_text("", encoding="utf-8")

    result = search.search_files("nothing-matches-this", root=str(home))

    assert result["matches"] == []
    assert result["truncated"] is False


# --------------------------------------------------------------------------- #
# _resolve_root
# --------------------------------------------------------------------------- #


def test_root_none_defaults_to_home(home):
    (home / "cv.pdf").write_text("", encoding="utf-8")

    result = search.search_files("cv", root=None)

    assert result["root"] == str(home)
    assert _names(result) == {"cv.pdf"}


def test_bare_name_resolves_under_home(home):
    downloads = home / "Downloads"
    downloads.mkdir()
    (downloads / "cv.pdf").write_text("", encoding="utf-8")
    # Same-named file directly in home must stay out of a Downloads-scoped search.
    (home / "cv-elsewhere.pdf").write_text("", encoding="utf-8")

    result = search.search_files("cv", root="Downloads")

    assert result["root"] == str(downloads)
    assert _names(result) == {"cv.pdf"}


def test_existing_absolute_path_is_used_verbatim(home):
    projects = home / "projects"
    projects.mkdir()
    (projects / "cv.pdf").write_text("", encoding="utf-8")

    result = search.search_files("cv", root=str(projects))

    assert result["root"] == str(projects)


def test_unresolvable_root_falls_back_to_home(home):
    (home / "cv.pdf").write_text("", encoding="utf-8")

    # Neither "no-such-dir" (relative to CWD) nor home / "no-such-dir" exists.
    result = search.search_files("cv", root="no-such-dir")

    assert result["root"] == str(home)
    assert _names(result) == {"cv.pdf"}


def test_resolve_root_prefers_real_path_over_home_candidate(home):
    assert search._resolve_root(None) == home
    assert search._resolve_root("") == home
    assert search._resolve_root(str(home)) == home


# --------------------------------------------------------------------------- #
# pruning
# --------------------------------------------------------------------------- #


def test_skip_dirs_are_pruned(home):
    (home / "keep.txt").write_text("", encoding="utf-8")
    for skipped in ("node_modules", ".git"):
        d = home / skipped / "nested"
        d.mkdir(parents=True)
        (d / "keep.txt").write_text("", encoding="utf-8")

    result = search.search_files("keep", root=str(home))

    assert _names(result) == {"keep.txt"}
    assert len(result["matches"]) == 1
    assert result["matches"][0]["path"] == str(home / "keep.txt")


def test_hidden_directories_are_pruned_even_when_not_listed(home):
    hidden = home / ".hidden-not-in-skip-dirs"
    hidden.mkdir()
    (hidden / "keep.txt").write_text("", encoding="utf-8")
    (home / "keep.txt").write_text("", encoding="utf-8")

    assert ".hidden-not-in-skip-dirs" not in search._SKIP_DIRS
    result = search.search_files("keep", root=str(home))

    assert [m["path"] for m in result["matches"]] == [str(home / "keep.txt")]


def test_non_skipped_subdirectories_are_walked(home):
    nested = home / "documents" / "2026"
    nested.mkdir(parents=True)
    (nested / "cv.pdf").write_text("", encoding="utf-8")

    result = search.search_files("cv", root=str(home))

    assert _names(result) == {"cv.pdf"}


# --------------------------------------------------------------------------- #
# limit / deadline
# --------------------------------------------------------------------------- #


def test_limit_truncates_and_flags_result(home):
    for i in range(5):
        (home / f"cv-{i}.pdf").write_text("", encoding="utf-8")

    result = search.search_files("cv", root=str(home), limit=3)

    assert result["truncated"] is True
    assert len(result["matches"]) == 3
    assert "timed_out" not in result


def test_exactly_limit_matches_still_reports_truncated(home):
    for i in range(3):
        (home / f"cv-{i}.pdf").write_text("", encoding="utf-8")

    # The limit check runs after appending, so hitting it exactly short-circuits.
    result = search.search_files("cv", root=str(home), limit=3)

    assert result["truncated"] is True
    assert len(result["matches"]) == 3


def test_deadline_stops_the_walk(home, monkeypatch):
    (home / "cv-top.pdf").write_text("", encoding="utf-8")
    nested = home / "documents"
    nested.mkdir()
    (nested / "cv-nested.pdf").write_text("", encoding="utf-8")

    clock = iter([0.0])

    def fake_time():
        # First call sets the deadline at 0 + _SCAN_DEADLINE_SECONDS; every
        # later call is past it, so the walk stops after the first directory.
        return next(clock, 1_000.0)

    monkeypatch.setattr(search.time, "time", fake_time)

    result = search.search_files("cv", root=str(home))

    assert result["timed_out"] is True
    assert result["truncated"] is True
    # The top-level directory was scanned; the walk never reached documents/.
    assert _names(result) == {"cv-top.pdf"}

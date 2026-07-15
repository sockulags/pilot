import json
import subprocess
from types import SimpleNamespace
from unittest import mock

from tools import github as github_tool


def test_gh_path_uses_path_lookup():
    with mock.patch.object(github_tool.shutil, "which", return_value=r"C:\bin\gh.exe") as which:
        assert github_tool.gh_path() == r"C:\bin\gh.exe"
    which.assert_called_once_with("gh")


def test_run_gh_reports_missing_executable_without_spawning():
    with mock.patch.object(github_tool, "gh_path", return_value=None), mock.patch.object(
        github_tool.subprocess, "run"
    ) as run:
        assert github_tool._run_gh(["repo", "view"]) == (
            1,
            "",
            "GitHub CLI (gh) is not installed or not on PATH.",
        )
    run.assert_not_called()


def test_run_gh_uses_utf8_and_returns_process_output():
    proc = SimpleNamespace(returncode=2, stdout="utdata — åäö", stderr="fel")
    with mock.patch.object(github_tool, "gh_path", return_value="gh"), mock.patch.object(
        github_tool.subprocess, "run", return_value=proc
    ) as run:
        result = github_tool._run_gh(["issue", "list"], timeout=7)

    assert result == (2, "utdata — åäö", "fel")
    run.assert_called_once_with(
        ["gh", "issue", "list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=7,
    )


def test_run_gh_reports_timeout():
    with mock.patch.object(github_tool, "gh_path", return_value="gh"), mock.patch.object(
        github_tool.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(["gh", "repo", "view"], 4),
    ):
        assert github_tool._run_gh(["repo", "view"], timeout=4) == (
            1,
            "",
            "gh timed out after 4s",
        )


def test_snippet_removes_blank_lines_and_applies_line_and_width_limits():
    assert github_tool._snippet(" first \n\n second \n third ", lines=2) == "first second"
    assert github_tool._snippet("abcdefgh", width=5) == "abcde"
    assert github_tool._snippet(" \n ") == ""


def test_github_issues_reports_cli_failure_preferring_stderr():
    with mock.patch.object(github_tool, "_run_gh", return_value=(1, "stdout", "stderr")):
        assert github_tool.github_issues("owner/repo") == (
            "gh issue list failed for owner/repo: stderr"
        )


def test_github_issues_handles_malformed_and_empty_json():
    with mock.patch.object(github_tool, "_run_gh", return_value=(0, "not-json", "")):
        assert github_tool.github_issues("owner/repo") == "not-json"
    with mock.patch.object(github_tool, "_run_gh", return_value=(0, "[]", "")):
        assert github_tool.github_issues("owner/repo", state="invalid") == (
            "No open issues in owner/repo."
        )


def test_github_issues_formats_labels_and_bounded_body():
    payload = [
        {
            "number": 12,
            "title": "Fix parser",
            "labels": [{"name": "bug"}, {"name": "ready"}],
            "body": "First line\n\nSecond line\nThird line\nFourth line",
        },
        {"number": 13, "title": "No labels", "labels": [], "body": ""},
    ]
    with mock.patch.object(
        github_tool, "_run_gh", return_value=(0, json.dumps(payload), "")
    ):
        result = github_tool.github_issues("owner/repo", state="closed")

    assert "Closed issues in owner/repo (2):" in result
    assert "#12 Fix parser  [bug, ready]" in result
    assert "    First line Second line Third line" in result
    assert "Fourth line" not in result
    assert "#13 No labels" in result


def test_github_prs_reports_failure_and_handles_malformed_or_empty_json():
    with mock.patch.object(github_tool, "_run_gh", return_value=(1, "fallback", "")):
        assert github_tool.github_prs("owner/repo") == (
            "gh pr list failed for owner/repo: fallback"
        )
    with mock.patch.object(github_tool, "_run_gh", return_value=(0, "not-json", "")):
        assert github_tool.github_prs("owner/repo") == "not-json"
    with mock.patch.object(github_tool, "_run_gh", return_value=(0, "[]", "")):
        assert github_tool.github_prs("owner/repo", state="invalid") == (
            "No open pull requests in owner/repo."
        )


def test_github_prs_formats_optional_author_and_body():
    payload = [
        {"number": 5, "title": "Add feature", "author": {"login": "octo"}, "body": "Why"},
        {"number": 6, "title": "Bot PR", "author": None, "body": ""},
    ]
    with mock.patch.object(
        github_tool, "_run_gh", return_value=(0, json.dumps(payload), "")
    ):
        result = github_tool.github_prs("owner/repo", state="merged")

    assert "Merged pull requests in owner/repo (2):" in result
    assert "#5 Add feature  (@octo)" in result
    assert "    Why" in result
    assert "#6 Bot PR" in result


def test_github_repo_returns_output_or_failure_message():
    with mock.patch.object(github_tool, "_run_gh", return_value=(0, " repo details \n", "")):
        assert github_tool.github_repo("owner/repo") == "repo details"
    with mock.patch.object(github_tool, "_run_gh", return_value=(1, "", "not found")):
        assert github_tool.github_repo("owner/repo") == (
            "gh repo view failed for owner/repo: not found"
        )

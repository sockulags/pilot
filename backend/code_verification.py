"""Independent post-run verification for the external coding agents.

After Claude Code / Codex finishes a turn, Pilot never trusts the agent's own
"done" — it inspects the repo itself: what files changed (git status/diff),
whether any changes landed OUTSIDE the selected project directory, and
(opt-in) whether a known, allowlisted verification command still passes.

Everything here uses ``asyncio.create_subprocess_exec`` (never a shell) so the
repo path can't be turned into a command, and every subprocess has a timeout so
a hung git/test call can't wedge the turn. When ``cwd`` is not a git repo the
helpers degrade gracefully (``is_git_repo: False``) instead of raising.
"""

import asyncio
import json
import os

# Timeouts (seconds). git is fast; tests get a wider but still bounded budget.
GIT_TIMEOUT = 15
TEST_TIMEOUT = 300

def _auto_run_tests_enabled() -> bool:
    """Whether auto-running the verification command is opted into.

    Read live (not at import) so the config flag / env can be toggled in tests.
    Prefers ``config.CODE_VERIFY_RUN_TESTS`` and falls back to the env var.
    """
    try:
        import config

        return bool(config.CODE_VERIFY_RUN_TESTS)
    except Exception:
        return os.getenv("CODE_VERIFY_RUN_TESTS", "false").lower() == "true"


async def _run(args, cwd, timeout):
    """Run ``args`` (argv list, no shell) in ``cwd``; return (rc, stdout, stderr).

    Returns ``(None, "", reason)`` when the process can't start or times out so
    callers can degrade gracefully rather than crash.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        return None, "", f"{type(exc).__name__}: {exc}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return None, "", f"timed out after {timeout}s"
    return (
        proc.returncode,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


async def _is_git_repo(cwd) -> bool:
    if not cwd or not os.path.isdir(cwd):
        return False
    rc, out, _ = await _run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd, GIT_TIMEOUT
    )
    return rc == 0 and out.strip() == "true"


def _parse_status_porcelain(text):
    """Parse ``git status --porcelain`` into (changed_files, untracked).

    Porcelain lines are ``XY <path>`` (``??`` = untracked). Renames appear as
    ``orig -> new``; we keep the destination path.
    """
    changed = []
    untracked = []
    for line in text.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if code == "??":
            untracked.append(path)
        else:
            changed.append(path)
    return changed, untracked


def _parse_diff_stat(text):
    """Pull (insertions, deletions) from a ``git diff --shortstat`` summary."""
    insertions = deletions = 0
    for line in text.splitlines():
        for token in line.split(","):
            token = token.strip()
            if "insertion" in token:
                insertions = int(token.split()[0])
            elif "deletion" in token:
                deletions = int(token.split()[0])
    return insertions, deletions


async def git_status_snapshot(cwd):
    """Return the set of changed+untracked paths right now (for before/after).

    Empty set when ``cwd`` isn't a git repo — callers compare snapshots and a
    non-repo simply has nothing to diff.
    """
    if not await _is_git_repo(cwd):
        return set()
    rc, out, _ = await _run(
        ["git", "status", "--porcelain"], cwd, GIT_TIMEOUT
    )
    if rc != 0:
        return set()
    changed, untracked = _parse_status_porcelain(out)
    return set(changed) | set(untracked)


def _detect_verification_command(cwd):
    """Find a SMALL allowlist of safe verification commands for this repo.

    Returns ``(argv, label)`` or ``(None, reason)``. Only commands the repo
    clearly opts into (a real ``test`` script in package.json, or pytest config)
    are eligible; arbitrary documented commands are NOT executed.
    """
    if not cwd or not os.path.isdir(cwd):
        return None, "cwd is not a directory"

    # Prefer a test command the repo documented in AGENTS.md/CLAUDE.md when it
    # maps to a known-safe runner. We do NOT execute arbitrary documented
    # commands — only ones whose runner is on our existing allowlist.
    try:
        from project_instructions import extract_documented_commands

        documented = extract_documented_commands(cwd).get("test", "")
    except Exception:
        documented = ""
    if documented:
        first = documented.split()[0]
        if first in ("pytest", "npm", "pnpm", "yarn", "uv"):
            return documented.split(), f"documented: {documented}"

    pkg = os.path.join(cwd, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, encoding="utf-8") as fh:
                scripts = (json.load(fh) or {}).get("scripts", {}) or {}
        except (OSError, ValueError, json.JSONDecodeError):
            scripts = {}
        if isinstance(scripts, dict) and scripts.get("test"):
            mgr = "npm"
            if os.path.isfile(os.path.join(cwd, "pnpm-lock.yaml")):
                mgr = "pnpm"
            elif os.path.isfile(os.path.join(cwd, "yarn.lock")):
                mgr = "yarn"
            return [mgr, "test"], f"{mgr} test"

    if os.path.isfile(os.path.join(cwd, "pyproject.toml")) or os.path.isfile(
        os.path.join(cwd, "pytest.ini")
    ):
        return ["pytest", "-q"], "pytest -q"

    return None, "no known verification command for this repo"


def _outside_changes(changed_paths, cwd):
    """Return paths that resolve OUTSIDE ``cwd`` (best-effort containment check).

    git reports paths relative to the repo root, so anything escaping ``cwd``
    via ``..`` (or an absolute path the agent emitted) is flagged.
    """
    if not cwd:
        return []
    base = os.path.realpath(cwd)
    outside = []
    for rel in changed_paths:
        abs_path = rel if os.path.isabs(rel) else os.path.join(cwd, rel)
        real = os.path.realpath(abs_path)
        if real != base and not real.startswith(base + os.sep):
            outside.append(rel)
    return outside


async def summarize_repo_changes(cwd, before=None):
    """Structured summary of the repo's working-tree changes.

    ``before`` is an optional snapshot from :func:`git_status_snapshot` taken
    BEFORE the agent ran; when given, ``turn_changed_files`` isolates what THIS
    turn touched. Non-git ``cwd`` returns ``{"is_git_repo": False, ...}``.
    """
    summary = {
        "is_git_repo": False,
        "changed_files": [],
        "untracked": [],
        "insertions": 0,
        "deletions": 0,
        "turn_changed_files": [],
        "outside_cwd": [],
    }

    if not await _is_git_repo(cwd):
        return summary
    summary["is_git_repo"] = True

    rc, status_out, _ = await _run(
        ["git", "status", "--porcelain"], cwd, GIT_TIMEOUT
    )
    if rc == 0:
        changed, untracked = _parse_status_porcelain(status_out)
        summary["changed_files"] = changed
        summary["untracked"] = untracked

    rc, stat_out, _ = await _run(
        ["git", "diff", "--shortstat"], cwd, GIT_TIMEOUT
    )
    if rc == 0:
        ins, dele = _parse_diff_stat(stat_out)
        summary["insertions"] = ins
        summary["deletions"] = dele

    all_changed = list(summary["changed_files"]) + list(summary["untracked"])
    if before is not None:
        after = set(all_changed)
        summary["turn_changed_files"] = sorted(after - set(before))
    else:
        summary["turn_changed_files"] = sorted(all_changed)

    summary["outside_cwd"] = _outside_changes(all_changed, cwd)
    return summary


async def run_verification(cwd, changed, run_tests=None):
    """Run (or explicitly skip) an allowlisted verification command.

    Returns a dict: ``{ran, passed, command, reason, returncode}``. Auto-run is
    gated on ``CODE_VERIFY_RUN_TESTS`` (overridable via ``run_tests`` for tests)
    and only fires when files actually changed AND a known command exists.
    """
    if run_tests is None:
        run_tests = _auto_run_tests_enabled()

    if not changed:
        return {"ran": False, "passed": None, "command": None,
                "reason": "no files changed; nothing to verify", "returncode": None}

    command, label = _detect_verification_command(cwd)
    if command is None:
        return {"ran": False, "passed": None, "command": None,
                "reason": label, "returncode": None}

    if not run_tests:
        return {"ran": False, "passed": None, "command": label,
                "reason": "auto-run disabled (set CODE_VERIFY_RUN_TESTS=true to enable)",
                "returncode": None}

    rc, out, err = await _run(command, cwd, TEST_TIMEOUT)
    if rc is None:
        return {"ran": False, "passed": None, "command": label,
                "reason": f"command did not run: {err}", "returncode": None}
    return {
        "ran": True,
        "passed": rc == 0,
        "command": label,
        "reason": "verification command passed" if rc == 0
        else "verification command failed",
        "returncode": rc,
        "output_tail": (out + err)[-2000:],
    }


async def verify_code_run(cwd, before=None, run_tests=None):
    """Build the full ``code_run`` artifact for one coding-agent turn.

    Combines the changed-files summary, the unexpected-change flag, and the
    (optional) verification-command result into a single dict suitable for the
    conversation meta and the ``code_verification`` event.
    """
    summary = await summarize_repo_changes(cwd, before=before)
    turn_changed = summary.get("turn_changed_files") or []
    verification = await run_verification(cwd, turn_changed, run_tests=run_tests)

    outside = summary.get("outside_cwd") or []
    return {
        "cwd": cwd,
        "is_git_repo": summary["is_git_repo"],
        "changed_files": summary["changed_files"],
        "untracked": summary["untracked"],
        "turn_changed_files": turn_changed,
        "insertions": summary["insertions"],
        "deletions": summary["deletions"],
        "outside_cwd": outside,
        "unexpected_changes": bool(outside),
        "verification": verification,
    }

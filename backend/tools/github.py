"""GitHub tools via the `gh` CLI.

First-class, read-only GitHub access so the assistant can answer "what issues /
PRs do I have" directly and locally — instead of dead-ending on the code agent
(see session 42cdda5a, where Codex's usage limit blocked listing cv_builder's
issues). `gh` carries the user's own auth, so no tokens are handled here.

`repo` accepts "owner/name" (e.g. "sockulags/cv_builder") or a bare "name" when
run inside that repo's folder.
"""

from __future__ import annotations

import json
import shutil
import subprocess


def gh_path() -> str | None:
    return shutil.which("gh")


def _run_gh(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    exe = gh_path()
    if not exe:
        return 1, "", "GitHub CLI (gh) is not installed or not on PATH."
    try:
        # gh emits UTF-8; force it (Windows text mode would otherwise decode as
        # cp1252 and mangle em-dashes/accents into mojibake).
        proc = subprocess.run(
            [exe, *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, "", f"gh timed out after {timeout}s"
    return proc.returncode, proc.stdout, proc.stderr


def _snippet(text: str, lines: int = 3, width: int = 200) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    picked = [ln.strip() for ln in body.splitlines() if ln.strip()][:lines]
    return " ".join(picked)[:width]


def github_issues(repo: str, state: str = "open", limit: int = 30) -> str:
    """List issues in a repo with a short description of each."""
    state = state if state in ("open", "closed", "all") else "open"
    code, out, err = _run_gh([
        "issue", "list", "-R", repo, "--state", state, "--limit", str(limit),
        "--json", "number,title,state,labels,body,updatedAt",
    ])
    if code != 0:
        return f"gh issue list failed for {repo}: {(err or out).strip()}"
    try:
        issues = json.loads(out or "[]")
    except json.JSONDecodeError:
        return out.strip()
    if not issues:
        return f"No {state} issues in {repo}."
    lines = [f"{state.capitalize()} issues in {repo} ({len(issues)}):"]
    for it in issues:
        labels = ", ".join(label.get("name", "") for label in it.get("labels", []))
        head = f"#{it['number']} {it['title']}"
        if labels:
            head += f"  [{labels}]"
        lines.append(head)
        desc = _snippet(it.get("body", ""))
        if desc:
            lines.append(f"    {desc}")
    return "\n".join(lines)


def github_prs(repo: str, state: str = "open", limit: int = 30) -> str:
    """List pull requests in a repo with a short description of each."""
    state = state if state in ("open", "closed", "merged", "all") else "open"
    code, out, err = _run_gh([
        "pr", "list", "-R", repo, "--state", state, "--limit", str(limit),
        "--json", "number,title,state,author,body,updatedAt",
    ])
    if code != 0:
        return f"gh pr list failed for {repo}: {(err or out).strip()}"
    try:
        prs = json.loads(out or "[]")
    except json.JSONDecodeError:
        return out.strip()
    if not prs:
        return f"No {state} pull requests in {repo}."
    lines = [f"{state.capitalize()} pull requests in {repo} ({len(prs)}):"]
    for pr in prs:
        author = (pr.get("author") or {}).get("login", "")
        head = f"#{pr['number']} {pr['title']}"
        if author:
            head += f"  (@{author})"
        lines.append(head)
        desc = _snippet(pr.get("body", ""))
        if desc:
            lines.append(f"    {desc}")
    return "\n".join(lines)


def github_repo(repo: str) -> str:
    """Show a repository overview (description, default branch, counts)."""
    code, out, err = _run_gh(["repo", "view", repo])
    if code != 0:
        return f"gh repo view failed for {repo}: {(err or out).strip()}"
    return out.strip()

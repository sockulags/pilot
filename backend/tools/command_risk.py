"""Structured command-risk classifier.

Replaces ad-hoc substring scanning with a shell-aware classifier that splits
compound commands, tokenises each part (POSIX-ish *and* Windows/PowerShell
forms) and maps tokens to risk classes. Any risky part makes the whole command
require confirmation; ambiguous commands also require confirmation.

This is intentionally pragmatic — not a full shell parser. ``shlex`` handles
POSIX splitting; PowerShell/Windows specifics are handled with targeted logic.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field


class RiskClass(str):
    """Risk class constants (str subclass so values are human-readable)."""


WRITE = "WRITE"
DELETE = "DELETE"
NETWORK = "NETWORK"
PROCESS_SPAWN = "PROCESS_SPAWN"
SECRET_ACCESS = "SECRET_ACCESS"
PACKAGE_INSTALL = "PACKAGE_INSTALL"
VERSION_CONTROL_PUSH = "VERSION_CONTROL_PUSH"
ENCODED = "ENCODED"
CODE_EXECUTION = "CODE_EXECUTION"
SAFE = "SAFE"


_HUMAN_LABELS = {
    WRITE: "writes/overwrites files",
    DELETE: "deletes files",
    NETWORK: "performs network access",
    PROCESS_SPAWN: "spawns another process",
    SECRET_ACCESS: "accesses secrets or credentials",
    PACKAGE_INSTALL: "installs packages",
    VERSION_CONTROL_PUSH: "pushes to version control",
    ENCODED: "runs an encoded command",
    CODE_EXECUTION: "evaluates dynamic code",
    SAFE: "is read-only",
}


@dataclass
class CommandRisk:
    requires_confirmation: bool
    risk_classes: set[str] = field(default_factory=set)
    reason: str = ""


# ---------------------------------------------------------------------------
# Token / pattern tables
# ---------------------------------------------------------------------------

# Read-only commands that are always safe (the *first* token of a part).
# NOTE: `find` is deliberately NOT here — Git's bundled Unix `find` on Windows
# supports `-delete`/`-exec`, so it is classified conditionally (see
# _classify_find) rather than assumed safe.
_SAFE_COMMANDS = {
    "ls", "dir", "pwd", "cd", "echo", "cat", "type", "head", "tail",
    "wc", "grep", "which", "where", "whoami", "hostname", "date",
    "pytest", "true", "false", "test", "printenv", "env",
    "get-childitem", "gci", "get-location", "gl", "write-output", "write-host",
    "test-path", "select-string", "measure-object",
}

# Language interpreters. With an inline-eval flag they run arbitrary code with
# no file on disk to inspect — the classic injection sink — so those forms are
# gated as CODE_EXECUTION. (Running a *named script file*, e.g. `python -m
# pytest` or `python build.py`, stays ungated so ordinary build/test flows are
# not interrupted; the file itself is visible and reviewable.)
_INTERPRETERS = {
    "python", "python3", "py", "node", "nodejs", "deno", "bun",
    "ruby", "perl", "php", "rscript", "pwsh-command",
}
# Inline-eval flags per interpreter family (lowercased). `-e`/`-c`/`--eval`/`-r`
# and PowerShell's `-command`/`-encodedcommand` all execute a string argument.
_INLINE_EVAL_FLAGS = {"-c", "-e", "--eval", "-r", "--exec", "eval"}

# Script hosts and loader binaries that execute a script/DLL argument directly —
# almost never benign in an autonomous agent context.
_SCRIPT_HOST_COMMANDS = {
    "wscript", "cscript", "mshta", "osascript", "rundll32", "regsvr32",
    "certutil", "bitsadmin", "msiexec", "installutil", "regasm",
}

# Windows system/persistence tools: registry, scheduled tasks, services, WMI,
# boot config, firewall. Side-effecting and a common persistence vector.
_SYSTEM_MUTATION_COMMANDS = {
    "reg", "schtasks", "sc", "wmic", "bcdedit", "netsh", "diskpart",
    "cipher", "vssadmin", "wevtutil", "net", "setx",
    "new-service", "set-service", "register-scheduledtask",
    "new-scheduledtask", "set-itemproperty", "new-itemproperty",
    "stop-service", "start-service", "restart-service",
}

# Extensions that make a bare path an executable invocation.
_EXECUTABLE_SUFFIXES = (
    ".exe", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".msi", ".scr", ".com", ".pif", ".cpl", ".jar",
)

# Verbs that, after `git`, are read-only in *every* form.
_SAFE_GIT_SUBCOMMANDS = {
    "status", "diff", "log", "show", "remote", "rev-parse",
    "describe", "blame", "ls-files",
}

# `git config`, `git branch`, `git stash` and `git fetch` are only safe in
# specific read-only forms; other forms write refs/config, delete data, or —
# for aliases and hook-like config keys — become code-execution surfaces.
# They are handled by the _classify_git_* helpers below.
_GIT_CONFIG_READ_FLAGS = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
# Config keys whose values git later executes as commands.
_GIT_CONFIG_EXEC_KEY_PREFIXES = (
    "alias.", "filter.", "core.fsmonitor", "core.sshcommand", "core.editor",
    "core.pager", "credential.helper", "diff.external",
)
_GIT_BRANCH_DELETE_FLAGS = {"-d", "-D", "--delete"}
_GIT_BRANCH_WRITE_FLAGS = {
    "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force",
    "-u", "--set-upstream", "--set-upstream-to", "--unset-upstream",
    "--edit-description",
}
_GIT_STASH_SAFE_SUBS = {"list", "show"}
_GIT_STASH_DELETE_SUBS = {"drop", "clear", "pop"}
_GIT_FETCH_DELETE_FLAGS = {"-p", "-P", "--prune", "--prune-tags"}

# Map first-token (lowercased) -> risk class for unambiguously risky commands.
_DELETE_COMMANDS = {
    "rm", "rmdir", "del", "erase", "format", "rd", "unlink", "shred",
    "remove-item", "ri", "rd", "rbp", "clear-content", "clc",
}
_WRITE_COMMANDS = {
    "set-content", "sc", "out-file", "add-content", "ac", "new-item", "ni",
    "tee-object", "tee", "move-item", "mi", "move", "mv", "copy-item", "cpi",
    "copy", "cp", "rename-item", "rni", "ren", "truncate",
}
_NETWORK_COMMANDS = {
    "curl", "wget", "iwr", "invoke-webrequest", "invoke-restmethod", "irm",
    "ssh", "scp", "ftp", "nc", "netcat", "telnet",
}
_PROCESS_SPAWN_COMMANDS = {
    "start-process", "saps", "start", "cmd", "powershell", "pwsh", "bash",
    "sh", "zsh", "spawn",
}
_CODE_EXECUTION_COMMANDS = {
    "invoke-expression", "iex", "eval", "exec",
}
_PERMISSION_COMMANDS = {
    "chmod", "chown", "chgrp", "icacls", "takeown", "attrib",
}

# Package-manager install verbs.
_PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "pip", "pip3", "uv", "cargo", "gem", "apt", "apt-get", "brew", "choco"}
_INSTALL_VERBS = {"install", "i", "add", "ci"}

# Secret-bearing path fragments.
_SECRET_FRAGMENTS = (".env", "id_rsa", "id_dsa", "id_ecdsa", "credentials", "secret", "token", ".pem", ".key")

# gh side-effecting subcommands preserved from the legacy substring set.
_GH_RISKY = ("gh issue close", "gh pr merge")

_REDIRECT_RE = re.compile(r"(^|\s)>>?(\s|$|\S)")
_ENCODED_RE = re.compile(r"-e(nc(odedcommand)?)?\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Splitting / tokenising
# ---------------------------------------------------------------------------

def _split_compound(cmd: str) -> list[str]:
    """Split on shell separators: ; && || | and newlines. Keep non-empty parts."""
    parts = re.split(r"\n|&&|\|\||;|\|", cmd)
    return [p.strip() for p in parts if p.strip()]


def _tokenize(part: str) -> list[str]:
    try:
        return shlex.split(part, posix=True)
    except ValueError:
        # Unbalanced quotes etc. — fall back to whitespace splitting.
        return part.split()


# ---------------------------------------------------------------------------
# Git sub-command rules (tokens arrive lowercased, so `-D` matches as `-d`)
# ---------------------------------------------------------------------------

def _flag_matches(arg: str, flags: set[str]) -> bool:
    """True if ``arg`` is one of ``flags``, allowing the ``--flag=value`` form."""
    return arg in flags or any(arg.startswith(f + "=") for f in flags if f.startswith("--"))


def _classify_git_config(args: list[str]) -> set[str]:
    """`git config` is safe only in explicit read-only form (--get/--list).

    Everything else — including bare ``git config key value`` — writes config,
    and keys like ``alias.*`` or ``core.fsmonitor`` make git execute the value.
    """
    if any(_flag_matches(a, _GIT_CONFIG_READ_FLAGS) for a in args):
        return set()
    classes = {WRITE}
    if any(a.startswith(_GIT_CONFIG_EXEC_KEY_PREFIXES) for a in args):
        classes.add(CODE_EXECUTION)
    return classes


def _classify_git_branch(args: list[str]) -> set[str]:
    """`git branch` is safe for listing; delete/move/force forms are not."""
    classes: set[str] = set()
    if any(_flag_matches(a, _GIT_BRANCH_DELETE_FLAGS) for a in args):
        classes.add(DELETE)
    if any(_flag_matches(a, _GIT_BRANCH_WRITE_FLAGS) for a in args):
        classes.add(WRITE)
    return classes


def _classify_git_stash(args: list[str]) -> set[str]:
    """`git stash list/show` is read-only; drop/clear/pop delete stash entries
    and every other form (including bare ``git stash``) mutates the worktree."""
    sub = args[0] if args else ""
    if sub in _GIT_STASH_SAFE_SUBS:
        return set()
    if sub in _GIT_STASH_DELETE_SUBS:
        return {DELETE}
    return {WRITE}


def _classify_git_fetch(args: list[str]) -> set[str]:
    """`git fetch` only updates remote-tracking refs, but --prune deletes them."""
    if any(_flag_matches(a, _GIT_FETCH_DELETE_FLAGS) for a in args):
        return {DELETE}
    return set()


# `find` destructive actions (Git's bundled Unix find supports these on Windows).
_FIND_DESTRUCTIVE_FLAGS = {"-delete"}
_FIND_EXEC_FLAGS = {"-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf"}


def _classify_find(args: list[str]) -> set[str]:
    """`find` is read-only unless it deletes or executes per match."""
    classes: set[str] = set()
    if any(a in _FIND_DESTRUCTIVE_FLAGS for a in args):
        classes.add(DELETE)
    if any(a in _FIND_EXEC_FLAGS for a in args):
        classes.add(PROCESS_SPAWN)
    return classes


def _looks_like_executable_path(token: str) -> bool:
    """True for a direct executable invocation: ./x, .\\x, C:\\...\\x.exe, x.bat."""
    t = token.strip().strip("'\"").lower()
    if not t:
        return False
    if t.startswith("./") or t.startswith(".\\") or t.startswith("~/"):
        return True
    if re.match(r"^[a-z]:\\", t) or re.match(r"^\\\\", t):  # C:\... or UNC \\host
        return True
    return t.endswith(_EXECUTABLE_SUFFIXES)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify_part(part: str) -> set[str]:
    classes: set[str] = set()
    lowered = part.lower()

    # Output redirection (write).
    if _REDIRECT_RE.search(part):
        classes.add(WRITE)

    # Encoded PowerShell command.
    if ("powershell" in lowered or "pwsh" in lowered) and _ENCODED_RE.search(part):
        classes.add(ENCODED)

    # Secret/credential access by path fragment anywhere in the part.
    if any(frag in lowered for frag in _SECRET_FRAGMENTS):
        classes.add(SECRET_ACCESS)

    # Preserved gh side-effecting subcommands.
    if any(needle in lowered for needle in _GH_RISKY):
        classes.add(PROCESS_SPAWN)

    tokens = _tokenize(part)
    if not tokens:
        return classes

    first = tokens[0].lower()
    rest = [t.lower() for t in tokens[1:]]

    # Direct executable invocation by path (./run.sh, .\setup.exe, C:\x\y.bat,
    # bare foo.exe) — the classifier can't see inside it, so it must be gated.
    if _looks_like_executable_path(tokens[0]):
        classes.add(PROCESS_SPAWN)

    # Language interpreters running INLINE code (python -c, node -e, perl -e,
    # php -r, Rscript -e, deno eval, pwsh -Command). A named script file is left
    # ungated (build/test flows); only the string-eval forms are code-execution.
    if first in _INTERPRETERS or first in {"powershell", "pwsh"}:
        if any(_flag_matches(a, _INLINE_EVAL_FLAGS) or a in _INLINE_EVAL_FLAGS for a in rest):
            classes.add(CODE_EXECUTION)
        if first in {"powershell", "pwsh"} and any(
            a in {"-command", "-c"} or a.startswith("-e") for a in rest
        ):
            classes.add(CODE_EXECUTION)

    # Script hosts / loaders that execute a script or DLL argument.
    if first in _SCRIPT_HOST_COMMANDS:
        classes.add(PROCESS_SPAWN)

    # Windows registry / scheduled-task / service / WMI mutation surfaces.
    if first in _SYSTEM_MUTATION_COMMANDS:
        classes.add(PROCESS_SPAWN)

    # git push vs read-only git.
    if first == "git":
        sub = rest[0] if rest else ""
        sub_args = rest[1:]
        if sub == "push":
            classes.add(VERSION_CONTROL_PUSH)
        elif sub == "config":
            classes |= _classify_git_config(sub_args)
        elif sub == "branch":
            classes |= _classify_git_branch(sub_args)
        elif sub == "stash":
            classes |= _classify_git_stash(sub_args)
        elif sub == "fetch":
            classes |= _classify_git_fetch(sub_args)
        elif sub in _SAFE_GIT_SUBCOMMANDS:
            pass
        else:
            # Unknown git subcommand — be conservative only if it looks mutating.
            if sub in {"commit", "add", "rm", "reset", "checkout", "merge", "rebase", "tag", "clean"}:
                classes.add(WRITE)

    # `find` with -delete/-exec (Git's Unix find ships these on Windows).
    if first == "find":
        classes |= _classify_find(rest)

    # Package installs.
    if first in _PACKAGE_MANAGERS and any(v in rest for v in _INSTALL_VERBS):
        classes.add(PACKAGE_INSTALL)

    if first in _DELETE_COMMANDS:
        classes.add(DELETE)
    if first in _WRITE_COMMANDS:
        classes.add(WRITE)
    if first in _NETWORK_COMMANDS:
        classes.add(NETWORK)
    if first in _PROCESS_SPAWN_COMMANDS:
        classes.add(PROCESS_SPAWN)
    if first in _CODE_EXECUTION_COMMANDS:
        classes.add(CODE_EXECUTION)
    if first in _PERMISSION_COMMANDS:
        classes.add(WRITE)

    # Inline dynamic-code / process spawn via flags, e.g. `cmd /c ...`,
    # `powershell -Command ...`, `Invoke-Expression` mid-string.
    if first in {"cmd"} and ("/c" in rest or "/k" in rest):
        classes.add(PROCESS_SPAWN)
    if "invoke-expression" in rest or "iex" in rest:
        classes.add(CODE_EXECUTION)
    if "start-process" in rest:
        classes.add(PROCESS_SPAWN)

    return classes


def classify_command(cmd: str) -> CommandRisk:
    cmd = str(cmd or "").strip()
    if not cmd:
        return CommandRisk(False, set(), "Empty command is read-only.")

    parts = _split_compound(cmd)
    all_classes: set[str] = set()
    for part in parts:
        all_classes |= _classify_part(part)

    risky = all_classes - {SAFE}
    if not risky:
        return CommandRisk(
            False,
            {SAFE},
            "Command is read-only and does not require confirmation.",
        )

    ordered = [c for c in (
        DELETE, WRITE, SECRET_ACCESS, PACKAGE_INSTALL, VERSION_CONTROL_PUSH,
        ENCODED, CODE_EXECUTION, PROCESS_SPAWN, NETWORK,
    ) if c in risky]
    labels = ", ".join(f"{c} ({_HUMAN_LABELS.get(c, c)})" for c in ordered)
    reason = f"High-risk shell command requires confirmation: {labels}."
    return CommandRisk(True, risky, reason)


def command_requires_confirmation(cmd: str) -> bool:
    return classify_command(cmd).requires_confirmation


def command_risk_reason(cmd: str) -> str:
    return classify_command(cmd).reason

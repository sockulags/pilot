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
_SAFE_COMMANDS = {
    "ls", "dir", "pwd", "cd", "echo", "cat", "type", "head", "tail",
    "wc", "grep", "find", "which", "where", "whoami", "hostname", "date",
    "pytest", "true", "false", "test", "printenv", "env",
    "get-childitem", "gci", "get-location", "gl", "write-output", "write-host",
    "test-path", "select-string", "measure-object",
}

# Verbs that, after `git`, are read-only.
_SAFE_GIT_SUBCOMMANDS = {
    "status", "diff", "log", "show", "branch", "remote", "rev-parse",
    "describe", "blame", "ls-files", "config", "fetch", "stash",
}

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

    # git push vs read-only git.
    if first == "git":
        sub = rest[0] if rest else ""
        if sub == "push":
            classes.add(VERSION_CONTROL_PUSH)
        elif sub in _SAFE_GIT_SUBCOMMANDS:
            pass
        else:
            # Unknown git subcommand — be conservative only if it looks mutating.
            if sub in {"commit", "add", "rm", "reset", "checkout", "merge", "rebase", "tag", "clean"}:
                classes.add(WRITE)

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

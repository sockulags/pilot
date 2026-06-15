"""Drive the OpenAI Codex CLI headlessly for the chat's `code` route.

The sibling of tools/codex.py (which, despite its name, drives Claude Code).
run_codex_cli spawns `codex exec --json -C <cwd> --sandbox <mode>` in a project
directory, optionally resuming a prior Codex session, and yields the SAME typed
events as the Claude driver (text / tool / session / result / error) so the
WebSocket layer is agent-agnostic.

CLI discovery: absolute CODEX_CLI, then PATH, then the Codex desktop app's
bundled CLI (%LOCALAPPDATA%\\OpenAI\\Codex\\bin\\*\\codex.exe). The bundled CLI
shares the desktop app's ChatGPT login, so headless runs work without a separate
sign-in.

Codex `exec --json` schema (thread/item): thread.started{thread_id},
turn.started, item.completed{item:{type,text,...}}, turn.completed{usage}.
"""

import asyncio
import glob
import json
import os
import shutil
import sys
from typing import AsyncGenerator

from config import CODEX_CLI

CODEX_EXEC_SANDBOX_MODE = "danger-full-access"

_resolved_cli: str | None = None

# item.completed item types we render as text vs. tool activity vs. ignore.
_IGNORED_ITEMS = {"reasoning", "todo_list"}


def _find_bundled_codex() -> str | None:
    """Locate the Codex desktop app's bundled CLI on Windows."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    pattern = os.path.join(local, "OpenAI", "Codex", "bin", "*", "codex.exe")
    matches = [p for p in glob.glob(pattern) if os.path.isfile(p)]
    if not matches:
        return None
    matches.sort(key=os.path.getmtime, reverse=True)
    return matches[0]


def resolve_codex_cli() -> str:
    """Resolve the codex executable: explicit path -> bundled desktop CLI -> PATH."""
    global _resolved_cli
    if _resolved_cli is not None:
        return _resolved_cli

    cli = CODEX_CLI or "codex"
    if os.path.isabs(cli) and os.path.isfile(cli):
        _resolved_cli = cli
    elif bundled := _find_bundled_codex():
        _resolved_cli = bundled
    elif shutil.which(cli):
        _resolved_cli = shutil.which(cli)
    else:
        _resolved_cli = cli
    return _resolved_cli


def _build_cmd(prompt: str, cwd: str | None, resume_session_id: str | None) -> list[str]:
    cli = resolve_codex_cli()
    flags = [
        "--json", "--skip-git-repo-check", "--color", "never",
        "--sandbox", CODEX_EXEC_SANDBOX_MODE,
    ]
    if cwd:
        flags += ["-C", cwd]

    if resume_session_id:
        args = [cli, "exec", "resume", *flags, resume_session_id, prompt]
    else:
        args = [cli, "exec", *flags, prompt]

    # .exe runs directly; a .cmd/.bat wrapper or bare name goes via cmd /c.
    if sys.platform == "win32" and not cli.lower().endswith(".exe"):
        return ["cmd", "/c", *args]
    return args


async def run_codex_cli(
    prompt: str, cwd: str | None = None, resume_session_id: str | None = None
) -> AsyncGenerator[dict, None]:
    """Run Codex headlessly, yielding {type: text|tool|session|result|error}."""
    cmd = _build_cmd(prompt, cwd, resume_session_id)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,  # exec reads stdin until EOF otherwise
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        yield {"type": "error", "text": f"[Codex CLI not found: {resolve_codex_cli()!r}]"}
        return

    session_emitted = False
    last_text = ""
    saw_event = False

    async for line in process.stdout:
        raw = line.decode(errors="replace").strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue  # e.g. "Reading additional input from stdin..." noise

        saw_event = True
        etype = ev.get("type")

        if etype == "thread.started":
            if not session_emitted and ev.get("thread_id"):
                session_emitted = True
                yield {"type": "session", "id": ev["thread_id"]}

        elif etype == "item.completed":
            item = ev.get("item", {})
            itype = item.get("type")
            if itype == "agent_message":
                text = item.get("text", "")
                last_text = text
                if text:
                    yield {"type": "text", "text": text}
            elif itype in _IGNORED_ITEMS:
                continue
            else:
                yield {"type": "tool", "name": itype or "tool", "input": item}

        elif etype in ("thread.error", "error", "turn.failed"):
            msg = ev.get("message") or ev.get("error") or "Codex error"
            yield {"type": "error", "text": str(msg)}

        elif etype == "turn.completed":
            yield {"type": "result", "text": last_text, "cost": None}

    await process.wait()
    if not saw_event and process.returncode not in (0, None):
        yield {"type": "error", "text": f"[Codex exited {process.returncode} with no events]"}

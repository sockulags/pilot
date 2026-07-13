"""Drive the Claude Code CLI headlessly for the chat's `code` route.

run_codex spawns `claude --print ... --output-format stream-json --verbose` in a
chosen project directory, optionally resuming a prior Claude Code session, and
yields typed events (text / tool / session / result) so the WebSocket layer can
stream the reply, surface tool activity, and persist the session id for
continuation.

CLI discovery: honours an absolute CLAUDE_CLI, then PATH, then the Claude
desktop app's bundled CLI (Windows MSIX de-virtualised location), so it works
out of the box on machines where `claude` isn't on PATH.

Auth note: the bundled desktop CLI does not share the desktop app's login when
launched as a foreign process — it must be authenticated for headless use
(`claude` -> /login, or set ANTHROPIC_API_KEY). A "Not logged in" result is
surfaced as an error event.
"""

import asyncio
import glob
import json
import os
import shutil
import sys
from typing import AsyncGenerator

from config import CLAUDE_CLI, CLAUDE_PERMISSION_MODE

_resolved_cli: str | None = None


def _version_key(path: str) -> tuple:
    # .../claude-code/<version>/claude.exe -> sortable version tuple
    version = os.path.basename(os.path.dirname(path))
    parts = []
    for chunk in version.split("."):
        parts.append(int(chunk) if chunk.isdigit() else 0)
    return tuple(parts)


def _find_bundled_claude() -> str | None:
    """Locate the Claude desktop app's bundled CLI on Windows (MSIX)."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    pattern = os.path.join(
        local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude",
        "claude-code", "*", "claude.exe",
    )
    matches = [p for p in glob.glob(pattern) if os.path.isfile(p)]
    if not matches:
        return None
    matches.sort(key=_version_key, reverse=True)
    return matches[0]


def resolve_claude_cli() -> str:
    """Resolve the claude executable: explicit path -> PATH -> bundled desktop CLI."""
    global _resolved_cli
    if _resolved_cli is not None:
        return _resolved_cli

    cli = CLAUDE_CLI or "claude"
    on_path = shutil.which(cli)
    resolved: str
    if os.path.isabs(cli) and os.path.isfile(cli):
        resolved = cli
    elif on_path:
        resolved = on_path
    else:
        resolved = _find_bundled_claude() or cli
    _resolved_cli = resolved
    return resolved


def _build_cmd(prompt: str, resume_session_id: str | None) -> list[str]:
    cli = resolve_claude_cli()
    args = [
        cli, "--print", prompt,
        "--output-format", "stream-json", "--verbose",
        "--include-partial-messages",
        "--permission-mode", CLAUDE_PERMISSION_MODE,
    ]
    if resume_session_id:
        args += ["--resume", resume_session_id]

    # .exe runs directly; .cmd/.bat (npm wrapper) or a bare name must go via cmd /c.
    if sys.platform == "win32" and not cli.lower().endswith(".exe"):
        return ["cmd", "/c", *args]
    return args


def _extract_text(delta: dict) -> str:
    if delta.get("type") == "text_delta":
        return delta.get("text", "")
    return delta.get("text", "")


async def run_codex(
    prompt: str, cwd: str | None = None, resume_session_id: str | None = None
) -> AsyncGenerator[dict, None]:
    """Run Claude Code headlessly, yielding {type: text|tool|session|result|error}."""
    cmd = _build_cmd(prompt, resume_session_id)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,  # avoid the "no stdin" wait
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        yield {"type": "error", "text": f"[Claude Code CLI not found: {resolve_claude_cli()!r}]"}
        return

    session_emitted = False
    streamed_text = False

    # stdout=PIPE guarantees a StreamReader; assert so the typed-Optional narrows.
    assert process.stdout is not None
    async for line in process.stdout:
        raw = line.decode(errors="replace").strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue  # warnings / non-JSON noise

        if not session_emitted and ev.get("session_id"):
            session_emitted = True
            yield {"type": "session", "id": ev["session_id"]}

        etype = ev.get("type")

        if etype == "stream_event":
            inner = ev.get("event", {})
            if inner.get("type") == "content_block_delta":
                text = _extract_text(inner.get("delta", {}))
                if text:
                    streamed_text = True
                    yield {"type": "text", "text": text}

        elif etype == "content_block_delta":
            text = _extract_text(ev.get("delta", {}))
            if text:
                streamed_text = True
                yield {"type": "text", "text": text}

        elif etype == "assistant":
            for block in ev.get("message", {}).get("content", []):
                btype = block.get("type")
                if btype == "text" and not streamed_text:
                    yield {"type": "text", "text": block.get("text", "")}
                elif btype == "tool_use":
                    yield {"type": "tool", "name": block.get("name", ""), "input": block.get("input", {})}
            streamed_text = False  # next message's deltas start fresh

        elif etype == "result":
            is_error = bool(ev.get("is_error")) or ev.get("subtype") != "success"
            text = ev.get("result", "") or ""
            yield {
                "type": "error" if is_error else "result",
                "text": text,
                "cost": ev.get("total_cost_usd"),
            }
            streamed_text = False

    await process.wait()

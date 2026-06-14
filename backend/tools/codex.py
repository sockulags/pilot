import asyncio
import json
import sys
from typing import AsyncGenerator

from config import CLAUDE_CLI


async def run_codex(prompt: str) -> AsyncGenerator[str, None]:
    # On Windows, npm-installed CLI tools (including the Claude CLI) are .cmd
    # wrappers. asyncio.create_subprocess_exec cannot execute .cmd files
    # directly — they must be invoked through cmd /c.
    if sys.platform == "win32":
        cmd = ["cmd", "/c", CLAUDE_CLI, "--print", prompt, "--output-format", "stream-json"]
    else:
        cmd = [CLAUDE_CLI, "--print", prompt, "--output-format", "stream-json"]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        yield f"[run_codex unavailable: '{CLAUDE_CLI}' not found in PATH]"
        return
    async for line in process.stdout:
        raw = line.decode(errors="replace").strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    yield delta.get("text", "")
        except json.JSONDecodeError:
            yield raw
    await process.wait()

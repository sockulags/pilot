import asyncio
import json
from typing import AsyncGenerator


async def run_codex(prompt: str) -> AsyncGenerator[str, None]:
    cmd = ["claude", "--print", prompt, "--output-format", "stream-json"]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
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

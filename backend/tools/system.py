import subprocess
import os
import asyncio
from typing import AsyncGenerator


async def run_command(cmd: str, cwd: str | None = None) -> AsyncGenerator[str, None]:
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    async for line in process.stdout:
        yield line.decode(errors="replace")
    await process.wait()


def run_command_sync(cmd: str, cwd: str | None = None, timeout: int = 30) -> str:
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )
    output = result.stdout + result.stderr
    return output.strip()


def open_app(name: str) -> str:
    os.startfile(name)
    return f"Opened: {name}"

"""The run_command timeout bounds a slow/hanging command (eval finding).

A pathological command must not block a turn indefinitely. system.run_command
enforces a wall-clock deadline, killing the subprocess and returning a note.
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools import system  # noqa: E402


async def _collect(cmd, timeout):
    out = []
    async for line in system.run_command(cmd, timeout=timeout):
        out.append(line)
    return "".join(out)


def _py(inner: str) -> str:
    """A python one-liner quoted for the shell run_command actually uses
    (PowerShell on Windows: & "exe" -c 'code'; plain quoting on POSIX)."""
    if os.name == "nt":
        # PowerShell: double-quote the code arg (single quotes inside survive).
        return f'& "{sys.executable}" -c "{inner}"'
    return f'"{sys.executable}" -c "{inner}"'


def test_slow_command_is_terminated_at_timeout():
    # A 5s sleep with a 1s budget must return promptly with a timeout note.
    start = time.monotonic()
    out = asyncio.run(_collect(_py("import time; time.sleep(5)"), timeout=1))
    elapsed = time.monotonic() - start
    assert "timed out" in out.lower()
    assert elapsed < 4.0, f"timeout not enforced (took {elapsed:.1f}s)"


def test_fast_command_completes_normally_within_timeout():
    out = asyncio.run(_collect(_py("print('pilot-eval-ok')"), timeout=30))
    assert "pilot-eval-ok" in out
    assert "timed out" not in out.lower()

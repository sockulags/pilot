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


def test_slow_command_is_terminated_at_timeout():
    # A 5s sleep with a 1s budget must return promptly with a timeout note.
    sleeper = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    start = time.monotonic()
    out = asyncio.run(_collect(sleeper, timeout=1))
    elapsed = time.monotonic() - start
    assert "timed out" in out.lower()
    assert elapsed < 4.0, f"timeout not enforced (took {elapsed:.1f}s)"


def test_fast_command_completes_normally_within_timeout():
    printer = f'"{sys.executable}" -c "print(\'pilot-eval-ok\')"'
    out = asyncio.run(_collect(printer, timeout=30))
    assert "pilot-eval-ok" in out
    assert "timed out" not in out.lower()

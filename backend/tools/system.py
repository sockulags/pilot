import subprocess
import os
import asyncio
from typing import AsyncGenerator


def _terminate_tree(process) -> None:
    """Kill a subprocess AND its children.

    ``create_subprocess_shell`` spawns ``cmd.exe /c <cmd>`` on Windows, so the
    real command runs as a grandchild; ``process.kill()`` would only kill the
    shell and leave the grandchild holding the stdout pipe open (defeating a
    timeout). ``taskkill /T`` terminates the whole tree; POSIX uses kill().
    """
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=10,
            )
        else:
            process.kill()
    except Exception:  # noqa: BLE001 — best-effort cleanup, never raise
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass


async def run_command(
    cmd: str, cwd: str | None = None, timeout: float | None = None
) -> AsyncGenerator[str, None]:
    """Stream a shell command's output, bounded by ``timeout`` seconds.

    On timeout the subprocess is killed and a timeout note is yielded, so a
    hanging or pathologically slow command can never block a turn indefinitely.
    ``timeout=None`` means unbounded (the historical behaviour).
    """
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    # Enforce the deadline with a watchdog that KILLS the process, rather than
    # cancelling the read: on Windows a pending Proactor pipe read cannot be
    # cancelled, so wait_for(readline) would block until natural EOF. Killing the
    # process forces EOF, which ends the read loop promptly on every platform.
    timed_out = False
    watchdog = None
    if timeout is not None:
        async def _kill_after() -> None:
            nonlocal timed_out
            try:
                await asyncio.sleep(timeout)
            except asyncio.CancelledError:
                return
            if process.returncode is None:
                timed_out = True
                _terminate_tree(process)

        watchdog = asyncio.ensure_future(_kill_after())
    try:
        async for line in process.stdout:
            yield line.decode(errors="replace")
        if timed_out:
            yield f"\n[command timed out after {timeout:g}s and was terminated]\n"
    finally:
        if watchdog is not None:
            watchdog.cancel()
        # Reap first. On normal completion the process has already exited (and on
        # the timeout path the watchdog already tree-killed it), so wait() returns
        # promptly and we must NOT spawn a taskkill against a PID Windows may have
        # recycled. Only force a tree-kill if the process is genuinely still alive
        # (e.g. the consumer closed the generator early, or a child outlived the
        # shell) — detected by wait() not completing within a short grace.
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            _terminate_tree(process)
            try:
                await process.wait()
            except ProcessLookupError:
                pass


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


WINDOWS_APP_ALIASES = {
    "calculator": "calc",
    "calc": "calc",
    "kalkylator": "calc",
}


def open_app(name: str) -> str:
    launch_name = name.strip()
    if os.name == "nt":
        alias = WINDOWS_APP_ALIASES.get(launch_name.lower())
        if alias:
            subprocess.Popen(alias, shell=True)
            return f"Opened: {name}"
    os.startfile(launch_name)
    return f"Opened: {name}"

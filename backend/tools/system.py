import subprocess
import os
import asyncio
import re
from typing import AsyncGenerator


def shell_name() -> str:
    """The shell run_command executes in — stated in tool output so a small
    model never has to guess which command dialect applies."""
    return "PowerShell" if os.name == "nt" else "sh"


# Common shell-confusion failure signatures -> one actionable hint each. The
# point is that the TOOL teaches the model the environment (eval 2026-07-02:
# models emitted cmd syntax into PowerShell, got an opaque error, then re-ran
# the same command instead of adapting).
_HINT_RULES: tuple[tuple[str, str], ...] = (
    (
        r"was unexpected at this time",
        "this shell is PowerShell, not cmd.exe — cmd-style piping like "
        "'dir | find /c' does not work. To count files: (Get-ChildItem *.py).Count",
    ),
    (
        r"is not recognized as (an internal or external command|the name of a cmdlet)",
        "the command was not found in PowerShell. Use PowerShell equivalents: "
        "ls/dir -> Get-ChildItem, cat/type -> Get-Content, find/grep -> Select-String, "
        "wc -l -> Measure-Object -Line",
    ),
    (
        r"CommandNotFoundException",
        "the command was not found in PowerShell. Use PowerShell equivalents: "
        "ls/dir -> Get-ChildItem, cat/type -> Get-Content, find/grep -> Select-String",
    ),
    (
        r"Missing expression after unary operator|ParserError",
        "PowerShell could not parse the command — check quoting; single-quote "
        "literals ('like this') and avoid cmd.exe-only syntax (/c, %VAR%)",
    ),
    (
        r"cannot find path .* because it does not exist",
        "the path does not exist in the working directory shown above — list it "
        "first with Get-ChildItem to see what is actually there",
    ),
    (
        # Observed live: 'find' resolved to Git's Unix find on PATH and walked the
        # whole drive for ~50s. Neither cmd's find.exe nor Unix find is wanted here.
        r"/usr/bin/find|find: .*Permission denied",
        "'find' resolves to Unix find (from Git) and scans the whole drive — to "
        "count files use (Get-ChildItem *.py).Count, to search text use Select-String",
    ),
)


def command_hint(output: str) -> str:
    """Return one actionable hint for a failed/confused command, or ''.

    Deterministic and cheap: matched against the command output so the next
    decision step learns what went wrong and what to try instead of repeating
    the same failing command.
    """
    for pattern, hint in _HINT_RULES:
        if re.search(pattern, output or "", re.IGNORECASE):
            return f"Hint: {hint}."
    return ""


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
    cmd: str, cwd: str | None = None, timeout: float | None = None,
    status: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Stream a shell command's output, bounded by ``timeout`` seconds.

    On Windows the command runs EXPLICITLY in PowerShell (create_subprocess_shell
    would hand it to cmd.exe): the tool descriptions, safety examples and the
    models' own habits are all PowerShell-flavoured, and an ambiguous shell is
    exactly what made models emit cmd/PowerShell hybrids that failed opaquely
    (eval 2026-07-02). POSIX keeps the default shell.

    On timeout the subprocess tree is killed and a timeout note is yielded, so a
    hanging or pathologically slow command can never block a turn indefinitely.
    ``timeout=None`` means unbounded (the historical behaviour).

    ``status`` (if given) is filled with ``{"returncode", "timed_out"}`` so the
    caller can tell success from failure (a corrective hint must only follow a
    FAILED command, not a successful one whose output merely contains a trigger
    phrase — adversarial review 2026-07-03).
    """
    if os.name == "nt":
        process = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive", "-Command", cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
    else:
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
    # stdout=PIPE guarantees a StreamReader; assert so the typed-Optional narrows.
    assert process.stdout is not None
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
        if status is not None:
            status["returncode"] = process.returncode
            status["timed_out"] = timed_out


def run_command_sync(cmd: str, cwd: str | None = None, timeout: int = 30) -> str:
    # Same shell as the async path (PowerShell on Windows) so the MCP surface
    # (pilot_run_command) and the agent loop never execute the same command under
    # two incompatible shells (adversarial review 2026-07-03).
    #
    # Decode as UTF-8 with errors="replace" (NOT the locale codec): PowerShell's
    # redirected-pipe output can carry OEM code-page bytes (cp850/437) that are
    # undefined under the default cp1252 strict decoder, which would raise
    # UnicodeDecodeError straight out of subprocess.run and crash /mcp/call with
    # a 500. We also ask the child to emit UTF-8 so both run_command surfaces
    # agree (review 2026-07-04).
    if os.name == "nt":
        prelude = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; "
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", prelude + cmd]
        result = subprocess.run(
            argv, capture_output=True, cwd=cwd, timeout=timeout,
        )
    else:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, cwd=cwd, timeout=timeout,
        )
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    return (stdout + stderr).strip()


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

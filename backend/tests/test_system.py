"""Direct tests for tools.system.run_command_sync — the synchronous command
runner behind the MCP surface (pilot_run_command).

Every other suite that touches this function replaces it with a mock at the MCP
boundary (test_mcp_auth, test_mcp_dispatch), so none of its own logic was ever
executed: the PowerShell invocation shape, the UTF-8 console-encoding prelude,
the byte-decode-with-replace, and the cwd/timeout pass-through. The decode is
worth pinning in particular — its comment in system.py records that strict
locale decoding of PowerShell's OEM code-page pipe output once crashed
/mcp/call with a 500 (review 2026-07-04), and that fix had no regression test.

The two tests that need a real PowerShell are Windows-only (CI's backend job
runs on windows-latest, so they do execute there). The decode and pass-through
tests patch subprocess.run and run on any platform.
"""

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools import system  # noqa: E402

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="exercises the Windows/PowerShell branch"
)


def _completed(stdout: bytes = b"", stderr: bytes = b"") -> mock.Mock:
    """A stand-in for CompletedProcess whose streams are real bytes, since the
    function under test calls .decode() on them."""
    return mock.Mock(stdout=stdout, stderr=stderr)


@windows_only
def test_echo_returns_trimmed_stdout_from_a_real_subprocess():
    assert system.run_command_sync("echo hello") == "hello"


@windows_only
def test_stdout_and_stderr_are_both_returned_in_one_string():
    # Write-Error runs FIRST here, yet its record still lands after the stdout
    # text: the function concatenates the two captured streams (stdout then
    # stderr) rather than interleaving them chronologically.
    out = system.run_command_sync("Write-Error ERRTEXT; Write-Output OUTTEXT")
    assert "OUTTEXT" in out
    assert "ERRTEXT" in out
    assert out.index("OUTTEXT") < out.index("ERRTEXT")
    # Substrings only, never equality: PowerShell's error record echoes the
    # whole command line back, including the UTF-8 prelude the function adds.


def test_invalid_utf8_output_is_replaced_instead_of_raising():
    # A lone 0x81 is undefined in UTF-8 (and in cp1252) — the exact shape of
    # byte that made a strict decode raise UnicodeDecodeError out of
    # subprocess.run and 500 the /mcp/call endpoint.
    with mock.patch.object(system.subprocess, "run") as run:
        run.return_value = _completed(stdout=b"before\x81after", stderr=b"err\xffend")
        out = system.run_command_sync("anything")

    assert isinstance(out, str)
    assert "before" in out and "after" in out
    assert "err" in out and "end" in out
    assert "�" in out  # errors="replace" produced the substitution


def test_cwd_and_timeout_are_passed_through_to_subprocess_run(tmp_path):
    with mock.patch.object(system.subprocess, "run") as run:
        run.return_value = _completed(stdout=b"ok")
        assert system.run_command_sync("anything", cwd=str(tmp_path), timeout=7) == "ok"

    run.assert_called_once()
    assert run.call_args.kwargs["cwd"] == str(tmp_path)
    assert run.call_args.kwargs["timeout"] == 7
    assert run.call_args.kwargs["capture_output"] is True


def test_default_timeout_is_thirty_seconds():
    with mock.patch.object(system.subprocess, "run") as run:
        run.return_value = _completed(stdout=b"ok")
        system.run_command_sync("anything")

    assert run.call_args.kwargs["timeout"] == 30


@windows_only
def test_windows_branch_runs_powershell_with_the_utf8_output_prelude():
    with mock.patch.object(system.subprocess, "run") as run:
        run.return_value = _completed(stdout=b"ok")
        system.run_command_sync("Get-ChildItem")

    # On the Windows branch argv is the first positional argument, and the
    # command is passed as an explicit argv list — not handed to a bare shell.
    argv = run.call_args.args[0]
    assert argv[0] == "powershell"
    assert "-NoProfile" in argv and "-NonInteractive" in argv and "-Command" in argv
    assert "shell" not in run.call_args.kwargs

    command = argv[-1]
    assert command.endswith("Get-ChildItem")
    assert "[Console]::OutputEncoding=[Text.Encoding]::UTF8" in command
    assert "$OutputEncoding=[Text.Encoding]::UTF8" in command
    # The prelude must precede the caller's command, not trail it.
    assert command.index("[Console]::OutputEncoding") < command.index("Get-ChildItem")

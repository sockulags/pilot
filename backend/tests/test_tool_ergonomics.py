"""Tests for tool ergonomics: tools that teach the model their environment.

A1: run_command is explicitly PowerShell on Windows, says so in its output, and
returns one actionable hint on the shell-confusion failures observed live.
A2/A3: web_research explains WHY retrieval failed and what to try, falls back to
the lite search endpoint, and retries once with a simplified query. No network —
search/fetch are faked.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools import system  # noqa: E402
from tools.web import (  # noqa: E402
    WebSearchResult,
    simplified_query,
    web_research_result,
)


# --------------------------------------------------------------------------- #
# A1 — shell clarity + hints
# --------------------------------------------------------------------------- #


def test_shell_name_matches_platform():
    assert system.shell_name() == ("PowerShell" if os.name == "nt" else "sh")


def test_hint_for_cmd_syntax_in_powershell():
    hint = system.command_hint("'.Count' was unexpected at this time")
    assert "PowerShell" in hint and "Get-ChildItem" in hint


def test_hint_for_unknown_command():
    hint = system.command_hint(
        "'wc' is not recognized as the name of a cmdlet, function, script file"
    )
    assert "Measure-Object" in hint or "Get-Content" in hint


def test_hint_for_unix_find_walking_the_drive():
    hint = system.command_hint(
        "/usr/bin/find: '/c/$Recycle.Bin/S-1-5-18': Permission denied"
    )
    assert "Unix find" in hint and "Get-ChildItem" in hint


def test_no_hint_for_clean_output():
    assert system.command_hint("3") == ""
    assert system.command_hint("") == ""


def test_run_command_executes_powershell_syntax():
    if os.name != "nt":
        return  # PowerShell semantics are Windows-specific
    async def go():
        out = ""
        async for line in system.run_command("(1..3 | Measure-Object).Count", timeout=30):
            out += line
        return out
    assert "3" in asyncio.run(go())


def test_loop_result_header_states_shell(tmp_path):
    from agents import loop as agent_loop

    async def go():
        return await agent_loop.execute_tool(
            "run_command", {"cmd": "echo pilot-eval", "cwd": str(tmp_path)}, lambda e: None
        )
    result = asyncio.run(go())
    assert f"Shell: {system.shell_name()}" in result
    assert "pilot-eval" in result


def test_loop_appends_hint_only_on_failed_command(tmp_path, monkeypatch):
    from agents import loop as agent_loop

    def fake_run(exit_code):
        async def _run(cmd, cwd=None, status=None):
            if status is not None:
                status["returncode"] = exit_code
                status["timed_out"] = False
            yield "'.Count' was unexpected at this time\n"
        return _run

    async def go():
        return await agent_loop.execute_tool(
            "run_command", {"cmd": "x", "cwd": str(tmp_path)}, lambda e: None
        )

    # Non-zero exit -> the corrective hint is attached.
    monkeypatch.setattr(agent_loop, "run_command_async", fake_run(1))
    failed = asyncio.run(go())
    assert "Hint:" in failed and "PowerShell" in failed

    # Exit 0 -> NO hint, even though the output contains a trigger phrase
    # (adversarial review 2026-07-03: a successful command must not be mis-hinted).
    monkeypatch.setattr(agent_loop, "run_command_async", fake_run(0))
    ok = asyncio.run(go())
    assert "Hint:" not in ok


# --------------------------------------------------------------------------- #
# A2/A3 — web_research explains itself and retries smarter
# --------------------------------------------------------------------------- #


def _result(url, title="T"):
    return WebSearchResult(title, url)


def _fake_fetch(pages: dict):
    async def fetch(url, max_chars=5000):
        return pages.get(url, f"fetch_url failed: HTTPStatusError: 403 for {url}")
    return fetch


def test_simplified_query_drops_filler_and_caps_words():
    q = simplified_query("aktuell bästa lokala LLM för 16 GB VRAM GPU 2026")
    assert "aktuell" not in q.lower() and "2026" not in q
    assert len(q.split()) <= 6
    # Already-minimal queries have no useful simplification.
    assert simplified_query("python") == ""


def test_retry_with_simplified_query_recovers_sources():
    calls = []

    async def fake_search(q, n):
        calls.append(q)
        if len(calls) == 1:
            return []  # primary query: nothing
        return [_result("https://ok.example/a"), _result("https://ok.example/b")]

    fetch = _fake_fetch({
        "https://ok.example/a": "readable A",
        "https://ok.example/b": "readable B",
    })
    r = asyncio.run(web_research_result(
        "aktuell bästa lokala LLM för 16GB",
        min_sources=2, search_results=fake_search, fetch=fetch,
    ))
    assert r.ok
    assert r.data["sources_fetched"] == 2
    assert len(calls) == 2, "must retry once with the simplified query"
    assert "retried with simplified query" in r.text


def test_zero_search_results_explains_and_suggests():
    # A query with no useful simplification -> only one attempt -> "do not repeat".
    async def fake_search(q, n):
        return []
    r = asyncio.run(web_research_result(
        "python", min_sources=2, search_results=fake_search, fetch=_fake_fetch({}),
    ))
    assert not r.ok
    assert "Sources fetched: 0" in r.error
    assert "Why:" in r.error and "Try:" in r.error
    assert "Do not repeat the same query" in r.error


def test_zero_results_after_retry_advises_different_query():
    # Both primary and simplified retry found nothing -> advise a DIFFERENT query,
    # not the one already tried (adversarial review 2026-07-03).
    async def fake_search(q, n):
        return []
    r = asyncio.run(web_research_result(
        "aktuell bästa lokala LLM 2026", min_sources=2,
        search_results=fake_search, fetch=_fake_fetch({}),
    ))
    assert not r.ok
    assert "already attempted" in r.error
    assert "a DIFFERENT" in r.error


def test_all_fetches_failed_explains_blocking():
    async def fake_search(q, n):
        return [_result("https://blocked.example/x"), _result("https://blocked.example/y")]
    r = asyncio.run(web_research_result(
        "python news", min_sources=2, search_results=fake_search, fetch=_fake_fetch({}),
    ))
    assert not r.ok
    assert "every page fetch failed" in r.error
    assert "fetch_url" in r.error  # concrete alternative offered
    assert "Do not repeat the same web_research call" in r.error


def test_search_transport_failure_explains():
    async def fake_search(q, n):
        raise RuntimeError("web_search failed on both endpoints (lite: ConnectError: x)")
    r = asyncio.run(web_research_result(
        "python news", min_sources=2, search_results=fake_search, fetch=_fake_fetch({}),
    ))
    assert not r.ok
    assert "search request itself failed" in r.error


def test_duplicate_urls_not_fetched_twice():
    fetch_calls = []

    async def fake_search(q, n):
        return [_result("https://ok.example/a")]  # same candidate on both attempts

    async def fetch(url, max_chars=5000):
        fetch_calls.append(url)
        return "fetch_url failed: 403"

    r = asyncio.run(web_research_result(
        "aktuell bästa lokala LLM", min_sources=2, search_results=fake_search, fetch=fetch,
    ))
    assert not r.ok
    assert fetch_calls.count("https://ok.example/a") == 1


def test_happy_path_unchanged_single_attempt():
    calls = []

    async def fake_search(q, n):
        calls.append(q)
        return [_result("https://a.example"), _result("https://b.example"), _result("https://c.example")]

    fetch = _fake_fetch({
        "https://a.example": "A", "https://b.example": "B", "https://c.example": "C",
    })
    r = asyncio.run(web_research_result(
        "Volvo Cars news", min_sources=3, search_results=fake_search, fetch=fetch,
    ))
    assert r.ok and r.data["sources_fetched"] == 3
    assert len(calls) == 1, "no retry when the first attempt satisfies min_sources"
    assert "Sources fetched: 3" in r.text

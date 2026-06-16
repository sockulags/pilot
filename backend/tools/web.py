"""Lightweight web access: search + fetch.

No API key and no extra dependencies (httpx only) so it works out of the box —
the gap behind "what's the weather Wednesday?" / "look this up" (sessions
b0b0c177, e251136a). web_search uses DuckDuckGo's HTML endpoint; fetch_url
pulls a page and reduces it to readable text.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote
from typing import Awaitable, Callable

import httpx
from tool_results import ToolResult

# A realistic browser UA — DuckDuckGo serves an empty/challenge page to
# obviously-bot user agents, which silently yields zero results.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.IGNORECASE | re.DOTALL
)


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str = ""


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"(?s)<[^>]+>", "", fragment)
    return html.unescape(text).strip()


def _clean_ddg_href(href: str) -> str:
    # DDG wraps results as //duckduckgo.com/l/?uddg=<encoded-real-url>
    match = re.search(r"[?&]uddg=([^&]+)", href)
    if match:
        return unquote(match.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def is_blocked_result_url(url: str) -> bool:
    """Return True for ads, click trackers and search-engine redirect wrappers."""
    lowered = html.unescape(url).lower()
    blocked_markers = (
        "duckduckgo.com/y.js",
        "ad_provider=",
        "ad_domain=",
        "ad_type=",
        "bing.com/aclick",
        "googleadservices.com",
        "/aclk?",
        "utm_ad",
    )
    return any(marker in lowered for marker in blocked_markers)


def infer_web_query(task: str) -> str:
    """Derive a concrete search query from a user task when the model omitted one."""
    text = re.sub(r"\s+", " ", (task or "").strip()).strip(" .")
    if not text:
        return ""

    patterns = (
        r"(?i)^sök\s+(?:på\s+webben|webben|online)?\s*(?:efter|om)?\s+",
        r"(?i)^search\s+(?:the\s+web\s+)?(?:for\s+)?",
        r"(?i)^look\s+up\s+",
        r"(?i)^kolla\s+upp\s+",
    )
    query = text
    for pattern in patterns:
        query = re.sub(pattern, "", query).strip()

    # Remove output-format clauses, not the actual subject.
    splitters = (
        r"\s+och\s+sammanfatta\b",
        r"\s+och\s+ge\s+mig\b",
        r"\s+med\s+länkar\b",
        r"\s+with\s+links\b",
        r"\s+and\s+summari[sz]e\b",
    )
    for splitter in splitters:
        query = re.split(splitter, query, maxsplit=1, flags=re.IGNORECASE)[0].strip()

    return query.strip(" .") or text


def task_requires_sources(task: str) -> bool:
    lowered = (task or "").lower()
    return any(
        token in lowered
        for token in (
            "källa",
            "källor",
            "länk",
            "länkar",
            "source",
            "sources",
            "links",
            "sammanfatta tre",
            "three sources",
        )
    )


def infer_requested_source_count(task: str, default: int = 3) -> int:
    lowered = (task or "").lower()
    patterns = (
        r"\b(\d+)\s+(?:källor|kallor|sources?|links?|länkar|lankar)\b",
        r"\b(?:sammanfatta|compare|jämför|jamfor)\s+(\d+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return max(1, int(match.group(1)))

    word_counts = {
        "en": 1,
        "ett": 1,
        "one": 1,
        "två": 2,
        "tva": 2,
        "two": 2,
        "tre": 3,
        "three": 3,
        "fyra": 4,
        "four": 4,
        "fem": 5,
        "five": 5,
    }
    for word, count in word_counts.items():
        if re.search(rf"\b{re.escape(word)}\s+(?:källor|kallor|sources?|links?|länkar|lankar)\b", lowered):
            return count

    return default


def _parse_search_output(output: str) -> list[WebSearchResult]:
    """Parse web_search's text output into title/url/snippet tuples."""
    results: list[WebSearchResult] = []
    current_title = ""
    current_url = ""
    snippet_parts: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_url, snippet_parts
        if current_title and current_url and not is_blocked_result_url(current_url):
            results.append(WebSearchResult(current_title, current_url, " ".join(snippet_parts).strip()))
        current_title = ""
        current_url = ""
        snippet_parts = []

    for raw in output.splitlines():
        line = raw.strip()
        match = re.match(r"^\d+\.\s+(.*)$", line)
        if match:
            flush()
            current_title = match.group(1).strip()
            continue
        if line.startswith(("http://", "https://")):
            current_url = html.unescape(line)
            continue
        if current_title and current_url and line:
            snippet_parts.append(line)
    flush()
    return results


def parse_search_results_html(body: str, max_results: int = 5) -> list[WebSearchResult]:
    """Parse DuckDuckGo HTML into structured, filtered results."""
    matches = list(_RESULT_RE.finditer(body))
    snippets = [_strip_tags(m.group("snippet")) for m in _SNIPPET_RE.finditer(body)]

    results: list[WebSearchResult] = []
    for i, m in enumerate(matches):
        url = _clean_ddg_href(m.group("href"))
        if is_blocked_result_url(url):
            continue
        snippet = snippets[i] if i < len(snippets) else ""
        results.append(WebSearchResult(_strip_tags(m.group("title")), url, snippet))
        if len(results) >= max_results:
            break
    return results


async def search_web_results(query: str, max_results: int = 5) -> list[WebSearchResult]:
    """Search the web and return structured results, not presentation text."""
    try:
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/", data={"q": query}
            )
            resp.raise_for_status()
            body = resp.text
    except Exception as exc:
        raise RuntimeError(f"web_search failed: {type(exc).__name__}: {exc}") from exc

    return parse_search_results_html(body, max_results)


def format_web_search_results(query: str, results: list[WebSearchResult]) -> str:
    if not results:
        return f"No web results for {query!r}."

    lines = [f"Top results for {query!r}:"]
    for n, result in enumerate(results, 1):
        lines.append(f"{n}. {result.title}\n   {result.url}")
        if result.snippet:
            lines.append(f"   {result.snippet[:240]}")
    return "\n".join(lines)


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return the top results as title — url — snippet."""
    try:
        return format_web_search_results(query, await search_web_results(query, max_results))
    except RuntimeError as exc:
        return str(exc)


async def web_research_result(
    query: str,
    task: str = "",
    min_sources: int = 3,
    max_results: int = 8,
    search_results: Callable[[str, int], Awaitable[list[WebSearchResult]]] = search_web_results,
    search: Callable[[str, int], Awaitable[str]] | None = None,
    fetch: Callable[[str, int], Awaitable[str]] | None = None,
) -> ToolResult:
    """Search, filter and fetch sources as a structured tool result."""
    final_query = (query or "").strip() or infer_web_query(task)
    if not final_query:
        error = "web_research requires a search query and could not infer one from the task."
        return ToolResult(False, "web_research", error=error, data={"query": final_query})

    fetcher = fetch or fetch_url
    try:
        candidates = _parse_search_output(await search(final_query, max_results)) if search else await search_results(final_query, max_results)
    except RuntimeError as exc:
        return ToolResult(False, "web_research", error=str(exc), data={"query": final_query})

    fetched: list[tuple[WebSearchResult, str]] = []
    failures: list[str] = []

    for result in candidates:
        if len(fetched) >= min_sources:
            break
        page = await fetcher(result.url, 5000)
        if page.startswith("fetch_url failed:"):
            failures.append(f"{result.url} -> {page}")
            continue
        fetched.append((result, page))

    lines = [f"Research results for {final_query!r}:", f"Sources fetched: {len(fetched)}"]
    if not fetched:
        lines.append("No readable sources could be fetched.")
    sources: list[dict[str, str]] = []
    for i, (result, page) in enumerate(fetched, 1):
        excerpt = re.sub(r"\s+", " ", page).strip()
        sources.append({"title": result.title, "url": result.url})
        lines.append(f"{i}. {result.title}\n   {result.url}\n   {excerpt[:900]}")
    if len(fetched) < min_sources:
        lines.append(f"Only {len(fetched)} readable source(s) were available from the search results.")
    if failures:
        lines.append("Fetch failures:\n" + "\n".join(failures[:5]))
    text = "\n".join(lines)
    ok = bool(fetched)
    return ToolResult(
        ok=ok,
        kind="web_research",
        text=text if ok else "",
        error=None if ok else text,
        data={"query": final_query, "sources_fetched": len(fetched), "failures": failures},
        sources=sources,
    )


async def web_research(
    query: str,
    task: str = "",
    min_sources: int = 3,
    max_results: int = 8,
    search_results: Callable[[str, int], Awaitable[list[WebSearchResult]]] = search_web_results,
    search: Callable[[str, int], Awaitable[str]] | None = None,
    fetch: Callable[[str, int], Awaitable[str]] | None = None,
) -> str:
    """Search, filter and fetch sources for a web-backed answer."""
    result = await web_research_result(
        query,
        task=task,
        min_sources=min_sources,
        max_results=max_results,
        search_results=search_results,
        search=search,
        fetch=fetch,
    )
    return result.to_text()


async def fetch_url(url: str, max_chars: int = 4000) -> str:
    """Fetch a URL and return its readable text content (HTML reduced to text)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            text = resp.text
    except Exception as exc:
        return f"fetch_url failed: {type(exc).__name__}: {exc}"

    if "html" in content_type or text.lstrip().startswith("<"):
        text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    suffix = "" if len(text) <= max_chars else " …[truncated]"
    return f"{url}\n\n{text[:max_chars]}{suffix}"

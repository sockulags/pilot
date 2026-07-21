"""Lightweight web access: search + fetch.

Zero-config by default (httpx only, no API key) so it works out of the box —
the gap behind "what's the weather Wednesday?" / "look this up" (sessions
b0b0c177, e251136a). web_search scrapes DuckDuckGo's HTML endpoint; fetch_url
pulls a page and reduces it to readable text.

Web retrieval is the eval's admitted weakest link (README): DuckDuckGo scraping
is brittle and returned zero sources for the research-to-file task. So the
search layer is now PLUGGABLE — set PILOT_SEARCH_PROVIDER + PILOT_SEARCH_API_KEY
to route through a resilient JSON search API (Tavily/Brave-style) instead. With
no key configured we fall back to the DuckDuckGo scraper, keeping the local-first
path intact. Network calls retry with a short back-off.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from dataclasses import dataclass
from urllib.parse import unquote
from typing import Awaitable, Callable, TypeVar

import httpx
from net_guard import aguard_request, pinned_async_transport
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


# --------------------------------------------------------------------------- #
# Retry / back-off around network calls
# --------------------------------------------------------------------------- #
# A couple of short attempts smooth over transient blocks/timeouts without
# stalling a turn. Read at call time (not import) so tests can shrink the delay.
_RETRY_ATTEMPTS = max(1, int(os.getenv("PILOT_HTTP_RETRIES", "2")))
_RETRY_BASE_DELAY = float(os.getenv("PILOT_HTTP_RETRY_DELAY", "0.5"))

_T = TypeVar("_T")


async def _with_retry(
    op: Callable[[], Awaitable[_T]],
    *,
    attempts: int | None = None,
    base_delay: float | None = None,
) -> _T:
    """Run an async network op, retrying transient failures with linear back-off.

    Retries on transport errors and 429/5xx responses; a 4xx (other than 429) is
    a client error and re-raised immediately. The last exception propagates.
    """
    n = attempts if attempts is not None else _RETRY_ATTEMPTS
    delay = base_delay if base_delay is not None else _RETRY_BASE_DELAY
    last: Exception | None = None
    for i in range(n):
        try:
            return await op()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise
            last = exc
        except httpx.HTTPError as exc:
            last = exc
        if i < n - 1 and delay > 0:
            await asyncio.sleep(delay * (i + 1))
    assert last is not None  # loop ran at least once and did not return
    raise last


# --------------------------------------------------------------------------- #
# Pluggable search provider
# --------------------------------------------------------------------------- #
def _search_provider() -> str:
    """Configured provider id, read at call time. Empty/unknown -> duckduckgo."""
    return (os.getenv("PILOT_SEARCH_PROVIDER") or "duckduckgo").strip().lower()


def _search_api_key() -> str:
    return (os.getenv("PILOT_SEARCH_API_KEY") or "").strip()


# Default endpoints per JSON provider; PILOT_SEARCH_BASE_URL overrides either.
_PROVIDER_ENDPOINTS = {
    "tavily": "https://api.tavily.com/search",
    "brave": "https://api.search.brave.com/res/v1/web/search",
}


def _use_json_provider(provider: str) -> bool:
    """Route through the JSON API when a non-DDG provider has a key and endpoint.

    Requires a key. A known provider (tavily/brave) supplies its own endpoint;
    any other name works too as long as PILOT_SEARCH_BASE_URL points at a
    Tavily-shaped API — so self-hosted/compatible services need no code change.
    """
    if provider == "duckduckgo" or not _search_api_key():
        return False
    return provider in _PROVIDER_ENDPOINTS or bool((os.getenv("PILOT_SEARCH_BASE_URL") or "").strip())


def _parse_tavily_results(payload: dict, max_results: int) -> list[WebSearchResult]:
    results: list[WebSearchResult] = []
    for item in (payload or {}).get("results", []) or []:
        url = (item.get("url") or "").strip()
        if not url or is_blocked_result_url(url):
            continue
        results.append(
            WebSearchResult(
                title=(item.get("title") or url).strip(),
                url=url,
                snippet=(item.get("content") or item.get("snippet") or "").strip(),
            )
        )
        if len(results) >= max_results:
            break
    return results


def _parse_brave_results(payload: dict, max_results: int) -> list[WebSearchResult]:
    results: list[WebSearchResult] = []
    web = ((payload or {}).get("web") or {}).get("results", []) or []
    for item in web:
        url = (item.get("url") or "").strip()
        if not url or is_blocked_result_url(url):
            continue
        results.append(
            WebSearchResult(
                title=_strip_tags(item.get("title") or url),
                url=url,
                snippet=_strip_tags(item.get("description") or ""),
            )
        )
        if len(results) >= max_results:
            break
    return results


async def _search_json_api(
    provider: str, query: str, max_results: int
) -> list[WebSearchResult]:
    """Query a resilient JSON search API (Tavily/Brave-style) behind one interface.

    Requires PILOT_SEARCH_API_KEY. Tavily takes a JSON POST with the key in the
    body; Brave takes a GET with the key in an ``X-Subscription-Token`` header.
    Both return JSON, which is far less brittle than scraping HTML.
    """
    api_key = _search_api_key()
    base = (os.getenv("PILOT_SEARCH_BASE_URL") or _PROVIDER_ENDPOINTS.get(provider, "")).strip()
    if not base:
        raise RuntimeError(f"no search endpoint configured for provider {provider!r}")

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        if provider == "brave":
            async def _call() -> httpx.Response:
                resp = await client.get(
                    base,
                    params={"q": query, "count": max_results},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": api_key,
                    },
                )
                resp.raise_for_status()
                return resp

            resp = await _with_retry(_call)
            return _parse_brave_results(resp.json(), max_results)

        # Tavily-style: key in the JSON body (the common default provider).
        async def _call() -> httpx.Response:
            resp = await client.post(
                base,
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                },
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            )
            resp.raise_for_status()
            return resp

        resp = await _with_retry(_call)
        return _parse_tavily_results(resp.json(), max_results)


async def search_web_results(query: str, max_results: int = 5) -> list[WebSearchResult]:
    """Search the web and return structured results, not presentation text.

    When PILOT_SEARCH_PROVIDER names a JSON API (e.g. "tavily"/"brave") AND
    PILOT_SEARCH_API_KEY is set, route through it — resilient JSON beats scraping.
    Otherwise (the zero-config default) scrape DuckDuckGo's HTML endpoint, falling
    back to the lite endpoint when it yields nothing (challenge page, transient
    block). Retrieval was the eval's weakest measured link (2026-07-02:
    research-to-file failed on BOTH backends because zero sources came back), so
    the search layer must neither depend on one endpoint nor on scraping at all.
    """
    provider = _search_provider()
    if _use_json_provider(provider):
        try:
            results = await _search_json_api(provider, query, max_results)
            if results:
                return results
        except Exception as exc:  # noqa: BLE001 — degrade to the scraper below
            # A configured API that fails must not brick search: fall through to
            # DuckDuckGo so the tool still returns something.
            _log_provider_fallback(provider, exc)

    primary_error: Exception | None = None
    try:
        async def _call() -> httpx.Response:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True, headers={"User-Agent": _UA}
            ) as client:
                resp = await client.post(
                    "https://html.duckduckgo.com/html/", data={"q": query}
                )
                resp.raise_for_status()
                return resp

        resp = await _with_retry(_call)
        results = parse_search_results_html(resp.text, max_results)
        if results:
            return results
    except Exception as exc:  # noqa: BLE001 — fall through to the lite endpoint
        primary_error = exc

    try:
        return await _search_lite(query, max_results)
    except Exception as exc:
        first = f"{type(primary_error).__name__}: {primary_error}; " if primary_error else ""
        raise RuntimeError(
            f"web_search failed on both endpoints ({first}lite: {type(exc).__name__}: {exc})"
        ) from exc


def _log_provider_fallback(provider: str, exc: Exception) -> None:
    import logging

    logging.getLogger(__name__).warning(
        "search provider %r failed (%s: %s); falling back to DuckDuckGo",
        provider,
        type(exc).__name__,
        exc,
    )


_LITE_RESULT_RE = re.compile(
    r'<a[^>]+rel="nofollow"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


async def _search_lite(query: str, max_results: int = 5) -> list[WebSearchResult]:
    """Fallback search via DuckDuckGo's lite endpoint (simple HTML, rarely blocked)."""
    async def _call() -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.post("https://lite.duckduckgo.com/lite/", data={"q": query})
            resp.raise_for_status()
            return resp

    resp = await _with_retry(_call)
    body = resp.text
    results: list[WebSearchResult] = []
    for m in _LITE_RESULT_RE.finditer(body):
        url = _clean_ddg_href(m.group("href"))
        if is_blocked_result_url(url) or not url.startswith(("http://", "https://")):
            continue
        results.append(WebSearchResult(_strip_tags(m.group("title")), url))
        if len(results) >= max_results:
            break
    return results


# Over-constraining filler words dropped when a search needs a second, simpler
# attempt. Deterministic — no model call inside a tool.
_QUERY_FILLER = {
    "aktuell", "aktuella", "aktuellt", "senaste", "current", "latest", "recent",
    "bäst", "bästa", "best", "bra", "rekommenderad", "recommended",
    "idag", "today", "nu", "now", "2025", "2026",
}


def simplified_query(query: str) -> str:
    """A simpler variant of a failing query: drop filler/qualifier words and
    quotes, cap the length. Returns "" when no meaningful simplification exists."""
    words = [w for w in re.split(r"\s+", (query or "").replace('"', " ").strip()) if w]
    kept = [w for w in words if w.lower().strip(".,!?") not in _QUERY_FILLER]
    if not kept:
        kept = words
    simplified = " ".join(kept[:6]).strip()
    return simplified if simplified and simplified.lower() != (query or "").lower() else ""


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
        error = (
            "web_research requires a search query and could not infer one from the "
            "task. Call it again with an explicit query, e.g. the key subject words "
            "of the user's request."
        )
        return ToolResult(False, "web_research", error=error, data={"query": final_query})

    fetcher = fetch or fetch_url

    async def _candidates(q: str) -> list[WebSearchResult]:
        if search:
            return _parse_search_output(await search(q, max_results))
        return await search_results(q, max_results)

    # The primary query, plus ONE deterministic simplified retry when it comes up
    # short — retrieval was the eval's weakest link, and a single over-constrained
    # query ("aktuell bästa ... 2026") returning nothing should not end the task.
    attempts = [final_query]
    retry_query = simplified_query(final_query)
    if retry_query:
        attempts.append(retry_query)

    fetched: list[tuple[WebSearchResult, str]] = []
    failures: list[str] = []
    search_errors: list[str] = []
    seen_urls: set[str] = set()
    candidates_total = 0
    queries_used: list[str] = []

    for attempt_query in attempts:
        if len(fetched) >= min_sources:
            break
        try:
            candidates = await _candidates(attempt_query)
        except RuntimeError as exc:
            search_errors.append(str(exc))
            continue
        queries_used.append(attempt_query)
        candidates_total += len(candidates)
        for result in candidates:
            if len(fetched) >= min_sources:
                break
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            page = await fetcher(result.url, 5000)
            if page.startswith("fetch_url failed:"):
                failures.append(f"{result.url} -> {page}")
                continue
            fetched.append((result, page))

    lines = [f"Research results for {final_query!r}:", f"Sources fetched: {len(fetched)}"]
    if len(queries_used) > 1:
        lines.append(f"(also retried with simplified query {queries_used[1]!r})")
    if not fetched:
        # Explain WHY nothing came back and what to do next, so the model adapts
        # instead of re-running the identical call (eval 2026-07-02: six identical
        # retries). This text is the model's only feedback signal.
        lines.append("No readable sources could be fetched.")
        retry_done = retry_query and retry_query in queries_used
        if not queries_used and search_errors:
            # Every search attempt raised (transport failure) — no query actually
            # ran, so retrying later (not rephrasing) is the right advice.
            lines.append(
                "Why: the search request itself failed ("
                + "; ".join(search_errors[:2])
                + ")."
            )
            lines.append(
                "Try: wait and call web_research once more, or answer from existing "
                "knowledge while saying the web could not be reached."
            )
        elif candidates_total == 0:
            # A search DID run but found nothing — rephrasing is the fix.
            lines.append(
                "Why: the search returned zero results — the query is likely too "
                "narrow or phrased unusually."
            )
            if retry_done:
                lines.append(
                    "Try: a DIFFERENT 2-4 key-word English query (a simplified retry "
                    "was already attempted and also found nothing), or answer from "
                    "existing knowledge."
                )
            else:
                lines.append(
                    "Try: ONE retry with a shorter English query of 2-4 key words"
                    + (f", e.g. {retry_query!r}" if retry_query else "")
                    + ". Do not repeat the same query."
                )
        else:
            lines.append(
                f"Why: {candidates_total} result(s) were found but every page fetch "
                "failed — the sites likely block automated access."
            )
            lines.append(
                "Try: fetch_url on a specific well-known site for this topic, or "
                "answer from existing knowledge and say sources were unreachable. "
                "Do not repeat the same web_research call."
            )
    sources: list[dict[str, str]] = []
    for i, (result, page) in enumerate(fetched, 1):
        excerpt = re.sub(r"\s+", " ", page).strip()
        sources.append({"title": result.title, "url": result.url})
        lines.append(f"{i}. {result.title}\n   {result.url}\n   {excerpt[:900]}")
    if fetched and len(fetched) < min_sources:
        lines.append(
            f"Only {len(fetched)} readable source(s) were available from the search "
            "results. Use what is here rather than retrying the same query."
        )
    if failures:
        lines.append("Fetch failures:\n" + "\n".join(failures[:5]))
    text = "\n".join(lines)
    ok = bool(fetched)
    return ToolResult(
        ok=ok,
        kind="web_research",
        text=text if ok else "",
        error=None if ok else text,
        data={
            "query": final_query,
            "queries_used": queries_used,
            "sources_fetched": len(fetched),
            "failures": failures,
        },
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


# Non-content elements whose *inner* text is noise (menus, scripts, chrome).
# Dropped whole (open tag → close tag) before flattening the rest to text.
_NON_CONTENT_TAGS = (
    "script", "style", "noscript", "template", "svg", "iframe",
    "nav", "header", "footer", "aside", "form",
)
_NON_CONTENT_RE = re.compile(
    r"(?is)<(" + "|".join(_NON_CONTENT_TAGS) + r")\b[^>]*>.*?</\1>"
)
# Comments carry no readable text and can hide markup.
_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
# Prefer the main article body when the page marks one, so boilerplate around a
# short article does not crowd out the actual content within max_chars.
_MAIN_RE = re.compile(r"(?is)<(main|article)\b[^>]*>(?P<body>.*?)</\1>")


def html_to_text(body: str) -> str:
    """Reduce an HTML document to readable text.

    Dependency-light and robust: drop comments and non-content elements
    (script/style plus nav/header/footer/aside/form), prefer a <main>/<article>
    region when present, then flatten remaining tags and collapse whitespace.
    """
    body = _COMMENT_RE.sub(" ", body)
    body = _NON_CONTENT_RE.sub(" ", body)
    # Re-run once: nested chrome (e.g. a <nav> inside a stripped <header>) is
    # already gone, but a page may repeat top-level sections.
    body = _NON_CONTENT_RE.sub(" ", body)

    match = _MAIN_RE.search(body)
    if match:
        candidate = match.group("body")
        # Only prefer <main> when it actually carries text — some sites use an
        # empty <main> shell hydrated by JS.
        if len(re.sub(r"(?s)<[^>]+>", "", candidate).strip()) >= 200:
            body = candidate

    text = re.sub(r"(?s)<[^>]+>", " ", body)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


async def fetch_url(url: str, max_chars: int = 4000) -> str:
    """Fetch a URL and return its readable text content (HTML reduced to text)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        async def _call() -> httpx.Response:
            # aguard_request runs before the initial request AND on every redirect
            # hop, so a public URL that 302s to an internal address is blocked too.
            # The pinning transport then dials the exact validated IP for each hop,
            # closing the DNS-rebinding window between check and connect.
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True, headers={"User-Agent": _UA},
                event_hooks={"request": [aguard_request]},
                transport=pinned_async_transport(),
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp

        resp = await _with_retry(_call)
        content_type = resp.headers.get("content-type", "")
        text = resp.text
    except Exception as exc:
        return f"fetch_url failed: {type(exc).__name__}: {exc}"

    if "html" in content_type or text.lstrip().startswith("<"):
        text = html_to_text(text)
    else:
        text = re.sub(r"\s+", " ", text).strip()
    suffix = "" if len(text) <= max_chars else " …[truncated]"
    return f"{url}\n\n{text[:max_chars]}{suffix}"

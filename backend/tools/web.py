"""Lightweight web access: search + fetch.

No API key and no extra dependencies (httpx only) so it works out of the box —
the gap behind "what's the weather Wednesday?" / "look this up" (sessions
b0b0c177, e251136a). web_search uses DuckDuckGo's HTML endpoint; fetch_url
pulls a page and reduces it to readable text.
"""

from __future__ import annotations

import html
import re
from urllib.parse import unquote

import httpx

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


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return the top results as title — url — snippet."""
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
        return f"web_search failed: {type(exc).__name__}: {exc}"

    matches = list(_RESULT_RE.finditer(body))
    snippets = [_strip_tags(m.group("snippet")) for m in _SNIPPET_RE.finditer(body)]

    results = []
    for i, m in enumerate(matches):
        url = _clean_ddg_href(m.group("href"))
        # Skip DuckDuckGo sponsored/ad results (y.js redirects) — not real sources.
        if any(mark in url for mark in ("duckduckgo.com/y.js", "ad_provider=", "ad_domain=")):
            continue
        snippet = snippets[i] if i < len(snippets) else ""
        results.append((_strip_tags(m.group("title")), url, snippet))
        if len(results) >= max_results:
            break

    if not results:
        return f"No web results for {query!r}."

    lines = [f"Top results for {query!r}:"]
    for n, (title, url, snippet) in enumerate(results, 1):
        lines.append(f"{n}. {title}\n   {url}")
        if snippet:
            lines.append(f"   {snippet[:240]}")
    return "\n".join(lines)


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

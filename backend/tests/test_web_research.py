import asyncio
import contextlib
import os
import sys
import unittest
from unittest import mock

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@contextlib.contextmanager
def mock_async_transport(handler):
    """Route every internally-built httpx.AsyncClient through a MockTransport.

    web.py constructs its own clients, so we swap the class for one that injects
    an offline transport — no real sockets, ever.
    """
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    with mock.patch.object(httpx, "AsyncClient", fake_client):
        yield


class WebResearchTests(unittest.TestCase):
    def test_infers_query_from_swedish_source_request(self):
        from tools.web import infer_web_query

        query = infer_web_query(
            "Sök på webben efter de senaste nyheterna om Volvo Cars och sammanfatta tre källor med länkar."
        )

        self.assertEqual("de senaste nyheterna om Volvo Cars", query)

    def test_filters_duckduckgo_ads_and_redirects(self):
        from tools.web import is_blocked_result_url

        self.assertTrue(is_blocked_result_url("https://duckduckgo.com/y.js?ad_provider=bing"))
        self.assertTrue(is_blocked_result_url("https://www.bing.com/aclick?foo=bar"))
        self.assertFalse(is_blocked_result_url("https://www.volvocars.com/se/news/"))

    def test_parses_search_html_into_structured_results(self):
        from tools.web import WebSearchResult, parse_search_results_html

        body = """
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fnews">Example News</a>
        <a class="result__snippet">Useful snippet</a>
        <a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=bing">Ad</a>
        <a class="result__snippet">Ad snippet</a>
        """

        results = parse_search_results_html(body, max_results=5)

        self.assertEqual([WebSearchResult("Example News", "https://example.com/news", "Useful snippet")], results)

    def test_research_fetches_three_non_ad_sources(self):
        from tools.web import web_research

        from tools.web import WebSearchResult

        async def fake_search_results(query, max_results=8):
            return [
                WebSearchResult("Volvo Cars News", "https://www.volvocars.com/se/news/", "Latest company news."),
                WebSearchResult("DN Volvo Cars", "https://www.dn.se/om/volvo-cars/", "Senaste nyheterna."),
                WebSearchResult("SVT Volvo Cars", "https://www.svt.se/nyheter/om/volvo-cars", "Följ ämnet."),
            ]

        fetched: list[str] = []

        async def fake_fetch(url, max_chars=4000):
            fetched.append(url)
            return f"{url}\n\nArticle text from {url}"

        result = asyncio.run(
            web_research(
                "",
                task="Sök på webben efter de senaste nyheterna om Volvo Cars och sammanfatta tre källor med länkar.",
                min_sources=3,
                search_results=fake_search_results,
                fetch=fake_fetch,
            )
        )

        self.assertEqual(
            [
                "https://www.volvocars.com/se/news/",
                "https://www.dn.se/om/volvo-cars/",
                "https://www.svt.se/nyheter/om/volvo-cars",
            ],
            fetched,
        )
        self.assertIn("Research results for 'de senaste nyheterna om Volvo Cars'", result)
        self.assertIn("Sources fetched: 3", result)
        self.assertNotIn("duckduckgo.com/y.js", result)

    def test_research_result_returns_structured_tool_result(self):
        from tools.web import WebSearchResult, web_research_result

        async def fake_search_results(query, max_results=8):
            return [WebSearchResult("Source", "https://example.com", "Snippet")]

        async def fake_fetch(url, max_chars=4000):
            return f"{url}\n\nReadable page text"

        result = asyncio.run(
            web_research_result(
                "Volvo Cars",
                min_sources=1,
                search_results=fake_search_results,
                fetch=fake_fetch,
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual("web_research", result.kind)
        self.assertEqual("Volvo Cars", result.data["query"])
        self.assertEqual([{"title": "Source", "url": "https://example.com"}], result.sources)
        self.assertIn("Readable page text", result.text)

    def test_source_request_upgrades_plain_search_to_research(self):
        from agents.loop import repair_web_tool_call

        tool, args, note = repair_web_tool_call(
            "web_search",
            {},
            "Sök på webben efter de senaste nyheterna om Volvo Cars och sammanfatta tre källor med länkar.",
        )

        self.assertEqual("web_research", tool)
        self.assertEqual("de senaste nyheterna om Volvo Cars", args["query"])
        self.assertEqual(3, args["min_sources"])
        self.assertIn("web_research", note)

    def test_source_request_infers_requested_source_count(self):
        from agents.loop import repair_web_tool_call

        tool, args, note = repair_web_tool_call(
            "web_search",
            {},
            "Search for Volvo EX90 reviews and compare five sources with links.",
        )

        self.assertEqual("web_research", tool)
        self.assertEqual(5, args["min_sources"])
        self.assertIn("web_research", note)


# Fast, deterministic retry back-off for the offline tests below.
_FAST_RETRY = {"PILOT_HTTP_RETRIES": "2", "PILOT_HTTP_RETRY_DELAY": "0"}


class SearchProviderTests(unittest.TestCase):
    """Pluggable provider: JSON API when keyed, DuckDuckGo scrape when not."""

    def _reload_web(self):
        # web.py reads retry knobs at import, but provider/key at call time; the
        # retry delay is what we want fast here, so reimport under the patched env.
        import importlib

        import tools.web as web

        return importlib.reload(web)

    def test_tavily_provider_returns_normalized_results(self):
        env = {
            "PILOT_SEARCH_PROVIDER": "tavily",
            "PILOT_SEARCH_API_KEY": "test-key",
            **_FAST_RETRY,
        }
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "Volvo News", "url": "https://volvocars.com/news", "content": "Latest."},
                        {"title": "Ad", "url": "https://duckduckgo.com/y.js?ad_provider=bing", "content": "x"},
                        {"title": "DN", "url": "https://dn.se/volvo", "content": "Nyheter."},
                    ]
                },
            )

        with mock.patch.dict(os.environ, env, clear=False):
            web = self._reload_web()
            with mock_async_transport(handler):
                results = asyncio.run(web.search_web_results("volvo cars", max_results=5))

        self.assertEqual("https://api.tavily.com/search", seen["url"])
        self.assertEqual("Bearer test-key", seen["auth"])
        # Ad result filtered; the two real sources normalized to WebSearchResult.
        self.assertEqual(
            [("Volvo News", "https://volvocars.com/news"), ("DN", "https://dn.se/volvo")],
            [(r.title, r.url) for r in results],
        )
        self.assertEqual("Latest.", results[0].snippet)

    def test_brave_provider_parses_web_results(self):
        env = {
            "PILOT_SEARCH_PROVIDER": "brave",
            "PILOT_SEARCH_API_KEY": "brave-key",
            **_FAST_RETRY,
        }
        seen = {}

        def handler(request):
            seen["token"] = request.headers.get("x-subscription-token")
            return httpx.Response(
                200,
                json={"web": {"results": [{"title": "Result", "url": "https://ex.com", "description": "desc"}]}},
            )

        with mock.patch.dict(os.environ, env, clear=False):
            web = self._reload_web()
            with mock_async_transport(handler):
                results = asyncio.run(web.search_web_results("q", max_results=3))

        self.assertEqual("brave-key", seen["token"])
        self.assertEqual([("Result", "https://ex.com")], [(r.title, r.url) for r in results])

    def test_no_key_falls_back_to_duckduckgo_scraper(self):
        # Provider names an API but NO key -> zero-config scraper path is used.
        env = {"PILOT_SEARCH_PROVIDER": "tavily", "PILOT_SEARCH_API_KEY": "", **_FAST_RETRY}
        calls = []

        def handler(request):
            calls.append(str(request.url))
            body = (
                '<a class="result__a" href="//duckduckgo.com/l/?uddg='
                'https%3A%2F%2Fexample.com%2Fnews">Example News</a>'
                '<a class="result__snippet">Snippet</a>'
            )
            return httpx.Response(200, text=body)

        with mock.patch.dict(os.environ, env, clear=False):
            web = self._reload_web()
            with mock_async_transport(handler):
                results = asyncio.run(web.search_web_results("news", max_results=5))

        # It hit DuckDuckGo, not any JSON API endpoint.
        self.assertTrue(all("duckduckgo.com" in u for u in calls))
        self.assertEqual([("Example News", "https://example.com/news")], [(r.title, r.url) for r in results])

    def test_provider_failure_degrades_to_scraper(self):
        # A keyed provider that errors must not brick search: fall back to DDG.
        env = {
            "PILOT_SEARCH_PROVIDER": "tavily",
            "PILOT_SEARCH_API_KEY": "test-key",
            **_FAST_RETRY,
        }

        def handler(request):
            if "tavily" in str(request.url):
                return httpx.Response(500, json={"error": "boom"})
            body = (
                '<a class="result__a" href="https://fallback.example/a">Fallback</a>'
                '<a class="result__snippet">from ddg</a>'
            )
            return httpx.Response(200, text=body)

        with mock.patch.dict(os.environ, env, clear=False):
            web = self._reload_web()
            with mock_async_transport(handler):
                results = asyncio.run(web.search_web_results("q", max_results=5))

        self.assertEqual([("Fallback", "https://fallback.example/a")], [(r.title, r.url) for r in results])


class FetchUrlTests(unittest.TestCase):
    def test_extracts_clean_text_stripping_chrome(self):
        import tools.web as web

        page = """
        <html><head><title>T</title><style>.x{color:red}</style></head>
        <body>
          <nav>Home About Contact</nav>
          <header>Site Header Junk</header>
          <script>var x = 1;</script>
          <main><article><p>The real article body says hello world.</p></article></main>
          <aside>Related links noise</aside>
          <footer>Copyright 2026 noise</footer>
        </body></html>
        """

        def handler(request):
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})

        with mock.patch.dict(os.environ, _FAST_RETRY, clear=False):
            with mock_async_transport(handler):
                out = asyncio.run(web.fetch_url("https://example.com/a", max_chars=4000))

        self.assertIn("The real article body says hello world.", out)
        for noise in ("var x = 1", "color:red", "Home About Contact", "Site Header Junk"):
            self.assertNotIn(noise, out)

    def test_html_to_text_prefers_main_when_substantial(self):
        import tools.web as web

        main_body = "Article. " * 40  # > 200 chars of real text
        page = f"<html><body><nav>menu</nav><main><p>{main_body}</p></main></body></html>"
        text = web.html_to_text(page)
        self.assertIn("Article.", text)
        self.assertNotIn("menu", text)

    def test_fetch_failure_returns_error_string(self):
        import tools.web as web

        def handler(request):
            raise httpx.ConnectError("no route", request=request)

        with mock.patch.dict(os.environ, _FAST_RETRY, clear=False):
            with mock_async_transport(handler):
                out = asyncio.run(web.fetch_url("https://example.com/a"))

        self.assertTrue(out.startswith("fetch_url failed:"))


class RetryTests(unittest.TestCase):
    def test_retries_transient_then_succeeds(self):
        import importlib

        with mock.patch.dict(os.environ, _FAST_RETRY, clear=False):
            import tools.web as web

            web = importlib.reload(web)
            attempts = {"n": 0}

            def handler(request):
                attempts["n"] += 1
                if attempts["n"] < 2:
                    return httpx.Response(503, text="busy")
                body = (
                    '<a class="result__a" href="https://ok.example/a">OK</a>'
                    '<a class="result__snippet">s</a>'
                )
                return httpx.Response(200, text=body)

            with mock_async_transport(handler):
                results = asyncio.run(web.search_web_results("q", max_results=5))

        self.assertEqual(2, attempts["n"])  # one retry
        self.assertEqual([("OK", "https://ok.example/a")], [(r.title, r.url) for r in results])


if __name__ == "__main__":
    unittest.main()

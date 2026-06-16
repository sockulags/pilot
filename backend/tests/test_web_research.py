import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


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


if __name__ == "__main__":
    unittest.main()

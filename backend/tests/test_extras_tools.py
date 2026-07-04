"""Tests for the added tools: content search, HTTP, documents, processes, clipboard."""

import os
import tempfile
import unittest
from unittest import mock

import httpx

from tools import extras, registry


class SearchInFilesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pilot-grep-")
        with open(os.path.join(self.dir, "a.py"), "w", encoding="utf-8") as f:
            f.write("import os\n\ndef handler():\n    return TARGET_VALUE\n")
        with open(os.path.join(self.dir, "b.md"), "w", encoding="utf-8") as f:
            f.write("# Notes\nnothing here\nTARGET_VALUE mentioned in prose\n")
        with open(os.path.join(self.dir, "c.bin"), "wb") as f:
            f.write(b"\x00\x01TARGET_VALUE\x02")

    def test_finds_matches_with_line_numbers(self):
        result = extras.search_in_files("TARGET_VALUE", self.dir)
        paths = {(os.path.basename(m["path"]), m["line"]) for m in result["matches"]}
        self.assertIn(("a.py", 4), paths)
        self.assertIn(("b.md", 3), paths)

    def test_glob_filter_limits_files(self):
        result = extras.search_in_files("TARGET_VALUE", self.dir, glob="*.py")
        exts = {os.path.splitext(m["path"])[1] for m in result["matches"]}
        self.assertEqual(exts, {".py"})

    def test_binary_files_skipped_by_default(self):
        result = extras.search_in_files("TARGET_VALUE", self.dir)
        self.assertFalse(any(m["path"].endswith(".bin") for m in result["matches"]))

    def test_regex_mode(self):
        result = extras.search_in_files(r"def \w+\(", self.dir, regex=True)
        self.assertTrue(any("def handler" in m["text"] for m in result["matches"]))

    def test_bad_regex_returns_error(self):
        result = extras.search_in_files("(unclosed", self.dir, regex=True)
        self.assertIn("error", result)

    def test_missing_root(self):
        result = extras.search_in_files("x", os.path.join(self.dir, "nope"))
        self.assertIn("error", result)


class HttpRequestTests(unittest.TestCase):
    def test_rejects_non_http_scheme(self):
        self.assertIn("error", extras.http_request("ftp://x"))

    def test_rejects_unknown_method(self):
        self.assertIn("error", extras.http_request("https://x", method="FROB"))

    def test_parses_json_response(self):
        def handler(request):
            return httpx.Response(200, json={"hello": "world"})

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client

        def fake_client(**kwargs):
            kwargs.pop("transport", None)
            return real_client(transport=transport, **kwargs)

        with mock.patch.object(httpx, "Client", fake_client):
            result = extras.http_request("https://api.example/x")
        self.assertEqual(result["json"], {"hello": "world"})
        self.assertTrue(result["ok"])

    def test_non_get_requires_confirmation(self):
        self.assertTrue(
            registry.confirmation_required("http_request", {"url": "https://x", "method": "POST"})
        )
        self.assertFalse(
            registry.confirmation_required("http_request", {"url": "https://x", "method": "GET"})
        )


class ReadDocumentTests(unittest.TestCase):
    def test_reads_text_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("hello document")
            path = f.name
        try:
            result = extras.read_document(path)
            self.assertEqual(result["text"], "hello document")
        finally:
            os.unlink(path)

    def test_missing_file(self):
        self.assertIn("error", extras.read_document("C:/nope/missing.pdf"))

    def test_reads_pdf_roundtrip(self):
        pypdf = __import__("pypdf")
        # Build a tiny one-page PDF with pypdf itself, then extract.
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as f:
            writer.write(f)
            path = f.name
        try:
            result = extras.read_document(path)
            self.assertNotIn("error", result)
            self.assertEqual(result.get("pages"), 1)
        finally:
            os.unlink(path)


class ListProcessesTests(unittest.TestCase):
    def test_lists_something(self):
        result = extras.list_processes()
        self.assertIn("processes", result)
        # This test process (python) should be visible on any platform.
        self.assertTrue(result["processes"] or result.get("error"))

    def test_filter_narrows(self):
        result = extras.list_processes(filter_name="definitely-not-a-real-process-xyz")
        self.assertEqual(result.get("processes"), [])


class ClipboardTests(unittest.TestCase):
    def test_roundtrip_or_graceful_error(self):
        write = extras.write_clipboard("pilot-clip-test")
        if "error" in write:
            self.skipTest("no clipboard backend in this environment")
        read = extras.read_clipboard()
        self.assertEqual(read.get("text"), "pilot-clip-test")


class RegistryWiringTests(unittest.TestCase):
    def test_new_tools_are_registered_and_schema_valid(self):
        names = registry.coordinator_tool_names()
        for tool in ("search_in_files", "read_document", "http_request",
                     "list_processes", "read_clipboard", "write_clipboard"):
            self.assertIn(tool, names, tool)
        # Schemas generate without error.
        schemas = registry.tool_schemas()
        schema_names = {s["function"]["name"] for s in schemas}
        self.assertIn("search_in_files", schema_names)


if __name__ == "__main__":
    unittest.main()

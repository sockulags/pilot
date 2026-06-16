import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools import registry


class RegistryDerivationTests(unittest.TestCase):
    """The registry is the single source of truth: the views it generates must
    match the behaviour the rest of the system previously hard-coded."""

    def test_coordinator_allowlist(self):
        self.assertEqual(
            {
                # OS core (Fas A)
                "run_command", "read_file", "list_dir", "find_file", "list_windows",
                "focus_window", "screenshot", "get_screen_size", "open_app",
                "click_element", "click", "type_text", "key_press", "hotkey", "scroll",
                # New tools (Fas C)
                "search_files", "github_issues", "github_prs", "github_repo",
                "web_research", "web_search", "fetch_url",
            },
            registry.coordinator_tool_names(),
        )

    def test_loop_behaviour_sets(self):
        # Streaming / desktop / observe sets are unchanged by the Fas C tools.
        self.assertEqual({"run_command", "run_codex"}, registry.streaming_tool_names())
        self.assertEqual(
            {"click_element", "click", "type_text", "scroll", "move_mouse", "key_press", "hotkey"},
            registry.desktop_tool_names(),
        )
        self.assertEqual(
            registry.desktop_tool_names() | {"open_app"},
            registry.observe_after_tool_names(),
        )
        # The new read-only tools are deterministic (their result answers directly).
        self.assertEqual(
            {
                "list_dir", "read_file", "find_file", "list_windows", "focus_window",
                "search_files", "github_issues", "github_prs", "github_repo",
                "web_research", "web_search", "fetch_url",
            },
            registry.deterministic_tool_names(),
        )

    def test_mcp_manifest_names_unchanged(self):
        names = {t["name"] for t in registry.mcp_manifest()["tools"]}
        self.assertEqual(
            {
                "pilot_screenshot", "pilot_click", "pilot_type", "pilot_run_command",
                "pilot_open_app", "pilot_list_dir", "pilot_read_file", "pilot_find_file",
                "pilot_list_windows", "pilot_focus_window",
            },
            names,
        )

    def test_mcp_manifest_schema_shape(self):
        by_name = {t["name"]: t for t in registry.mcp_manifest()["tools"]}
        click = by_name["pilot_click"]
        self.assertEqual({"x", "y", "button"}, set(click["inputSchema"]["properties"]))
        self.assertEqual(["x", "y"], click["inputSchema"]["required"])
        self.assertEqual([], by_name["pilot_list_dir"]["inputSchema"]["required"])

    def test_capability_manifest_lists_real_tools_grouped(self):
        manifest = registry.capability_manifest()
        for needle in ("read_file", "find_file", "run_command", "click_element", "open_app"):
            self.assertIn(needle, manifest)
        # Grouped by human-readable category labels, not raw category keys.
        self.assertIn("Files & folders", manifest)
        self.assertIn("Desktop & screen", manifest)

    def test_tool_menu_only_exposes_coordinator_tools(self):
        menu = registry.tool_menu()
        self.assertIn("run_command", menu)
        self.assertNotIn("move_mouse", menu)   # coordinator=False
        self.assertNotIn("run_codex", menu)    # coordinator=False

    def test_tool_schemas_are_function_shaped(self):
        schemas = registry.tool_schemas()
        self.assertTrue(all(s["type"] == "function" for s in schemas))
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(registry.coordinator_tool_names(), names)


if __name__ == "__main__":
    unittest.main()

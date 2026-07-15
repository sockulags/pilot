import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools import registry
from fastapi.testclient import TestClient


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
                "generate_image",
                # New tools (Fas C)
                "search_files", "github_issues", "github_prs", "github_repo",
                "web_research", "web_search", "fetch_url",
                # First-class file output (2026-07-02 eval finding)
                "write_file",
                # Added tools (2026-07-04 audit gap analysis)
                "search_in_files", "read_document", "http_request",
                "list_processes", "read_clipboard", "write_clipboard",
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
                "web_research", "web_search", "fetch_url", "generate_image",
                "search_in_files", "read_document", "http_request",
                "list_processes", "read_clipboard",
                # write_clipboard is a side-effecting action, not deterministic
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

    def test_mcp_manifest_exposes_risk_metadata(self):
        by_name = {t["name"]: t for t in registry.mcp_manifest()["tools"]}

        self.assertEqual("medium", by_name["pilot_run_command"]["riskLevel"])
        self.assertTrue(by_name["pilot_run_command"]["sideEffects"])
        self.assertEqual("low", by_name["pilot_read_file"]["riskLevel"])
        self.assertFalse(by_name["pilot_read_file"]["sideEffects"])

    def test_capability_manifest_lists_real_tools_grouped(self):
        manifest = registry.capability_manifest()
        for needle in ("read_file", "find_file", "run_command", "click_element", "open_app", "generate_image"):
            self.assertIn(needle, manifest)
        # Grouped by human-readable category labels, not raw category keys.
        self.assertIn("Files & folders", manifest)
        self.assertIn("Desktop & screen", manifest)
        self.assertIn("Image generation", manifest)

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
        generate_image = next(s for s in schemas if s["function"]["name"] == "generate_image")
        props = generate_image["function"]["parameters"]["properties"]
        self.assertEqual({"prompt", "width", "height", "steps", "seed"}, set(props))
        self.assertEqual(["prompt"], generate_image["function"]["parameters"]["required"])

    def test_tool_specs_expose_risk_and_side_effect_metadata(self):
        read_file = registry.get("read_file")
        run_command = registry.get("run_command")
        type_text = registry.get("type_text")

        self.assertEqual("low", read_file.risk_level)
        self.assertFalse(read_file.side_effects)
        self.assertEqual("medium", run_command.risk_level)
        self.assertTrue(run_command.side_effects)
        self.assertEqual("medium", type_text.risk_level)
        self.assertTrue(type_text.side_effects)

    def test_confirmation_policy_flags_high_risk_commands(self):
        self.assertTrue(registry.confirmation_required("run_command", {"cmd": "Remove-Item -Recurse .\\data"}))
        self.assertTrue(registry.confirmation_required("run_command", {"cmd": "Set-Content -Path report.md -Value 'ok'"}))
        self.assertTrue(registry.confirmation_required("run_command", {"cmd": "npm install left-pad"}))
        self.assertTrue(registry.confirmation_required("run_command", {"cmd": "Get-Content backend/.env"}))
        self.assertTrue(registry.confirmation_required("read_file", {"path": "backend/.env"}))
        self.assertFalse(registry.confirmation_required("run_command", {"cmd": "Get-ChildItem backend"}))
        self.assertFalse(registry.confirmation_required("read_file", {"path": "README.md"}))

    def test_read_file_gates_on_full_secret_fragment_set(self):
        # Previously missing from read_file's gate (issue #74): reading an SSH
        # private key or a cert/key directly must prompt, matching the shell path.
        for path in (
            r"C:\Users\me\.ssh\id_dsa",
            r"C:\Users\me\.ssh\id_ecdsa",
            r"C:\certs\server.pem",
            r"C:\keys\private.key",
        ):
            self.assertTrue(
                registry.confirmation_required("read_file", {"path": path}),
                f"expected read_file to gate on {path!r}",
            )

    def test_read_file_and_shell_agree_across_fragments(self):
        # For every shared fragment, read_file's gate and the shell classifier's
        # SECRET_ACCESS check agree on the same path (single source of truth).
        from tools import command_risk

        for frag in command_risk.SECRET_FRAGMENTS:
            path = f"C:\\work\\thing{frag}"
            via_read = registry.confirmation_required("read_file", {"path": path})
            via_shell = command_risk.classify_command(f"cat {path}").requires_confirmation
            self.assertTrue(via_read, f"read_file should gate on {path!r}")
            self.assertTrue(via_shell, f"shell should gate on {path!r}")
            self.assertEqual(via_read, via_shell, f"gates disagree on {path!r}")

    def test_read_document_and_search_gate_on_secret_paths(self):
        # issue #85: read_document (by path) and search_in_files (by root) read
        # arbitrary file content but previously fell through to risk_level="low"
        # and were never gated. They must now prompt on the same secret fragments
        # as read_file, while non-secret paths stay ungated.
        for path in (r"C:\Users\me\.ssh\id_rsa", "backend/.env", r"C:\certs\server.pem"):
            self.assertTrue(
                registry.confirmation_required("read_document", {"path": path}),
                f"expected read_document to gate on {path!r}",
            )
            self.assertTrue(
                registry.confirmation_required("search_in_files", {"root": path}),
                f"expected search_in_files to gate on {path!r}",
            )
        self.assertFalse(registry.confirmation_required("read_document", {"path": "docs/cv.pdf"}))
        self.assertFalse(registry.confirmation_required("search_in_files", {"root": "backend"}))

    def test_read_document_search_and_read_file_agree_across_fragments(self):
        # For every shared fragment, read_document/search_in_files gate exactly
        # where read_file and the shell classifier do — one secret list, four
        # surfaces (shell, read_file, read_document, search_in_files).
        from tools import command_risk

        for frag in command_risk.SECRET_FRAGMENTS:
            path = f"C:\\work\\thing{frag}"
            via_read = registry.confirmation_required("read_file", {"path": path})
            via_doc = registry.confirmation_required("read_document", {"path": path})
            via_search = registry.confirmation_required("search_in_files", {"root": path})
            self.assertTrue(via_doc, f"read_document should gate on {path!r}")
            self.assertTrue(via_search, f"search_in_files should gate on {path!r}")
            self.assertEqual(via_read, via_doc, f"read_document disagrees on {path!r}")
            self.assertEqual(via_read, via_search, f"search_in_files disagrees on {path!r}")

    def test_mcp_call_enforces_confirmation_policy(self):
        from api.mcp import create_mcp_app

        with TestClient(create_mcp_app()) as client:
            response = client.post(
                "/mcp/call",
                json={
                    "name": "pilot_run_command",
                    "arguments": {"cmd": "Remove-Item -Recurse .\\data"},
                },
            )
            read_response = client.post(
                "/mcp/call",
                json={
                    "name": "pilot_read_file",
                    "arguments": {"path": "backend/.env"},
                },
            )

        self.assertEqual("confirmation_required", response.json()["error"])
        self.assertEqual("confirmation_required", read_response.json()["error"])


if __name__ == "__main__":
    unittest.main()

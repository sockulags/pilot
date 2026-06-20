import os
import sys
import unittest
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from project_instructions import (  # noqa: E402
    build_instruction_block,
    discover_instruction_files,
    extract_documented_commands,
)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class DiscoveryTests(unittest.TestCase):
    def test_root_files_discovered_and_included_in_block(self):
        with TemporaryDirectory() as cwd:
            _write(os.path.join(cwd, "AGENTS.md"), "Always run lint before commit.")
            _write(os.path.join(cwd, "CLAUDE.md"), "Use four-space indentation.")
            block = build_instruction_block(cwd)
            self.assertIn("Always run lint before commit.", block)
            self.assertIn("Use four-space indentation.", block)
            self.assertIn("AGENTS.md", block)
            self.assertIn("CLAUDE.md", block)
            self.assertTrue(block.startswith("Project instructions"))

    def test_dot_claude_nested_root_file(self):
        with TemporaryDirectory() as cwd:
            _write(os.path.join(cwd, ".claude", "CLAUDE.md"), "Hidden root rule.")
            block = build_instruction_block(cwd)
            self.assertIn("Hidden root rule.", block)
            self.assertIn(os.path.join(".claude", "CLAUDE.md"), block)

    def test_missing_cwd_and_no_files_return_empty(self):
        self.assertEqual(build_instruction_block(None), "")
        self.assertEqual(build_instruction_block("/no/such/dir/xyz"), "")
        with TemporaryDirectory() as cwd:
            self.assertEqual(build_instruction_block(cwd), "")
            self.assertEqual(discover_instruction_files(cwd), [])

    def test_no_edited_paths_uses_only_root_files(self):
        with TemporaryDirectory() as cwd:
            _write(os.path.join(cwd, "AGENTS.md"), "root rule")
            _write(os.path.join(cwd, "sub", "AGENTS.md"), "sub rule")
            files = discover_instruction_files(cwd)
            self.assertEqual([f.rel_path for f in files], ["AGENTS.md"])


class PrecedenceTests(unittest.TestCase):
    def test_nested_file_ordered_after_root_for_its_subtree(self):
        with TemporaryDirectory() as cwd:
            _write(os.path.join(cwd, "AGENTS.md"), "ROOT: use tabs.")
            _write(os.path.join(cwd, "frontend", "AGENTS.md"), "NESTED: use spaces.")
            edited = [os.path.join(cwd, "frontend", "app.tsx")]
            files = discover_instruction_files(cwd, edited_paths=edited)
            rels = [f.rel_path for f in files]
            self.assertIn("AGENTS.md", rels)
            self.assertIn(os.path.join("frontend", "AGENTS.md"), rels)
            # Nested must come AFTER root so it is presented as overriding.
            self.assertGreater(
                rels.index(os.path.join("frontend", "AGENTS.md")),
                rels.index("AGENTS.md"),
            )
            block = build_instruction_block(cwd, edited_paths=edited)
            self.assertGreater(
                block.index("NESTED: use spaces."),
                block.index("ROOT: use tabs."),
            )

    def test_nested_file_outside_edited_subtree_excluded(self):
        with TemporaryDirectory() as cwd:
            _write(os.path.join(cwd, "AGENTS.md"), "root")
            _write(os.path.join(cwd, "backend", "AGENTS.md"), "backend only")
            edited = [os.path.join(cwd, "frontend", "app.tsx")]
            files = discover_instruction_files(cwd, edited_paths=edited)
            rels = [f.rel_path for f in files]
            self.assertNotIn(os.path.join("backend", "AGENTS.md"), rels)

    def test_deeper_nested_file_ordered_last(self):
        with TemporaryDirectory() as cwd:
            _write(os.path.join(cwd, "AGENTS.md"), "root")
            _write(os.path.join(cwd, "a", "AGENTS.md"), "level1")
            _write(os.path.join(cwd, "a", "b", "AGENTS.md"), "level2")
            edited = [os.path.join(cwd, "a", "b", "c.py")]
            files = discover_instruction_files(cwd, edited_paths=edited)
            rels = [f.rel_path for f in files]
            self.assertEqual(rels[0], "AGENTS.md")
            self.assertEqual(rels[-1], os.path.join("a", "b", "AGENTS.md"))


class SizeLimitTests(unittest.TestCase):
    def test_block_truncated_to_max_chars(self):
        with TemporaryDirectory() as cwd:
            _write(os.path.join(cwd, "AGENTS.md"), "x" * 50000)
            block = build_instruction_block(cwd, max_chars=500)
            self.assertLessEqual(len(block), 600)
            self.assertIn("truncated", block)


class DocumentedCommandTests(unittest.TestCase):
    def test_extracts_test_and_setup_commands(self):
        with TemporaryDirectory() as cwd:
            _write(
                os.path.join(cwd, "AGENTS.md"),
                "# Project\n\n## Setup\n\n```\nuv sync\n```\n\n"
                "## Test\n\n```bash\n$ pytest -q\n```\n",
            )
            cmds = extract_documented_commands(cwd)
            self.assertEqual(cmds.get("test"), "pytest -q")
            self.assertEqual(cmds.get("setup"), "uv sync")

    def test_no_commands_returns_empty(self):
        with TemporaryDirectory() as cwd:
            _write(os.path.join(cwd, "AGENTS.md"), "Just prose, no sections.")
            self.assertEqual(extract_documented_commands(cwd), {})

    def test_verification_prefers_documented_test_command(self):
        from code_verification import _detect_verification_command

        with TemporaryDirectory() as cwd:
            # Has pyproject (would default to pytest -q) but documents a richer cmd.
            _write(os.path.join(cwd, "pyproject.toml"), "[project]\nname='x'\n")
            _write(
                os.path.join(cwd, "AGENTS.md"),
                "## Test\n\n```\npytest -q tests/\n```\n",
            )
            argv, label = _detect_verification_command(cwd)
            self.assertEqual(argv, ["pytest", "-q", "tests/"])
            self.assertIn("documented", label)


class CoordinatorContextTests(unittest.TestCase):
    def test_decision_context_includes_project_instructions_block(self):
        from agents.coordinator import _build_decision_context

        with TemporaryDirectory() as cwd:
            _write(os.path.join(cwd, "AGENTS.md"), "FOLLOW THE HOUSE RULES.")
            instructions = build_instruction_block(cwd)
            ctx = _build_decision_context(
                task="fix the bug",
                conversation=None,
                experts={},
                notes=[],
                project_instructions=instructions,
            )
            self.assertIn("FOLLOW THE HOUSE RULES.", ctx)
            self.assertIn("Project instructions", ctx)

    def test_decision_context_default_omits_block(self):
        from agents.coordinator import _build_decision_context

        ctx = _build_decision_context(
            task="hi", conversation=None, experts={}, notes=[]
        )
        self.assertNotIn("Project instructions", ctx)


if __name__ == "__main__":
    unittest.main()

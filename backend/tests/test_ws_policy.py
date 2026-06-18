import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class WebSocketPolicyTests(unittest.TestCase):
    def test_reply_model_uses_stable_default_for_user_visible_synthesis(self):
        from api.ws import _reply_model
        from config import OLLAMA_MODEL

        self.assertEqual(OLLAMA_MODEL, _reply_model("gpt-oss:20b"))

    def test_file_output_verification_requires_write_and_verification_commands(self):
        from api.ws import _file_output_verified

        self.assertFalse(_file_output_verified([
            {"type": "action", "tool": "run_command", "args": {"cmd": "ollama list"}},
            {"type": "action", "tool": "read_file", "args": {"path": "config.py"}},
        ]))
        self.assertFalse(_file_output_verified([
            {
                "type": "action",
                "tool": "run_command",
                "args": {"cmd": "Set-Content -Path model_report.md -Value 'ok'"},
            },
        ]))
        self.assertTrue(_file_output_verified([
            {
                "type": "action",
                "tool": "run_command",
                "args": {"cmd": "Set-Content -Path model_report.md -Value 'ok'"},
            },
            {"type": "action", "tool": "run_command", "args": {"cmd": "Test-Path model_report.md"}},
        ]))

    def test_fallback_markdown_report_writes_file(self):
        from api.ws import _write_fallback_markdown_report

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_fallback_markdown_report(
                "Modellrapport\n\n- gemma4:12b matchar.",
                output_dir=Path(tmp),
                session_id="sess",
                turn=3,
            )

            self.assertTrue(path.exists())
            self.assertIn("gemma4:12b", path.read_text(encoding="utf-8"))
            self.assertEqual("pilot_report_sess_3.md", path.name)


if __name__ == "__main__":
    unittest.main()

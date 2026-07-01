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

    def test_agent_role_catalog_exposes_configured_roles(self):
        from api.ws import agent_role_catalog

        roles = agent_role_catalog()
        by_role = {role["role"]: role for role in roles}

        self.assertIn("default_agent", by_role)
        self.assertIn("research_agent", by_role)
        self.assertIn("model", by_role["research_agent"])
        self.assertIn("available", by_role["research_agent"])
        self.assertIn("label", by_role["research_agent"])

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

    def test_runtime_file_output_verification_requires_verified_runtime_artifact(self):
        from agents.runtime_state import RuntimeState
        from api.ws import _runtime_file_output_verified

        state = RuntimeState()
        state.record_tool_result(
            "run_command",
            {"cmd": "Set-Content -Path model_report.md -Value 'ok'"},
            "Command: Set-Content -Path model_report.md -Value 'ok'\nOutput:\n",
            ok=True,
        )

        self.assertFalse(_runtime_file_output_verified(state))

        state.record_tool_result(
            "run_command",
            {"cmd": "Test-Path -Path model_report.md"},
            "Command: Test-Path -Path model_report.md\nOutput:\nTrue",
            ok=True,
            artifact_verified=True,
        )

        self.assertTrue(_runtime_file_output_verified(state))

    def test_append_verified_artifact_paths_adds_missing_runtime_path(self):
        from agents.runtime_state import RuntimeState
        from api.ws import _append_verified_artifact_paths

        state = RuntimeState()
        state.record_tool_result(
            "run_command",
            {"cmd": "Test-Path -Path C:\\repo\\report.html"},
            "Command: Test-Path -Path C:\\repo\\report.html\nOutput:\nTrue",
            ok=True,
            artifact_verified=True,
        )

        reply = _append_verified_artifact_paths("Rapporten är klar.", state)

        self.assertIn("C:\\repo\\report.html", reply)
        self.assertEqual(reply, _append_verified_artifact_paths(reply, state))

    def test_turn_meta_includes_confirmation_audit_evidence(self):
        from agents.runtime_state import RuntimeState
        from api.ws import _turn_meta

        state = RuntimeState()
        state.record_confirmation_required(
            "run_command",
            {"cmd": "Remove-Item -Recurse .\\data"},
            "Bekräftelse krävs innan jag kör run_command.",
        )

        meta = _turn_meta(
            4,
            "computer",
            "gemma4:12b",
            [{
                "type": "confirmation_required",
                "tool": "run_command",
                "args": {"cmd": "Remove-Item -Recurse .\\data"},
            }],
            "needs_input",
            "computer_action",
            runtime_state=state,
        )

        self.assertEqual("needs_input", meta["status"])
        self.assertEqual("confirmation_required", meta["runtime_state"]["actions"][0]["decision"])
        self.assertEqual("high", meta["runtime_state"]["actions"][0]["risk_level"])
        self.assertTrue(meta["runtime_state"]["actions"][0]["side_effects"])

    def test_turn_meta_records_agent_role_fallback(self):
        from api.ws import _turn_meta

        meta = _turn_meta(
            2,
            "computer",
            "gemma4:12b",
            [{
                "type": "turn_start",
                "agent_role": "research_agent",
                "agent_role_model": "missing:latest",
                "agent_role_fallback": "configured research_agent model is missing",
            }],
            "done",
            "research",
        )

        self.assertEqual("research_agent", meta["agent_role"])
        self.assertEqual("missing:latest", meta["agent_role_model"])
        self.assertIn("missing", meta["agent_role_fallback"])

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

    def test_chat_prompt_lists_image_generation_capability(self):
        from agents.orchestrator import _build_reply_messages

        messages = _build_reply_messages(
            [{"role": "user", "content": "Kan du generera bilder?"}],
            outcome=None,
        )
        system = messages[0]["content"]

        self.assertIn("run_command", system)
        self.assertIn("generate_image", system)
        self.assertIn("Image generation", system)
        self.assertNotIn("not have a built-in image generation tool", system)


if __name__ == "__main__":
    unittest.main()

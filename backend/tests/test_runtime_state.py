import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class RuntimeStateTests(unittest.TestCase):
    def test_tool_results_update_structured_fields(self):
        from agents.runtime_state import RuntimeState

        state = RuntimeState()

        state.record_tool_result(
            "read_file",
            {"path": "backend/agents/coordinator.py"},
            "File: backend/agents/coordinator.py\nContent:\n...",
            ok=True,
        )
        state.record_tool_result(
            "run_command",
            {"cmd": "Set-Content -Path report.md -Value 'ok'", "cwd": r"C:\repo"},
            "Command: Set-Content -Path report.md -Value 'ok'\nOutput:\n",
            ok=True,
        )
        state.record_tool_result(
            "run_command",
            {"cmd": "Test-Path report.md", "cwd": r"C:\repo"},
            "Command: Test-Path report.md\nOutput:\nTrue",
            ok=True,
            artifact_verified=True,
        )
        state.record_tool_result(
            "web_research",
            {"query": "pilot runtime state", "min_sources": 2},
            "Research results for 'pilot runtime state':\nSources fetched: 2\n- Source: https://example.com",
            ok=True,
        )

        self.assertEqual(["backend/agents/coordinator.py"], state.files_read)
        self.assertEqual("Set-Content -Path report.md -Value 'ok'", state.commands[0]["cmd"])
        self.assertEqual("pilot runtime state", state.sources[0]["query"])
        self.assertEqual([{"path": "report.md", "verified": True}], state.artifacts)
        self.assertTrue(all(item["ok"] for item in state.evidence_items))

    def test_contract_status_is_serialized_for_metadata(self):
        from agents.runtime_state import RuntimeState
        from agents.task_contracts import build_task_contract

        state = RuntimeState()
        contract = build_task_contract("create_file")

        result = contract.evaluate([])
        state.set_contract_result(contract, result)

        meta = state.to_meta()
        self.assertFalse(meta["verified"])
        self.assertEqual([], meta["artifacts"])
        self.assertEqual("create_file", meta["requirements"]["intent"])
        self.assertEqual(["verified local artifact"], meta["requirements"]["missing"])


class RuntimeStateIntegrationTests(unittest.TestCase):
    def test_final_answer_messages_include_structured_evidence(self):
        from agents import orchestrator
        from agents.loop import LoopOutcome
        from agents.runtime_state import RuntimeState

        state = RuntimeState()
        state.record_tool_result(
            "read_file",
            {"path": "backend/api/ws.py"},
            "File: backend/api/ws.py\nContent:\nwebsocket_endpoint",
            ok=True,
        )
        outcome = LoopOutcome(
            "done",
            "- read_file({'path': 'backend/api/ws.py'}) -> websocket_endpoint",
            runtime_state=state,
        )

        messages = orchestrator._build_reply_messages(
            [{"role": "user", "content": "Förklara websocketen"}],
            outcome,
        )

        grounding = messages[-1]["content"]
        self.assertIn("Strukturerat underlag", grounding)
        self.assertIn('"files_read": ["backend/api/ws.py"]', grounding)
        self.assertIn("Textlogg", grounding)

    def test_turn_meta_records_artifacts_and_verified_status(self):
        from agents.runtime_state import RuntimeState
        from api.ws import _turn_meta

        state = RuntimeState()
        state.record_tool_result(
            "run_command",
            {"cmd": "Test-Path report.md"},
            "Command: Test-Path report.md\nOutput:\nTrue",
            ok=True,
            artifact_verified=True,
        )

        meta = _turn_meta(
            3,
            "computer",
            "gemma4:12b",
            [{"type": "action", "tool": "run_command", "args": {"cmd": "Test-Path report.md"}}],
            "done",
            "create_file",
            runtime_state=state,
        )

        self.assertTrue(meta["verified"])
        self.assertEqual([{"path": "report.md", "verified": True}], meta["artifacts"])
        self.assertIn("runtime_state", meta)


if __name__ == "__main__":
    unittest.main()

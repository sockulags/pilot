import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TaskContractTests(unittest.TestCase):
    def test_research_requires_source_evidence_before_final_answer(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("research")

        self.assertIn("web_research", contract.allowed_tools)
        self.assertFalse(contract.evaluate([]).satisfied)
        result = contract.evaluate([
            {"tool": "web_research", "ok": True, "text": "Research results for 'x':\nSources fetched: 3"}
        ])
        self.assertTrue(result.satisfied)
        self.assertIn("sources", result.final_answer_requirements.lower())
        self.assertIn("synthesized answer", result.final_answer_requirements.lower())
        self.assertIn("weak", result.final_answer_requirements.lower())

    def test_research_contract_rejects_zero_fetched_sources(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("research")

        result = contract.evaluate([
            {
                "tool": "web_research",
                "ok": True,
                "text": "Research results for 'x':\nSources fetched: 0\nNo readable sources could be fetched.",
            }
        ])

        self.assertFalse(result.satisfied)

    def test_create_file_requires_verified_artifact(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("create_file")

        self.assertIn("run_command", contract.allowed_tools)
        self.assertFalse(contract.evaluate([
            {"tool": "run_command", "ok": True, "text": "Set-Content report.md"}
        ]).satisfied)
        result = contract.evaluate([
            {
                "tool": "run_command",
                "ok": True,
                "text": "Command: Test-Path report.md\nOutput:\nTrue",
                "artifact_verified": True,
            }
        ])
        self.assertTrue(result.satisfied)
        self.assertIn("path", result.final_answer_requirements.lower())

    def test_project_analysis_requires_local_file_inspection(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("project_analysis")

        self.assertTrue({"list_dir", "read_file", "search_files"}.issubset(contract.allowed_tools))
        self.assertFalse(contract.evaluate([
            {"tool": "consult", "ok": True, "text": "general explanation"}
        ]).satisfied)
        self.assertFalse(contract.evaluate([
            {"tool": "list_dir", "ok": True, "text": "Directory: .\nbackend"},
        ]).satisfied)
        result = contract.evaluate([
            {"tool": "list_dir", "ok": True, "text": "Directory: .\nbackend"},
            {"tool": "read_file", "ok": True, "text": "File: backend/agents/coordinator.py"},
        ])
        self.assertTrue(result.satisfied)
        self.assertIn("files", result.final_answer_requirements.lower())

    def test_run_command_requires_command_output_evidence(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("run_command")

        self.assertEqual({"run_command"}, contract.allowed_tools)
        self.assertFalse(contract.evaluate([]).satisfied)
        result = contract.evaluate([
            {"tool": "run_command", "ok": True, "text": "Command: git status\nOutput:\nclean"}
        ])
        self.assertTrue(result.satisfied)
        self.assertIn("command", result.final_answer_requirements.lower())


if __name__ == "__main__":
    unittest.main()

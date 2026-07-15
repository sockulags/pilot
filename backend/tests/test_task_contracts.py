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
            {"tool": "read_file", "ok": True, "text": f"File: {path}"}
            for path in contract.playbook_files
        ])
        self.assertTrue(result.satisfied)
        self.assertIn("files", result.final_answer_requirements.lower())

    def test_project_analysis_requires_backend_flow_playbook_files(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("project_analysis")

        self.assertIn("backend/api/ws.py", contract.final_answer_requirements)
        self.assertFalse(contract.evaluate([
            {
                "tool": "read_file",
                "ok": True,
                "text": "File: backend/agents/coordinator.py\nContent:\n...",
            },
        ]).satisfied)
        result = contract.evaluate([
            {
                "tool": "read_file",
                "ok": True,
                "text": f"File: {path}\nContent:\n...",
            }
            for path in (
                "backend/api/ws.py",
                "backend/agents/orchestrator.py",
                "backend/agents/coordinator.py",
                "backend/agents/loop.py",
                "backend/store.py",
                "backend/tools/registry.py",
            )
        ])
        self.assertTrue(result.satisfied)

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

    def test_local_model_audit_contract_requires_playbook_evidence_and_verified_artifact(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("local_model_audit_report")

        self.assertEqual(
            {"run_command", "read_file"},
            contract.allowed_tools,
        )
        self.assertFalse(contract.evaluate([]).satisfied)
        self.assertFalse(contract.evaluate([
            {
                "tool": "run_command",
                "ok": True,
                "text": "Command: ollama list\nOutput:\ngemma4:12b",
            },
            {
                "tool": "read_file",
                "ok": True,
                "args": {"path": "backend/config.py"},
                "text": "File: backend/config.py\nContent:\nOLLAMA_MODEL = os.getenv(...)",
            },
            {
                "tool": "run_command",
                "ok": True,
                "text": "Command: Test-Path local_model_audit_report.md\nOutput:\nTrue",
                "artifact_verified": True,
            },
        ]).satisfied)
        result = contract.evaluate([
            {
                "tool": "run_command",
                "ok": True,
                "text": "Command: ollama list\nOutput:\ngemma4:12b",
            },
            {
                "tool": "read_file",
                "ok": True,
                "args": {"path": "backend/config.py"},
                "text": "File: backend/config.py\nContent:\nOLLAMA_MODEL = os.getenv(...)",
            },
            {
                "tool": "read_file",
                "ok": True,
                "args": {"path": "README.md"},
                "text": "File: README.md\nContent:\nOLLAMA_MODEL default gemma4:12b",
            },
            {
                "tool": "run_command",
                "ok": True,
                "text": "Command: Test-Path local_model_audit_report.md\nOutput:\nTrue",
                "artifact_verified": True,
            },
        ])

        self.assertTrue(result.satisfied)
        self.assertIn("verified artifact path", result.final_answer_requirements.lower())


    def test_all_tool_backed_routes_have_a_default_contract(self):
        from agents.task_contracts import TaskContract, build_task_contract

        for intent in (
            "desktop_action",
            "shell_action",
            "code_change",
            "github_operation",
            "memory_write",
        ):
            contract = build_task_contract(intent)
            self.assertIsInstance(contract, TaskContract, intent)
            self.assertEqual(intent, contract.intent)
            self.assertTrue(contract.required_evidence, intent)
            self.assertTrue(contract.final_answer_requirements, intent)

    def test_desktop_action_requires_input_then_observation(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("desktop_action")

        self.assertIn("click_element", contract.allowed_tools)
        self.assertIn("perceive", contract.allowed_tools)
        # Input alone is not enough — needs a post-action observation.
        self.assertFalse(contract.evaluate([
            {"tool": "type_text", "ok": True, "text": "typed"},
        ]).satisfied)
        # Observation before the action does not count.
        self.assertFalse(contract.evaluate([
            {"tool": "perceive", "ok": True, "text": "screen"},
            {"tool": "type_text", "ok": True, "text": "typed"},
        ]).satisfied)
        result = contract.evaluate([
            {"tool": "type_text", "ok": True, "text": "typed"},
            {"tool": "perceive", "ok": True, "text": "screen after typing"},
        ])
        self.assertTrue(result.satisfied)

    def test_screen_analysis_requires_successful_visual_description(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("screen_analysis")

        self.assertEqual({"perceive"}, contract.allowed_tools)
        self.assertFalse(contract.evaluate([]).satisfied)
        self.assertFalse(contract.evaluate([
            {"tool": "perceive", "ok": True, "text": "Elements: [1] Browser"},
        ]).satisfied)
        self.assertFalse(contract.evaluate([
            {
                "tool": "perceive",
                "ok": True,
                "text": "Elements: [1] Browser\n\nVisual description: Vision analysis unavailable",
            },
        ]).satisfied)
        result = contract.evaluate([
            {
                "tool": "perceive",
                "ok": True,
                "text": (
                    "Elements: [1] Browser\n\nVisual description:\n"
                    "A dashboard with a narrow sidebar and low-contrast controls."
                ),
            },
        ])
        self.assertTrue(result.satisfied)

    def test_shell_action_requires_command_output(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("shell_action")

        self.assertEqual({"run_command"}, contract.allowed_tools)
        self.assertFalse(contract.evaluate([]).satisfied)
        result = contract.evaluate([
            {"tool": "run_command", "ok": True, "text": "Command: dir\nOutput:\nfile.txt"}
        ])
        self.assertTrue(result.satisfied)

    def test_code_change_requires_concrete_inspection(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("code_change")

        self.assertFalse(contract.evaluate([
            {"tool": "consult", "ok": True, "text": "I edited the file"},
        ]).satisfied)
        result = contract.evaluate([
            {"tool": "read_file", "ok": True, "text": "File: x.py\nContent:\n..."}
        ])
        self.assertTrue(result.satisfied)
        run_result = contract.evaluate([
            {"tool": "run_command", "ok": True, "text": "Command: pytest\nOutput:\n1 passed"}
        ])
        self.assertTrue(run_result.satisfied)

    def test_github_operation_requires_github_result(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("github_operation")

        self.assertFalse(contract.evaluate([]).satisfied)
        self.assertFalse(contract.evaluate([
            {"tool": "github_issues", "ok": False, "text": "gh issue list failed"},
        ]).satisfied)
        result = contract.evaluate([
            {"tool": "github_issues", "ok": True, "text": "Open issues in owner/repo (2):"}
        ])
        self.assertTrue(result.satisfied)
        gh_cmd = contract.evaluate([
            {
                "tool": "run_command",
                "ok": True,
                "args": {"cmd": "gh pr list"},
                "text": "Command: gh pr list\nOutput:\n#1 Fix",
            }
        ])
        self.assertTrue(gh_cmd.satisfied)

    def test_memory_write_requires_confirmed_save(self):
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("memory_write")

        self.assertEqual({"memory_write"}, contract.allowed_tools)
        self.assertFalse(contract.evaluate([]).satisfied)
        result = contract.evaluate([
            {"tool": "memory_write", "ok": True, "text": "Saved to long-term memory: likes tea"}
        ])
        self.assertTrue(result.satisfied)


if __name__ == "__main__":
    unittest.main()

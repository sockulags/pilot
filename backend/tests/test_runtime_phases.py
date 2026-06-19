import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class RuntimePhaseTests(unittest.TestCase):
    def test_planner_produces_steps_tied_to_active_contract(self):
        from agents.runtime_phases import plan_steps
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("project_analysis")

        steps = plan_steps(contract)

        self.assertTrue(steps)
        self.assertEqual(tuple(contract.playbook_files), tuple(step.args["path"] for step in steps))
        self.assertTrue(all(step.tool == "read_file" for step in steps))
        self.assertTrue(all(step.contract_intent == "project_analysis" for step in steps))

    def test_executor_policy_only_allows_contract_tools(self):
        from agents.runtime_phases import PlannedStep, validate_step_allowed
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("research")

        self.assertTrue(validate_step_allowed(PlannedStep("web_research", {"query": "pilot"}, "research"), contract).allowed)
        blocked = validate_step_allowed(PlannedStep("run_command", {"cmd": "dir"}, "research"), contract)
        self.assertFalse(blocked.allowed)
        self.assertIn("outside the research contract allowlist", blocked.reason)

    def test_verifier_owns_completion_status(self):
        from agents.runtime_phases import verify_contract
        from agents.runtime_state import RuntimeState
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("create_file")
        runtime_state = RuntimeState()

        result = verify_contract(contract, runtime_state)

        self.assertFalse(result.satisfied)
        self.assertEqual(["verified local artifact"], runtime_state.requirements["missing"])
        self.assertFalse(runtime_state.requirements["satisfied"])

    def test_synthesizer_cannot_complete_unverified_contract(self):
        from agents.runtime_phases import can_compose_final_answer, verify_contract
        from agents.runtime_state import RuntimeState
        from agents.task_contracts import build_task_contract

        contract = build_task_contract("create_file")
        runtime_state = RuntimeState()

        self.assertFalse(can_compose_final_answer(contract, runtime_state))
        verify_contract(contract, runtime_state)
        self.assertFalse(can_compose_final_answer(contract, runtime_state))
        runtime_state.record_tool_result(
            "run_command",
            {"cmd": "Test-Path -LiteralPath report.md"},
            "Command: Test-Path -LiteralPath report.md\nOutput:\nTrue",
            True,
            True,
        )
        verify_contract(contract, runtime_state)

        self.assertTrue(can_compose_final_answer(contract, runtime_state))


if __name__ == "__main__":
    unittest.main()

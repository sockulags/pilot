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


    def test_default_contracts_block_premature_answer_until_evidence(self):
        from agents.runtime_phases import can_compose_final_answer, verify_contract
        from agents.runtime_state import RuntimeState
        from agents.task_contracts import build_task_contract

        cases = {
            "desktop_action": [
                ("type_text", {"text": "hi"}, "typed", True),
                ("perceive", {}, "screen after typing", True),
            ],
            "shell_action": [
                ("run_command", {"cmd": "dir"}, "Command: dir\nOutput:\nfile.txt", True),
            ],
            "code_change": [
                ("read_file", {"path": "x.py"}, "File: x.py\nContent:\n...", True),
            ],
            "github_operation": [
                ("github_issues", {"repo": "o/r"}, "Open issues in o/r (1):", True),
            ],
        }
        for intent, evidence in cases.items():
            contract = build_task_contract(intent)
            state = RuntimeState()
            # No evidence yet: premature answer must be blocked.
            self.assertFalse(can_compose_final_answer(contract, state), intent)
            verify_contract(contract, state)
            self.assertFalse(can_compose_final_answer(contract, state), intent)
            for tool, args, result, ok in evidence:
                state.record_tool_result(tool, args, result, ok)
            verify_contract(contract, state)
            self.assertTrue(can_compose_final_answer(contract, state), intent)

    def test_no_contract_still_answers(self):
        from agents.runtime_phases import can_compose_final_answer
        from agents.runtime_state import RuntimeState

        self.assertTrue(can_compose_final_answer(None, RuntimeState()))

    def test_runtime_metadata_exposes_contract_phase(self):
        from agents.runtime_phases import verify_contract
        from agents.runtime_state import RuntimeState
        from agents.task_contracts import build_task_contract

        state = RuntimeState()
        contract = build_task_contract("shell_action")

        # Before any contract result is set: no_contract phase.
        prompt = state.to_prompt_dict()
        self.assertIsNone(prompt["contract_intent"])
        self.assertFalse(prompt["contract_satisfied"])
        self.assertEqual("no_contract", prompt["phase"])

        verify_contract(contract, state)
        gathering = state.to_prompt_dict()
        self.assertEqual("shell_action", gathering["contract_intent"])
        self.assertFalse(gathering["contract_satisfied"])
        self.assertEqual("gathering", gathering["phase"])

        state.record_tool_result("run_command", {"cmd": "dir"}, "Command: dir\nOutput:\nok", True)
        verify_contract(contract, state)
        verified = state.to_prompt_dict()
        self.assertTrue(verified["contract_satisfied"])
        self.assertEqual("verified", verified["phase"])

    def test_resolver_maps_side_effecting_action_but_not_chat(self):
        from agents.turn_policy import build_task_context, resolve_task_contract_intent

        # A side-effecting desktop action with no run_command match -> default.
        self.assertEqual(
            "desktop_action",
            resolve_task_contract_intent(build_task_context([], "Öppna Notepad")),
        )
        # A shell run still resolves to the specific run_command contract.
        self.assertEqual(
            "run_command",
            resolve_task_contract_intent(build_task_context([], "Kör git status")),
        )
        # A plain conversational/Q&A turn maps to None (no over-gating).
        self.assertIsNone(resolve_task_contract_intent(build_task_context([], "Vad är huvudstaden i Sverige?")))
        self.assertIsNone(resolve_task_contract_intent(build_task_context([], "Berätta en rolig historia")))


if __name__ == "__main__":
    unittest.main()

"""Explicit runtime phases shared by coordinator-style loops."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from agents.runtime_state import RuntimeState
from agents.task_contracts import ContractResult, TaskContract


@dataclass(frozen=True)
class PlannedStep:
    tool: str
    args: dict
    contract_intent: str
    reason: str = ""


@dataclass(frozen=True)
class StepPolicy:
    allowed: bool
    reason: str = ""


def plan_steps(contract: TaskContract | None) -> tuple[PlannedStep, ...]:
    """Build deterministic contract-tied setup steps for a turn."""
    if not contract:
        return ()
    return tuple(
        PlannedStep(
            tool="read_file",
            args={"path": path},
            contract_intent=contract.intent,
            reason="contract playbook file",
        )
        for path in contract.playbook_files
    )


def validate_step_allowed(step: PlannedStep, contract: TaskContract | None) -> StepPolicy:
    if contract and step.tool not in contract.allowed_tools:
        return StepPolicy(
            False,
            f"tool {step.tool!r} is outside the {contract.intent} contract allowlist",
        )
    if contract and step.contract_intent != contract.intent:
        return StepPolicy(
            False,
            f"planned step belongs to {step.contract_intent!r}, not {contract.intent!r}",
        )
    return StepPolicy(True)


def verify_contract(contract: TaskContract, runtime_state: RuntimeState) -> ContractResult:
    result = contract.evaluate(runtime_state.evidence_items)
    runtime_state.set_contract_result(contract, result)
    return result


def can_compose_final_answer(contract: TaskContract | None, runtime_state: RuntimeState) -> bool:
    if not contract:
        return True
    requirements = runtime_state.requirements
    if requirements.get("intent") != contract.intent:
        return False
    return bool(requirements.get("satisfied"))


def missing_requirements_text(missing: Iterable[str]) -> str:
    return ", ".join(missing)

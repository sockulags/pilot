"""Runtime contracts for tool-backed task intents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class EvidenceRequirement:
    name: str
    description: str


@dataclass(frozen=True)
class ContractResult:
    satisfied: bool
    missing: tuple[str, ...]
    final_answer_requirements: str


@dataclass(frozen=True)
class TaskContract:
    intent: str
    required_evidence: tuple[EvidenceRequirement, ...]
    allowed_tools: frozenset[str]
    completion_criteria: str
    failure_criteria: str
    final_answer_requirements: str
    playbook_files: tuple[str, ...] = ()

    def evaluate(self, evidence: Iterable[dict]) -> ContractResult:
        items = tuple(evidence)
        missing = tuple(
            requirement.description
            for requirement in self.required_evidence
            if not _has_requirement(requirement.name, items)
        )
        return ContractResult(
            satisfied=not missing,
            missing=missing,
            final_answer_requirements=self.final_answer_requirements,
        )


def build_task_contract(intent: str) -> TaskContract | None:
    normalized = (intent or "").strip().lower()
    if normalized == "research_and_create_file":
        normalized = "create_file"
    return _CONTRACTS.get(normalized)


def _has_requirement(name: str, evidence: tuple[dict, ...]) -> bool:
    if name == "source_evidence":
        return any(
            item.get("ok")
            and item.get("tool") == "web_research"
            and _sources_fetched_count(str(item.get("text", ""))) > 0
            for item in evidence
        )
    if name == "verified_artifact":
        return any(item.get("artifact_verified") for item in evidence)
    if name == "local_file_inspection":
        inspected = {item.get("tool") for item in evidence if item.get("ok")}
        return bool(inspected.intersection({"read_file", "search_files", "find_file"}))
    if name == "backend_flow_playbook_files":
        return _has_all_files(_BACKEND_FLOW_PLAYBOOK_FILES, evidence)
    if name == "command_output":
        return any(
            item.get("ok")
            and item.get("tool") == "run_command"
            and "output:" in str(item.get("text", "")).lower()
            for item in evidence
        )
    return False


def _sources_fetched_count(text: str) -> int:
    match = re.search(r"(?im)^\s*sources fetched:\s*(\d+)\s*$", text)
    return int(match.group(1)) if match else 0


def _has_all_files(required_paths: tuple[str, ...], evidence: tuple[dict, ...]) -> bool:
    inspected = {
        _normalize_path(path)
        for item in evidence
        if item.get("ok") and item.get("tool") == "read_file"
        for path in (_evidence_path(item),)
        if path
    }
    return all(
        any(path.endswith(_normalize_path(required)) for path in inspected)
        for required in required_paths
    )


def _evidence_path(item: dict) -> str:
    args = item.get("args")
    if isinstance(args, dict) and args.get("path"):
        return str(args["path"])
    text = str(item.get("text", ""))
    first_line = text.splitlines()[0] if text else ""
    if first_line.startswith("File: "):
        return first_line.removeprefix("File: ").strip()
    return ""


def _normalize_path(path: str) -> str:
    return str(path).replace("\\", "/").lower().strip()


_BACKEND_FLOW_PLAYBOOK_FILES = (
    "backend/api/ws.py",
    "backend/agents/orchestrator.py",
    "backend/agents/coordinator.py",
    "backend/agents/loop.py",
    "backend/store.py",
    "backend/tools/registry.py",
)


_CONTRACTS: dict[str, TaskContract] = {
    "research": TaskContract(
        intent="research",
        required_evidence=(
            EvidenceRequirement("source_evidence", "source evidence from web_research"),
        ),
        allowed_tools=frozenset({"web_research", "web_search", "fetch_url"}),
        completion_criteria=(
            "Derive focused search queries, run web_research, fetch the requested "
            "minimum source count where possible, and preserve source URLs."
        ),
        failure_criteria="Web research fails or no readable sources can be fetched.",
        final_answer_requirements=(
            "Write a synthesized answer, not raw web_research(...) logs. Cite the "
            "fetched source URLs, give a recommendation when the question asks for "
            "one, and explicitly say when sources are weak, too few, or unavailable."
        ),
    ),
    "create_file": TaskContract(
        intent="create_file",
        required_evidence=(
            EvidenceRequirement("verified_artifact", "verified local artifact"),
        ),
        allowed_tools=frozenset({"run_command", "read_file", "list_dir", "search_files", "web_research"}),
        completion_criteria="A local file is written and its path/existence is verified.",
        failure_criteria="The artifact cannot be written or verified.",
        final_answer_requirements="Report the verified artifact path and any important caveats.",
    ),
    "project_analysis": TaskContract(
        intent="project_analysis",
        required_evidence=(
            EvidenceRequirement(
                "backend_flow_playbook_files",
                "backend flow playbook files inspected",
            ),
        ),
        allowed_tools=frozenset({"list_dir", "read_file", "search_files", "find_file", "run_command"}),
        completion_criteria=(
            "Relevant local project files have been inspected, including the backend "
            "flow playbook files when the user asks about backend/WebSocket/tool flow."
        ),
        failure_criteria="Project files cannot be accessed or inspected.",
        final_answer_requirements=(
            "Base the analysis on inspected files and name the files used. For backend "
            "flow questions, reference backend/api/ws.py, backend/agents/orchestrator.py, "
            "backend/agents/coordinator.py, backend/agents/loop.py, backend/store.py, "
            "and backend/tools/registry.py; identify risks with file/flow-specific evidence."
        ),
        playbook_files=_BACKEND_FLOW_PLAYBOOK_FILES,
    ),
    "run_command": TaskContract(
        intent="run_command",
        required_evidence=(
            EvidenceRequirement("command_output", "command output evidence"),
        ),
        allowed_tools=frozenset({"run_command"}),
        completion_criteria="The requested command ran and produced output or an explicit error.",
        failure_criteria="The command cannot be executed or is blocked by safety policy.",
        final_answer_requirements="Summarize the command output, including errors if the command failed.",
    ),
}

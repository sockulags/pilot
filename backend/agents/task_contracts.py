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
    if name == "ollama_list_output":
        return any(
            item.get("ok")
            and item.get("tool") == "run_command"
            and "ollama list" in _command_text(item).lower()
            and "output:" in str(item.get("text", "")).lower()
            for item in evidence
        )
    if name == "local_model_config":
        return _has_all_files(("backend/config.py",), evidence)
    if name == "local_model_default_docs":
        return any(
            _normalize_path(_evidence_path(item)).endswith(path)
            for item in evidence
            if item.get("ok") and item.get("tool") == "read_file"
            for path in ("readme.md", "getting_started.md", "backend/.env")
        )
    if name == "desktop_input_action":
        # A successful desktop input tool (click/type/key/etc.) actually ran.
        return any(
            item.get("ok") and item.get("tool") in _DESKTOP_INPUT_TOOLS
            for item in evidence
        )
    if name == "post_action_observation":
        # A perceive/screen observation recorded AFTER a desktop input action,
        # so the effect of the action was actually verified on screen.
        last_input = -1
        for index, item in enumerate(evidence):
            if item.get("ok") and item.get("tool") in _DESKTOP_INPUT_TOOLS:
                last_input = index
        if last_input < 0:
            return False
        return any(
            item.get("tool") in _SCREEN_OBSERVE_TOOLS
            for item in evidence[last_input + 1:]
        )
    if name == "code_change_inspection":
        # Lenient: at least one concrete inspection/verification of the changed
        # code — a read_file/search_files, a run_command (test/diff/build), or a
        # verified code_verification artifact.
        for item in evidence:
            if not item.get("ok"):
                continue
            if item.get("artifact_verified"):
                return True
            if item.get("tool") in {"read_file", "search_files", "find_file", "run_command"}:
                return True
        return False
    if name == "github_operation_result":
        # A successful github tool result, OR a successful run_command that ran
        # gh/git and produced output.
        for item in evidence:
            if not item.get("ok"):
                continue
            tool = item.get("tool")
            if tool in _GITHUB_TOOLS:
                return True
            if tool == "run_command":
                cmd = _command_text(item).lower()
                if (cmd.startswith("gh ") or " gh " in f" {cmd}" or cmd.startswith("git ")) and (
                    "output:" in str(item.get("text", "")).lower()
                ):
                    return True
        return False
    if name == "memory_write_confirmed":
        # The coordinator's remember path confirmed a save: a memory_write
        # evidence record, or a note that the fact was saved to long-term memory.
        return any(
            item.get("ok")
            and (
                item.get("tool") == "memory_write"
                or "saved to long-term memory" in str(item.get("text", "")).lower()
            )
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


def _command_text(item: dict) -> str:
    args = item.get("args")
    if isinstance(args, dict):
        return str(args.get("cmd") or args.get("command") or item.get("text") or "")
    return str(item.get("text") or "")


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

# Desktop input tools (side-effecting GUI actions) and the screen-observation
# tools used to verify their effect. Kept in sync with the registry's desktop
# specs / perceive path; listed literally so the contract checks stay pure.
_DESKTOP_INPUT_TOOLS = frozenset({
    "click",
    "click_element",
    "type_text",
    "key_press",
    "hotkey",
    "scroll",
    "move_mouse",
    "open_app",
})
_SCREEN_OBSERVE_TOOLS = frozenset({"perceive", "screenshot"})
_GITHUB_TOOLS = frozenset({"github_issues", "github_prs", "github_repo"})


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
    # --- Default/fallback contracts for tool-backed, side-effecting intents ---
    "desktop_action": TaskContract(
        intent="desktop_action",
        required_evidence=(
            EvidenceRequirement("desktop_input_action", "a successful desktop input action"),
            EvidenceRequirement(
                "post_action_observation",
                "a post-action screen observation verifying the effect",
            ),
        ),
        allowed_tools=frozenset(
            _DESKTOP_INPUT_TOOLS
            | _SCREEN_OBSERVE_TOOLS
            | {"list_windows", "focus_window", "get_screen_size"}
        ),
        completion_criteria=(
            "Run the requested desktop input action, then perceive/observe the screen "
            "again to confirm the action had the intended effect."
        ),
        failure_criteria="The desktop action cannot be performed or its effect cannot be observed.",
        final_answer_requirements=(
            "Describe what was done and what the screen showed afterward. Do not claim "
            "an action succeeded unless a post-action screen observation confirms it."
        ),
    ),
    "shell_action": TaskContract(
        # Generic shell side-effect task — same evidence shape as run_command.
        intent="shell_action",
        required_evidence=(
            EvidenceRequirement("command_output", "command output evidence"),
        ),
        allowed_tools=frozenset({"run_command"}),
        completion_criteria="The requested command ran and produced output or an explicit error.",
        failure_criteria="The command cannot be executed or is blocked by safety policy.",
        final_answer_requirements="Summarize the command output, including errors if the command failed.",
    ),
    "code_change": TaskContract(
        intent="code_change",
        required_evidence=(
            EvidenceRequirement(
                "code_change_inspection",
                "a concrete inspection/verification of the changed code",
            ),
        ),
        allowed_tools=frozenset(
            {"run_command", "read_file", "list_dir", "search_files", "find_file"}
        ),
        completion_criteria=(
            "Apply the code change, then inspect/verify it — re-read the file, run the "
            "relevant test/diff/build, or confirm the code_verification artifact."
        ),
        failure_criteria="The change cannot be applied or no inspection/verification can be run.",
        final_answer_requirements=(
            "Report the change and the evidence that verified it (file inspection, test, "
            "or diff). Do not claim a code change succeeded without a concrete inspection."
        ),
    ),
    "github_operation": TaskContract(
        intent="github_operation",
        required_evidence=(
            EvidenceRequirement("github_operation_result", "a successful GitHub tool result"),
        ),
        allowed_tools=frozenset(
            _GITHUB_TOOLS | {"run_command", "read_file"}
        ),
        completion_criteria=(
            "Run the GitHub operation (a github_* tool, or gh/git via run_command) and "
            "capture its output."
        ),
        failure_criteria="The GitHub operation fails or gh/git is unavailable.",
        final_answer_requirements=(
            "Base the answer on the actual GitHub tool output; do not invent issues, PRs, "
            "or repository details that were not returned."
        ),
    ),
    "memory_write": TaskContract(
        intent="memory_write",
        required_evidence=(
            EvidenceRequirement("memory_write_confirmed", "a confirmed memory save"),
        ),
        allowed_tools=frozenset({"memory_write"}),
        completion_criteria="The durable fact was saved to long-term memory and the save was confirmed.",
        failure_criteria="The memory save did not occur or could not be confirmed.",
        final_answer_requirements=(
            "Confirm exactly what was saved to long-term memory. Do not claim a fact was "
            "remembered unless RuntimeState records the save."
        ),
    ),
    "local_model_audit_report": TaskContract(
        intent="local_model_audit_report",
        required_evidence=(
            EvidenceRequirement("ollama_list_output", "ollama list output"),
            EvidenceRequirement("local_model_config", "backend/config.py inspected"),
            EvidenceRequirement("local_model_default_docs", "model env/default docs inspected"),
            EvidenceRequirement("verified_artifact", "verified local artifact"),
        ),
        allowed_tools=frozenset({"run_command", "read_file"}),
        completion_criteria=(
            "Run ollama list, inspect backend/config.py plus relevant env/default docs, "
            "compare installed and configured models, write a Markdown report, and verify it exists."
        ),
        failure_criteria="The model audit report cannot be written or verified.",
        final_answer_requirements=(
            "Report the verified artifact path exactly. Do not claim the file exists unless "
            "RuntimeState contains the verified artifact path."
        ),
    ),
}

"""Structured per-turn evidence gathered while tools run."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from tools import registry


_EXPLICIT_PATH_ARG_RE = re.compile(
    r"(?:-LiteralPath\s+|(?<![A-Za-z])-Path\s+|Get-Item\s+)(['\"]?)(?P<path>[^'\"\r\n]+?)\1(?:\s|$)",
    re.IGNORECASE,
)
_TEST_PATH_ARG_RE = re.compile(
    r"Test-Path\s+(?!-)(['\"]?)(?P<path>[^'\"\r\n]+?)\1(?:\s|$)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s)>\]]+")


@dataclass
class RuntimeState:
    actions: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    requirements: dict[str, Any] = field(default_factory=dict)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)

    def record_tool_result(
        self,
        tool: str,
        args: dict | None,
        result: str,
        ok: bool,
        artifact_verified: bool = False,
    ) -> None:
        args = dict(args or {})
        text = str(result or "")
        action = {
            "tool": tool,
            "args": args,
            "ok": bool(ok),
            "summary": text[:1000],
            "risk_level": registry.risk_level_for(tool, args),
            "side_effects": registry.side_effects_for(tool),
            "decision": "allowed" if ok else "failed",
        }
        self.actions.append(action)
        self.evidence_items.append({
            "tool": tool,
            "args": args,
            "ok": bool(ok),
            "text": text,
            "artifact_verified": bool(artifact_verified),
            "risk_level": action["risk_level"],
            "side_effects": action["side_effects"],
            "decision": action["decision"],
        })
        if not ok:
            self.errors.append({"tool": tool, "args": args, "error": text[:1000]})

        if tool == "read_file":
            path = str(args.get("path") or _path_from_tool_text(text) or "").strip()
            if path and path not in self.files_read:
                self.files_read.append(path)
        elif tool == "run_command":
            cmd = str(args.get("cmd") or args.get("command") or "")
            self.commands.append({
                "cmd": cmd,
                "cwd": args.get("cwd"),
                "ok": bool(ok),
                "summary": text[:1000],
            })
            artifact_path = _artifact_path_from_command(cmd)
            if artifact_path:
                self._record_artifact(artifact_path, artifact_verified)
        elif tool == "web_research":
            self.sources.append(_web_research_source_record(args, text))
        elif tool == "fetch_url":
            self.sources.append({
                "url": str(args.get("url") or ""),
                "summary": text[:500],
            })
        elif tool == "generate_image":
            for path in _generated_image_paths(text):
                self._record_artifact(path, True)

    def record_memory_write(self, fact: str, mem_id: str | None = None) -> None:
        """Record that a durable fact was saved to long-term memory.

        Surfaced as a ``memory_write`` evidence item so the ``memory_write``
        contract can verify the save actually occurred before the turn answers.
        """
        text = f"Saved to long-term memory: {fact}"
        self.evidence_items.append({
            "tool": "memory_write",
            "args": {"fact": fact, "mem_id": mem_id} if mem_id else {"fact": fact},
            "ok": True,
            "text": text,
            "artifact_verified": False,
            "risk_level": "low",
            "side_effects": True,
            "decision": "allowed",
        })

    def record_error(self, message: str, tool: str | None = None, args: dict | None = None) -> None:
        error = {"error": str(message)}
        if tool:
            error["tool"] = tool
        if args:
            error["args"] = dict(args)
        self.errors.append(error)

    def record_confirmation_required(self, tool: str, args: dict | None, reason: str) -> None:
        args = dict(args or {})
        action = {
            "tool": tool,
            "args": args,
            "ok": False,
            "summary": reason,
            "risk_level": registry.risk_level_for(tool, args),
            "side_effects": registry.side_effects_for(tool),
            "decision": "confirmation_required",
        }
        self.actions.append(action)
        self.evidence_items.append({
            "tool": tool,
            "args": args,
            "ok": False,
            "text": reason,
            "artifact_verified": False,
            "risk_level": action["risk_level"],
            "side_effects": action["side_effects"],
            "decision": "confirmation_required",
        })
        self.errors.append({"tool": tool, "args": args, "error": reason})

    def set_contract_result(self, contract, result) -> None:
        self.requirements = {
            "intent": contract.intent,
            "satisfied": bool(result.satisfied),
            "missing": list(result.missing),
            "final_answer_requirements": result.final_answer_requirements,
        }

    def _contract_status(self) -> dict[str, Any]:
        """Small status block naming which contract/phase completed the turn."""
        if not self.requirements:
            return {"contract_intent": None, "contract_satisfied": False, "phase": "no_contract"}
        satisfied = bool(self.requirements.get("satisfied"))
        return {
            "contract_intent": self.requirements.get("intent"),
            "contract_satisfied": satisfied,
            "phase": "verified" if satisfied else "gathering",
        }

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "actions": self.actions,
            "artifacts": self.artifacts,
            "sources": self.sources,
            "files_read": self.files_read,
            "commands": self.commands,
            "errors": self.errors,
            "requirements": self.requirements,
            **self._contract_status(),
        }

    def to_meta(self) -> dict[str, Any]:
        verified = bool(self.requirements.get("satisfied")) if self.requirements else any(
            artifact.get("verified") for artifact in self.artifacts
        )
        return {
            "artifacts": self.artifacts,
            "verified": verified,
            "runtime_state": self.to_prompt_dict(),
            "requirements": self.requirements,
        }

    def _record_artifact(self, path: str, verified: bool) -> None:
        for artifact in self.artifacts:
            if artifact.get("path") == path:
                artifact["verified"] = bool(artifact.get("verified") or verified)
                return
        self.artifacts.append({"path": path, "verified": bool(verified)})


def _artifact_path_from_command(cmd: str) -> str:
    match = _EXPLICIT_PATH_ARG_RE.search(cmd)
    if match:
        return match.group("path").strip()
    match = _TEST_PATH_ARG_RE.search(cmd)
    if match:
        return match.group("path").strip()
    return ""


def _path_from_tool_text(text: str) -> str:
    first_line = text.splitlines()[0] if text else ""
    if first_line.startswith("File: "):
        return first_line.removeprefix("File: ").strip()
    return ""


def _generated_image_paths(text: str) -> list[str]:
    lines = str(text or "").splitlines()
    paths: list[str] = []
    in_files = False
    for raw_line in lines:
        line = raw_line.strip()
        if line == "Files:":
            in_files = True
            continue
        if not in_files:
            continue
        if not line:
            continue
        if re.match(r"^[A-Za-z][A-Za-z ]+:", line) and not re.match(r"^[A-Za-z]:[\\/]", line):
            break
        if re.search(r"\.(png|jpe?g|webp|bmp)$", line, re.IGNORECASE):
            paths.append(line)
    return paths


def _source_summary(text: str) -> str:
    for line in text.splitlines():
        if "sources fetched:" in line.lower():
            return line.strip()
    return text[:500]


def _web_research_source_record(args: dict, text: str) -> dict[str, Any]:
    fetched = _sources_fetched_count(text)
    min_sources = _int_or_none(args.get("min_sources"))
    urls = _dedupe_preserve_order(
        url.rstrip(".,;")
        for url in _URL_RE.findall(text)
        if not _looks_like_search_engine_noise(url)
    )
    lowered = text.lower()
    weak = (
        "no readable sources could be fetched" in lowered
        or "fetch failures:" in lowered
        or bool(re.search(r"(?im)^\s*only\s+\d+\s+readable source", text))
    )
    if min_sources is not None and fetched is not None:
        weak = weak or fetched < min_sources
    record: dict[str, Any] = {
        "query": str(args.get("query") or ""),
        "min_sources": min_sources,
        "summary": _source_summary(text),
    }
    if fetched is not None:
        record["sources_fetched"] = fetched
    if urls:
        record["urls"] = urls
    record["weak"] = bool(weak)
    return record


def _sources_fetched_count(text: str) -> int | None:
    match = re.search(r"(?im)^\s*sources fetched:\s*(\d+)\s*$", text)
    return int(match.group(1)) if match else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _looks_like_search_engine_noise(url: str) -> bool:
    lowered = url.lower()
    return "duckduckgo.com/y.js" in lowered or "bing.com/aclick" in lowered

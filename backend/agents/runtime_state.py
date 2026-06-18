"""Structured per-turn evidence gathered while tools run."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_PATH_ARG_RE = re.compile(
    r"(?:-Path\s+|-LiteralPath\s+|Test-Path\s+|Get-Item\s+)(['\"]?)(?P<path>[^'\"\r\n]+?)\1(?:\s|$)",
    re.IGNORECASE,
)


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
        }
        self.actions.append(action)
        self.evidence_items.append({
            "tool": tool,
            "ok": bool(ok),
            "text": text,
            "artifact_verified": bool(artifact_verified),
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
            self.sources.append({
                "query": str(args.get("query") or ""),
                "min_sources": args.get("min_sources"),
                "summary": _source_summary(text),
            })
        elif tool == "fetch_url":
            self.sources.append({
                "url": str(args.get("url") or ""),
                "summary": text[:500],
            })

    def record_error(self, message: str, tool: str | None = None, args: dict | None = None) -> None:
        error = {"error": str(message)}
        if tool:
            error["tool"] = tool
        if args:
            error["args"] = dict(args)
        self.errors.append(error)

    def set_contract_result(self, contract, result) -> None:
        self.requirements = {
            "intent": contract.intent,
            "satisfied": bool(result.satisfied),
            "missing": list(result.missing),
            "final_answer_requirements": result.final_answer_requirements,
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
    match = _PATH_ARG_RE.search(cmd)
    if match:
        return match.group("path").strip()
    return ""


def _path_from_tool_text(text: str) -> str:
    first_line = text.splitlines()[0] if text else ""
    if first_line.startswith("File: "):
        return first_line.removeprefix("File: ").strip()
    return ""


def _source_summary(text: str) -> str:
    for line in text.splitlines():
        if "sources fetched:" in line.lower():
            return line.strip()
    return text[:500]

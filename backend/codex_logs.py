from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MAX_SUMMARY_CHARS = 2000
MAX_TOOL_CALLS = 25


def default_codex_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def find_codex_log(
    codex_session_id: str | None,
    roots: list[Path] | None = None,
) -> Path | None:
    if not codex_session_id:
        return None

    search_roots = roots or [default_codex_sessions_root()]
    for root in search_roots:
        if not root.exists():
            continue
        matches = sorted(root.glob(f"**/*{codex_session_id}.jsonl"))
        if matches:
            return matches[-1]
    return None


def summarize_codex_session(codex_session_id: str | None) -> dict[str, Any] | None:
    log_path = find_codex_log(codex_session_id)
    if not log_path:
        return None
    return summarize_codex_log(log_path)


def summarize_codex_log(log_path: Path) -> dict[str, Any]:
    session_id = ""
    prompt = ""
    final_summary = ""
    error_summary = ""
    tool_calls: list[dict[str, Any]] = []
    shell_calls = 0
    mcp_calls = 0

    for row in _iter_jsonl(log_path):
        payload = row.get("payload") or {}
        row_type = row.get("type")
        payload_type = payload.get("type")

        if row_type == "session_meta":
            session_id = str(payload.get("id") or session_id)
            continue

        if payload_type == "user_message" and not prompt:
            prompt = str(payload.get("message") or "")
            continue

        if payload_type == "function_call":
            name = str(payload.get("name") or "")
            namespace = str(payload.get("namespace") or "")
            arguments = payload.get("arguments")
            if name == "shell_command":
                shell_calls += 1
            if namespace.startswith("mcp__") or "github" in namespace.lower():
                mcp_calls += 1
            if len(tool_calls) < MAX_TOOL_CALLS:
                tool_calls.append(
                    {
                        "name": name,
                        "namespace": namespace,
                        "arguments": _compact(arguments),
                    }
                )
            continue

        if payload_type == "mcp_tool_call_end":
            invocation = payload.get("invocation") or {}
            if not any(
                call.get("name") == invocation.get("tool")
                for call in tool_calls
            ):
                mcp_calls += 1
            continue

        if payload_type == "agent_message" and payload.get("phase") == "final_answer":
            final_summary = str(payload.get("message") or "")[:MAX_SUMMARY_CHARS]
            continue

        if payload_type in {"error", "thread.error", "turn.failed"}:
            error_summary = str(payload.get("message") or payload.get("error") or payload_type)

        if payload_type == "function_call_output":
            output = str(payload.get("output") or "")
            if "execution error" in output.lower() and not error_summary:
                error_summary = output[:MAX_SUMMARY_CHARS]

    if not session_id:
        session_id = _session_id_from_filename(log_path)

    return {
        "codex_session_id": session_id,
        "codex_log_path": str(log_path),
        "codex_prompt": prompt,
        "codex_tool_call_count": len(tool_calls),
        "codex_shell_call_count": shell_calls,
        "codex_mcp_call_count": mcp_calls,
        "codex_tool_calls": tool_calls,
        "codex_final_summary": final_summary,
        "codex_error_summary": error_summary,
    }


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _compact(value: Any) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:MAX_SUMMARY_CHARS]


def _session_id_from_filename(path: Path) -> str:
    stem = path.stem
    parts = stem.split("-")
    if len(parts) >= 5:
        return "-".join(parts[-5:])
    return stem

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"


def _candidate_paths(path: str | None) -> list[Path]:
    raw = Path(path or os.getcwd())
    if raw.is_absolute():
        return [raw]
    return [
        Path.cwd() / raw,
        BACKEND_ROOT / raw,
        PROJECT_ROOT / raw,
    ]


def resolve_path(path: str | None) -> Path:
    for candidate in _candidate_paths(path):
        if candidate.exists():
            return candidate.resolve()
    return _candidate_paths(path)[0].resolve()


def list_dir(path: str | None = None) -> dict[str, Any]:
    target = resolve_path(path)
    if not target.is_dir():
        raise NotADirectoryError(str(target))

    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "type": "directory" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            }
        )
    return {"path": str(target), "entries": entries}


def read_file(path: str) -> dict[str, str]:
    target = resolve_path(path)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    return {"path": str(target), "text": target.read_text(encoding="utf-8", errors="replace")}


def write_file(path: str, content: str, overwrite: bool = False, cwd: str | None = None) -> dict[str, Any]:
    """Write ``content`` to a text file and verify the write.

    First-class file output for the agent: the eval (2026-07-02) showed that
    requiring file output while confirmation-gating EVERY shell write command
    (Set-Content/Out-File/>) made research-to-file impossible to complete
    autonomously — the system demanded what its safety layer forbade. write_file
    resolves that: creating a NEW file is a normal, verifiable act; overwriting
    requires the explicit ``overwrite`` flag (which the registry confirmation-
    gates), and path traversal/absolute targets are gated at the registry level.

    Refuses to replace an existing file unless ``overwrite`` is set — the refusal
    message tells the model exactly what its options are. Returns the resolved
    path, byte count and a verified flag (existence re-checked after writing).
    """
    raw = Path(path)
    if not raw.is_absolute():
        raw = Path(cwd) / raw if cwd else Path.cwd() / raw
    target = raw.resolve()
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"{target} already exists. Pass overwrite=true to replace it, or "
            "choose a different filename."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    data = str(content or "")
    target.write_text(data, encoding="utf-8")
    verified = target.is_file() and target.stat().st_size >= len(data.encode("utf-8")) - 8
    return {
        "path": str(target),
        "bytes": target.stat().st_size,
        "verified": bool(verified),
    }


def find_file(name: str, root: str | None = None) -> dict[str, Any]:
    search_root = resolve_path(root or str(PROJECT_ROOT))
    if not search_root.is_dir():
        raise NotADirectoryError(str(search_root))

    matches = []
    lowered = name.lower()
    for dirpath, dirnames, filenames in os.walk(search_root):
        dirnames[:] = [d for d in dirnames if d not in {".git", ".next", "node_modules", ".venv", "__pycache__"}]
        for filename in filenames:
            if filename.lower() == lowered:
                matches.append(str(Path(dirpath) / filename))
    return {"root": str(search_root), "matches": matches}


def _get_all_windows():
    import pygetwindow

    return pygetwindow.getAllWindows()


def active_window_title() -> str:
    import pygetwindow

    window = pygetwindow.getActiveWindow()
    if not window:
        return ""
    return getattr(window, "title", "") or ""


def list_windows() -> dict[str, Any]:
    windows = []
    for window in _get_all_windows():
        title = getattr(window, "title", "") or ""
        if title.strip():
            windows.append({"title": title})
    return {"windows": windows}


def focus_window(title: str) -> dict[str, str]:
    needle = title.lower()
    for window in _get_all_windows():
        window_title = getattr(window, "title", "") or ""
        if needle in window_title.lower():
            window.activate()
            return {"focused": window_title}
    raise ValueError(f"No window matching: {title}")

"""Robust, home-rooted file search.

The original find_file is exact-match-only and rooted in the Pilot repo, so it
can never find e.g. a CV in the user's Downloads (session e251136a). This does
substring OR glob matching, defaults to the user's home directory (accepts a
folder name like "Downloads" as a shortcut), and returns each hit's last-
modified time and size — directly answering "find my CV and when it changed".
Heavy/system folders are pruned and the walk is time-bounded so it stays snappy.
"""

from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path
from typing import Any

_SKIP_DIRS = {
    ".git", ".next", "node_modules", ".venv", "__pycache__", "$Recycle.Bin",
    "AppData", "Application Data", "Local Settings", ".cache", ".npm", ".gradle",
    "Temp", "Temporary Internet Files",
}
_SCAN_DEADLINE_SECONDS = 8.0


def _resolve_root(root: str | None) -> Path:
    home = Path.home()
    if not root:
        return home
    raw = Path(root).expanduser()
    if raw.is_dir():
        return raw
    # Treat a bare name ("Downloads") as a folder under the home directory.
    candidate = home / root
    return candidate if candidate.is_dir() else home


def search_files(query: str, root: str | None = None, limit: int = 40) -> dict[str, Any]:
    base = _resolve_root(root)
    needle = query.lower()
    is_glob = any(ch in query for ch in "*?[")
    matches: list[dict[str, Any]] = []
    deadline = time.time() + _SCAN_DEADLINE_SECONDS

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not (d.startswith(".") and d != ".")
        ]
        for name in filenames:
            hit = fnmatch.fnmatch(name.lower(), needle) if is_glob else needle in name.lower()
            if not hit:
                continue
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            matches.append({
                "path": str(path),
                "size": st.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            })
            if len(matches) >= limit:
                return {"root": str(base), "matches": matches, "truncated": True}
        if time.time() > deadline:
            return {"root": str(base), "matches": matches, "truncated": True, "timed_out": True}

    return {"root": str(base), "matches": matches, "truncated": False}

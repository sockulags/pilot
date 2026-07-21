"""Additional grounded tools: content search, HTTP APIs, documents, processes,
clipboard.

These fill gaps the eval/README motivate but the original tool set could not
cover: locating *where* behaviour lives in a codebase (content search, not just
filename search), calling JSON APIs (weather/current-info), reading PDFs the
research/"find my CV" flows land on, and light OS grounding (processes,
clipboard). Every function here is synchronous and side-effect-light; the agent
loop wraps the blocking ones in ``asyncio.to_thread``.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from net_guard import guard_request, pinned_transport

# Directories never worth walking for a content search (mirrors search.py).
_SKIP_DIRS = {
    ".git", ".next", "node_modules", ".venv", "__pycache__", "$Recycle.Bin",
    "AppData", "Application Data", "Local Settings", ".cache", ".npm", ".gradle",
    "Temp", "Temporary Internet Files", "dist", "build", ".mypy_cache",
    ".pytest_cache", ".ruff_cache",
}
# Extensions we treat as searchable text (skip binaries/media by default).
_TEXT_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".toml",
    ".yaml", ".yml", ".cfg", ".ini", ".css", ".scss", ".html", ".xml", ".sh",
    ".ps1", ".bat", ".env", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".cs",
    ".rb", ".php", ".sql", ".vue", ".svelte", ".gitignore", ".dockerfile",
}
_CONTENT_SCAN_DEADLINE_SECONDS = 8.0
_MAX_LINE_LEN = 400


def _resolve_dir(root: str | None) -> Path | None:
    """Resolve a search root: an explicit dir, a bare name under home, else home."""
    home = Path.home()
    if not root:
        return home
    raw = Path(root).expanduser()
    if raw.is_dir():
        return raw
    candidate = home / root
    if candidate.is_dir():
        return candidate
    return None


# --------------------------------------------------------------------------- #
# Content search (grep)
# --------------------------------------------------------------------------- #


def search_in_files(
    pattern: str,
    root: str | None = None,
    glob: str | None = None,
    *,
    regex: bool = False,
    ignore_case: bool = True,
    limit: int = 60,
) -> dict[str, Any]:
    """Search file CONTENTS under ``root`` for ``pattern``.

    Returns each hit's file path, 1-indexed line number and the matching line.
    ``glob`` restricts which files are scanned (e.g. ``*.py``); by default only
    known text extensions are read. This is the "where does X live" tool that
    filename search cannot answer.
    """
    base = _resolve_dir(root)
    if not base:
        return {"root": str(root or ""), "matches": [], "error": "root not found"}
    try:
        matcher = _build_matcher(pattern, regex, ignore_case)
    except re.error as exc:
        return {"root": str(base), "matches": [], "error": f"bad regex: {exc}"}

    matches: list[dict[str, Any]] = []
    deadline = time.time() + _CONTENT_SCAN_DEADLINE_SECONDS
    truncated = False
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not (d.startswith(".") and d != ".")
        ]
        for name in filenames:
            if glob and not fnmatch.fnmatch(name.lower(), glob.lower()):
                continue
            if not glob and not _looks_textual(name):
                continue
            path = Path(dirpath) / name
            for lineno, line in _grep_file(path, matcher):
                matches.append({
                    "path": str(path),
                    "line": lineno,
                    "text": line[:_MAX_LINE_LEN],
                })
                if len(matches) >= limit:
                    return {"root": str(base), "matches": matches, "truncated": True}
            if time.time() > deadline:
                truncated = True
                break
        if truncated:
            break
    return {"root": str(base), "matches": matches, "truncated": truncated}


def _build_matcher(pattern: str, regex: bool, ignore_case: bool):
    flags = re.IGNORECASE if ignore_case else 0
    if regex:
        rx = re.compile(pattern, flags)
        return lambda line: bool(rx.search(line))
    needle = pattern.lower() if ignore_case else pattern
    if ignore_case:
        return lambda line: needle in line.lower()
    return lambda line: needle in line


def _looks_textual(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in _TEXT_EXTS or name.lower() in _TEXT_EXTS


def _grep_file(path: Path, matcher):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, start=1):
                if matcher(line):
                    yield i, line.rstrip("\n")
    except (OSError, ValueError):
        return


# --------------------------------------------------------------------------- #
# HTTP request (structured API calls)
# --------------------------------------------------------------------------- #

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
_HTTP_MAX_CHARS = 8000


def http_request(
    url: str,
    method: str = "GET",
    *,
    headers: dict | None = None,
    json_body: Any = None,
    params: dict | None = None,
    timeout: float = 20.0,
    max_chars: int = _HTTP_MAX_CHARS,
) -> dict[str, Any]:
    """Call a JSON/HTTP API and return status, headers and body.

    Unlike fetch_url (which returns readable page text), this speaks to APIs:
    arbitrary method, JSON body, custom headers, query params. The body is
    returned parsed when the response is JSON, else as (truncated) text.
    """
    method = str(method or "GET").upper()
    if method not in _ALLOWED_METHODS:
        return {"error": f"unsupported method {method!r}"}
    if not re.match(r"^https?://", url or "", re.IGNORECASE):
        return {"error": "url must be http(s)"}
    try:
        # guard_request runs before the initial request AND on every redirect
        # hop, so a public URL that 302s to an internal address is blocked too.
        # The pinning transport then dials the exact validated IP for each hop,
        # closing the DNS-rebinding window between check and connect.
        with httpx.Client(
            timeout=timeout, follow_redirects=True,
            event_hooks={"request": [guard_request]},
            transport=pinned_transport(),
        ) as client:
            resp = client.request(
                method, url, headers=headers or None,
                json=json_body if json_body is not None else None,
                params=params or None,
            )
    except Exception as exc:  # noqa: BLE001 — network result, not a crash
        return {"error": f"{type(exc).__name__}: {exc}", "url": url}

    ctype = resp.headers.get("content-type", "")
    out: dict[str, Any] = {
        "url": str(resp.url),
        "status": resp.status_code,
        "ok": resp.is_success,
        "content_type": ctype,
    }
    if "application/json" in ctype:
        try:
            out["json"] = resp.json()
            return out
        except (json.JSONDecodeError, ValueError):
            pass
    text = resp.text or ""
    out["text"] = text[:max_chars]
    if len(text) > max_chars:
        out["truncated"] = True
    return out


# --------------------------------------------------------------------------- #
# Document text extraction (PDF + plain text)
# --------------------------------------------------------------------------- #

_DOC_MAX_CHARS = 20000


def read_document(path: str, max_chars: int = _DOC_MAX_CHARS) -> dict[str, Any]:
    """Extract readable text from a document (PDF or text-like file).

    PDFs are parsed page by page via pypdf; other files fall back to a UTF-8
    text read. The research and 'find my CV' flows routinely land on PDFs, which
    read_file (plain text) mangles — this returns their actual text.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return {"path": str(p), "error": "file not found"}
    ext = p.suffix.lower()
    if ext == ".pdf":
        return _read_pdf(p, max_chars)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"path": str(p), "error": f"{type(exc).__name__}: {exc}"}
    return _clip_doc(str(p), text, max_chars)


def _read_pdf(p: Path, max_chars: int) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"path": str(p), "error": "pypdf is not installed; cannot read PDFs"}
    try:
        reader = PdfReader(str(p))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 — malformed/encrypted PDFs
        return {"path": str(p), "error": f"could not read PDF: {exc}"}
    text = "\n\n".join(pages).strip()
    result = _clip_doc(str(p), text, max_chars)
    result["pages"] = len(pages)
    return result


def _clip_doc(path: str, text: str, max_chars: int) -> dict[str, Any]:
    clipped = text[:max_chars]
    return {
        "path": path,
        "text": clipped,
        "chars": len(text),
        "truncated": len(text) > max_chars,
    }


# --------------------------------------------------------------------------- #
# Process listing (read-only OS grounding)
# --------------------------------------------------------------------------- #


def list_processes(filter_name: str | None = None, limit: int = 40) -> dict[str, Any]:
    """List running processes (name, pid, memory). Read-only.

    Uses tasklist on Windows / ps on POSIX — no extra dependency. ``filter_name``
    keeps only processes whose image name contains the substring.
    """
    try:
        if os.name == "nt":
            rows = _list_processes_windows()
        else:
            rows = _list_processes_posix()
    except Exception as exc:  # noqa: BLE001
        return {"processes": [], "error": f"{type(exc).__name__}: {exc}"}
    if filter_name:
        needle = filter_name.lower()
        rows = [r for r in rows if needle in r["name"].lower()]
    rows.sort(key=lambda r: r.get("memory_kb") or 0, reverse=True)
    return {"processes": rows[:limit], "total": len(rows)}


def _list_processes_windows() -> list[dict]:
    out = subprocess.run(
        ["tasklist", "/fo", "csv", "/nh"],
        capture_output=True, timeout=15,
    )
    text = (out.stdout or b"").decode("utf-8", errors="replace")
    rows: list[dict] = []
    for line in text.splitlines():
        parts = [c.strip('"') for c in line.split('","')]
        if len(parts) < 5:
            continue
        name, pid_s, _session, _snum, mem = parts[0], parts[1], parts[2], parts[3], parts[4]
        mem_kb = _parse_mem_kb(mem)
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        rows.append({"name": name.strip('"'), "pid": pid, "memory_kb": mem_kb})
    return rows


def _list_processes_posix() -> list[dict]:
    out = subprocess.run(
        ["ps", "-eo", "pid,rss,comm"],
        capture_output=True, timeout=15,
    )
    text = (out.stdout or b"").decode("utf-8", errors="replace")
    rows: list[dict] = []
    for line in text.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, rss, comm = parts
        try:
            rows.append({"name": comm, "pid": int(pid_s), "memory_kb": int(rss)})
        except ValueError:
            continue
    return rows


def _parse_mem_kb(mem: str) -> int | None:
    digits = re.sub(r"[^\d]", "", mem)
    return int(digits) if digits else None


# --------------------------------------------------------------------------- #
# Clipboard
# --------------------------------------------------------------------------- #


def read_clipboard() -> dict[str, Any]:
    """Return the current clipboard text (or an error if unavailable)."""
    try:
        import pyperclip
        return {"text": pyperclip.paste() or ""}
    except Exception as exc:  # noqa: BLE001 — headless / no clipboard backend
        return {"error": f"clipboard unavailable: {exc}"}


def write_clipboard(text: str) -> dict[str, Any]:
    """Copy ``text`` to the clipboard."""
    try:
        import pyperclip
        pyperclip.copy(str(text or ""))
        return {"ok": True, "chars": len(str(text or ""))}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"clipboard unavailable: {exc}"}

"""Project instruction files — the "house rules" layer for the selected repo.

A repo the user explicitly opens may ship agent-instruction files (``AGENTS.md``,
``CLAUDE.md``, ``.claude/CLAUDE.md``) at its root and inside subdirectories. These
are first-party config from the user's OWN project — trusted guidance, exactly
like the bundled ``skills/*.md`` "how" layer — and the downstream coding agents
(Claude Code / Codex) already obey them. This module gives the LOCAL coordinator
and the prompt-refinement gateway the same shared instruction layer.

Discovery is layered by proximity: root files are the base; nested files that sit
closer to the files being read/edited this turn override/append (more-specific
instructions win for their subtree). The block is rendered with each file's
relative path so precedence is visible, and bounded in size so prompt growth is
capped. Everything degrades gracefully to "" — unreadable files are skipped and
nothing raises.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The instruction filenames we look for. "CLAUDE.md" / "AGENTS.md" are the
# standard agent-instruction filenames; ".claude/CLAUDE.md" is the nested
# convention some repos use at the root.
INSTRUCTION_FILENAMES = ("AGENTS.md", "CLAUDE.md")

# Bounds so a verbose repo can't blow up the prompt.
MAX_BLOCK_CHARS = 4000
MAX_PER_FILE_CHARS = 2000
# Directories we never descend into when hunting for nested instruction files.
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
# How deep below the root we look for nested instruction files.
_MAX_DEPTH = 6


@dataclass
class InstructionFile:
    """One discovered instruction file.

    ``rel_path`` is relative to the project root (shown in the block so
    precedence is visible). ``depth`` is the directory depth below the root (0 =
    root); deeper files are more specific and take precedence. ``proximity`` is
    how close the file's directory is to the edited paths (higher = closer);
    used to order nested files so the most relevant ones come last.
    """

    path: str
    rel_path: str
    depth: int
    text: str
    proximity: int = 0


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as exc:  # noqa: BLE001 — robustness: never crash on a bad file
        logger.warning("could not read instruction file %s: %s", path, exc)
        return ""


def _root_files(cwd: str) -> list[InstructionFile]:
    found: list[InstructionFile] = []
    for name in INSTRUCTION_FILENAMES:
        path = os.path.join(cwd, name)
        if os.path.isfile(path):
            text = _read_text(path)
            if text.strip():
                found.append(InstructionFile(path=path, rel_path=name, depth=0, text=text))
    # ".claude/CLAUDE.md" — the nested-root convention — counts as a root file.
    nested_root = os.path.join(cwd, ".claude", "CLAUDE.md")
    if os.path.isfile(nested_root):
        text = _read_text(nested_root)
        if text.strip():
            found.append(
                InstructionFile(
                    path=nested_root,
                    rel_path=os.path.join(".claude", "CLAUDE.md"),
                    depth=0,
                    text=text,
                )
            )
    return found


def _nested_files(cwd: str) -> list[InstructionFile]:
    """All AGENTS.md/CLAUDE.md below the root (excluding root-level ones)."""
    found: list[InstructionFile] = []
    # Walk the realpath'd base so dirpath/path share the SAME representation as
    # ``base``; otherwise a short-form cwd (e.g. Windows 8.3 "RUNNER~1" temp dirs
    # on CI) walked directly yields paths that os.path.relpath cannot relate to
    # the realpath'd base, producing "..\..\.." garbage rel_paths.
    base = os.path.realpath(cwd)
    for dirpath, dirnames, filenames in os.walk(base):
        # Prune noisy/irrelevant trees in-place.
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, base)
        if rel_dir == ".":
            continue  # root files handled separately
        depth = rel_dir.count(os.sep) + 1
        if depth > _MAX_DEPTH:
            dirnames[:] = []
            continue
        for name in INSTRUCTION_FILENAMES:
            if name in filenames:
                path = os.path.join(dirpath, name)
                text = _read_text(path)
                if text.strip():
                    found.append(
                        InstructionFile(
                            path=path,
                            rel_path=os.path.relpath(path, base),
                            depth=depth,
                            text=text,
                        )
                    )
    return found


def _proximity(instr_dir: str, edited_dirs: list[str]) -> int:
    """How many path components the instruction's directory shares with an edited
    path's directory (best over all edited paths). Higher = closer = wins later.
    """
    best = 0
    instr_parts = os.path.normpath(instr_dir).split(os.sep)
    for ed in edited_dirs:
        ed_parts = os.path.normpath(ed).split(os.sep)
        shared = 0
        for a, b in zip(instr_parts, ed_parts):
            if a == b:
                shared += 1
            else:
                break
        best = max(best, shared)
    return best


def discover_instruction_files(
    cwd: str | None, edited_paths: list[str] | None = None
) -> list[InstructionFile]:
    """Discover and order instruction files for the selected project ``cwd``.

    Root files come first (the base). Nested files are included only when
    ``edited_paths`` point into their subtree (so we don't dump every nested rule
    into every turn); they are ordered so the most-specific / closest file comes
    LAST, giving it precedence. With no edited paths, only root files are used.
    """
    if not cwd or not os.path.isdir(cwd):
        return []

    root = _root_files(cwd)

    edited_paths = edited_paths or []
    if not edited_paths:
        return root

    base = os.path.realpath(cwd)
    edited_dirs: list[str] = []
    for p in edited_paths:
        ap = p if os.path.isabs(p) else os.path.join(cwd, p)
        ap = os.path.realpath(ap)
        edited_dirs.append(os.path.dirname(ap) if os.path.splitext(ap)[1] else ap)

    nested: list[InstructionFile] = []
    for f in _nested_files(cwd):
        instr_dir = os.path.dirname(os.path.realpath(f.path))
        # Keep a nested file only when an edited path lives in its subtree.
        in_subtree = any(
            ed == instr_dir or ed.startswith(instr_dir + os.sep) for ed in edited_dirs
        )
        if not in_subtree:
            continue
        f.proximity = _proximity(os.path.relpath(instr_dir, base), edited_dirs)
        nested.append(f)

    # Order nested by depth then proximity so the deepest / closest file is last
    # (and therefore presented as overriding the broader ones).
    nested.sort(key=lambda f: (f.depth, f.proximity))
    return root + nested


def build_instruction_block(
    cwd: str | None,
    edited_paths: list[str] | None = None,
    max_chars: int = MAX_BLOCK_CHARS,
) -> str:
    """Render the layered instruction files as one bounded, trusted block (or "").

    Each file is labelled with its relative path so precedence is visible; the
    block is truncated to ``max_chars`` total. Returns "" when there are no
    instruction files (or no readable cwd).
    """
    files = discover_instruction_files(cwd, edited_paths)
    if not files:
        return ""

    header = (
        "Project instructions (from AGENTS.md / CLAUDE.md — follow these for this "
        "project; more-specific entries below override the earlier ones):\n"
    )
    parts: list[str] = []
    for f in files:
        body = f.text.strip()
        if len(body) > MAX_PER_FILE_CHARS:
            body = body[:MAX_PER_FILE_CHARS].rstrip() + "\n[...truncated...]"
        parts.append(f"--- {f.rel_path} ---\n{body}")

    block = header + "\n\n".join(parts)
    if len(block) > max_chars:
        block = block[:max_chars].rstrip() + "\n[...truncated...]"
    return block


# --- Documented command extraction (for verification) -----------------------

_COMMAND_SECTIONS = {
    "test": ("test", "tests", "testing", "running tests"),
    "setup": ("setup", "install", "installation", "getting started", "development"),
}


def _first_fenced_command(lines: list[str], start: int) -> str:
    """Return the first command inside a code fence that belongs to THIS section.

    Stops at the next ``#`` heading: a section documented in prose with no fenced
    command must not borrow an UNRELATED later section's command (review
    2026-07-04 — a prose-only ``## Testing`` followed by ``## Release`` would
    otherwise yield the release command as the test command)."""
    in_fence = False
    for line in lines[start:]:
        stripped = line.strip()
        if not in_fence and stripped.startswith("#"):
            break  # reached the next section without finding a fence — give up
        if stripped.startswith("```"):
            if in_fence:
                break
            in_fence = True
            continue
        if in_fence and stripped:
            # Drop a leading shell prompt marker if present.
            return stripped[1:].strip() if stripped.startswith("$") else stripped
    return ""


def extract_documented_commands(cwd: str | None) -> dict:
    """Pull obvious documented test/setup commands from AGENTS.md/CLAUDE.md.

    Looks for a ``## Test`` / ``## Setup`` (etc.) heading and takes the first
    command in the following code fence. Best-effort and read-only — it NEVER
    runs anything; it only surfaces what the repo documented. Returns e.g.
    ``{"test": "pytest -q", "setup": "uv sync"}`` (only keys it finds).
    """
    result: dict[str, str] = {}
    if not cwd or not os.path.isdir(cwd):
        return result

    candidates = [os.path.join(cwd, n) for n in INSTRUCTION_FILENAMES]
    candidates.append(os.path.join(cwd, ".claude", "CLAUDE.md"))
    for path in candidates:
        if not os.path.isfile(path):
            continue
        text = _read_text(path)
        if not text:
            continue
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            heading = stripped.lstrip("#").strip().lower()
            for kind, keywords in _COMMAND_SECTIONS.items():
                if kind in result:
                    continue
                if heading in keywords or any(heading.startswith(k) for k in keywords):
                    cmd = _first_fenced_command(lines, idx + 1)
                    if cmd:
                        result[kind] = cmd
    return result

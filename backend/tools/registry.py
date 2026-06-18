"""Single source of truth for the tools the assistant can use.

Every tool is described ONCE here (name, schema, when-to-use, behaviour flags).
From this registry we generate everything that used to be hand-maintained in
four separate places and drifted apart:

- the coordinator's decision menu + allowlist (``agents/coordinator.py``),
- the agent loop's behaviour sets — streaming / observe-after / deterministic
  (``agents/loop.py``),
- the Pilot MCP server's tool manifest (``api/mcp.py``),
- the always-on **capability manifest** the model reads so it actually knows
  what it can do (so "list your tools" stops returning "I have none"), and
- function-call schemas for native tool-calling (Fas B).

Adding a tool = adding one ``ToolSpec`` here; all surfaces pick it up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    """One tool, described once. See module docstring for how it's consumed."""

    name: str
    summary: str  # one short line for the user-facing capability manifest
    description: str  # fuller line for the model's decision menu / MCP manifest
    when_to_use: str  # the "skill" hint: WHEN to reach for this tool
    params: dict[str, dict] = field(default_factory=dict)  # JSON-schema properties
    required: tuple[str, ...] = ()
    category: str = "general"

    # Behaviour flags (drive the agent loop + safety gating):
    unsafe: bool = False  # subject to safety review before running
    streaming: bool = False  # handler streams output (run_command, run_codex)
    desktop: bool = False  # a GUI action (click/type/...) — needs a focused target
    observe_after: bool = False  # re-perceive the screen after running
    deterministic: bool = False  # result directly answers; the loop may stop
    needs_perception: bool = False  # requires a prior screen perception
    risk_level: str = "low"  # low|medium|high
    side_effects: bool = False  # whether the tool can alter local/external state

    # Which surfaces expose this tool:
    coordinator: bool = True  # offered to the in-turn coordinator (front brain)
    mcp_facing: bool = False  # exposed via the Pilot MCP server
    mcp_name: str | None = None  # MCP tool name override (default: pilot_<name>)


# ---------------------------------------------------------------------------
# The registry. Order here is the order shown in menus.
# ---------------------------------------------------------------------------

REGISTRY: tuple[ToolSpec, ...] = (
    # --- Files ---------------------------------------------------------------
    ToolSpec(
        name="read_file",
        summary="Read a text file's contents",
        description="read_file(path): return the text content of a file",
        when_to_use="To answer questions about a file's contents, or to understand "
        "a project (read its README / package.json / pyproject.toml).",
        params={"path": {"type": "string", "description": "File path to read"}},
        required=("path",),
        category="files",
        deterministic=True,
        mcp_facing=True,
    ),
    ToolSpec(
        name="list_dir",
        summary="List files and folders in a directory",
        description="list_dir(path?): list the entries of a directory (defaults to cwd)",
        when_to_use="To see what's in a folder or project before acting.",
        params={"path": {"type": "string", "description": "Directory to list (optional)"}},
        category="files",
        deterministic=True,
        mcp_facing=True,
    ),
    ToolSpec(
        name="find_file",
        summary="Find files by name",
        description="find_file(name, root?): search a folder tree for files matching name",
        when_to_use="To locate a file when you don't know its exact folder "
        "(e.g. find a CV in the user's home directory).",
        params={
            "name": {"type": "string", "description": "Filename or pattern to find"},
            "root": {"type": "string", "description": "Folder to search under (optional)"},
        },
        required=("name",),
        category="files",
        deterministic=True,
        mcp_facing=True,
    ),
    # --- Shell ---------------------------------------------------------------
    ToolSpec(
        name="run_command",
        summary="Run a shell command and read its output",
        description="run_command(cmd, cwd?): run a shell command, stream its output",
        when_to_use="For quick one-off commands and CLI tools (e.g. gh, git, dir). "
        "This is a Windows machine — use Windows/PowerShell commands, not 'pwd'.",
        params={
            "cmd": {"type": "string", "description": "The command line to run"},
            "cwd": {"type": "string", "description": "Working directory (optional)"},
        },
        required=("cmd",),
        category="shell",
        unsafe=True,
        streaming=True,
        risk_level="medium",
        side_effects=True,
        mcp_facing=True,
    ),
    # --- Desktop windows -----------------------------------------------------
    ToolSpec(
        name="list_windows",
        summary="List open desktop windows",
        description="list_windows(): list the titles of visible desktop windows",
        when_to_use="To see which apps/windows are open before focusing or acting.",
        params={},
        category="desktop",
        deterministic=True,
        risk_level="medium",
        side_effects=True,
        mcp_facing=True,
    ),
    ToolSpec(
        name="focus_window",
        summary="Bring a window to the foreground",
        description="focus_window(title): focus the window whose title matches",
        when_to_use="To make a specific app active before typing or clicking into it.",
        params={"title": {"type": "string", "description": "Window title (substring)"}},
        required=("title",),
        category="desktop",
        deterministic=True,
        mcp_facing=True,
    ),
    ToolSpec(
        name="open_app",
        summary="Open an application",
        description="open_app(name): launch an application by name or path",
        when_to_use="To start an app that isn't open yet.",
        params={"name": {"type": "string", "description": "App name or path"}},
        required=("name",),
        category="desktop",
        observe_after=True,
        risk_level="medium",
        side_effects=True,
        mcp_facing=True,
    ),
    # --- Screen perception / pointer ----------------------------------------
    ToolSpec(
        name="screenshot",
        summary="Take a screenshot of the screen",
        description="screenshot(): capture the current screen",
        when_to_use="To see what is currently on screen.",
        params={},
        category="desktop",
        mcp_facing=True,
    ),
    ToolSpec(
        name="get_screen_size",
        summary="Get the screen resolution",
        description="get_screen_size(): return the screen width and height",
        when_to_use="When you need the screen dimensions to reason about coordinates.",
        params={},
        category="desktop",
    ),
    ToolSpec(
        name="click_element",
        summary="Click a numbered on-screen element",
        description="click_element(element_id, button?): click an element from the "
        "last screen perception by its id",
        when_to_use="The accurate way to click — perceive the screen first, then "
        "click the element by its id (no pixel guessing).",
        params={
            "element_id": {"type": "integer", "description": "Element id from perception"},
            "button": {"type": "string", "description": "left|right (default left)"},
        },
        required=("element_id",),
        category="desktop",
        desktop=True,
        observe_after=True,
        needs_perception=True,
        risk_level="medium",
        side_effects=True,
    ),
    ToolSpec(
        name="click",
        summary="Click at screen coordinates",
        description="click(x, y, button?): click at pixel coordinates",
        when_to_use="Only when no perceived element fits; prefer click_element.",
        params={
            "x": {"type": "integer", "description": "X pixel"},
            "y": {"type": "integer", "description": "Y pixel"},
            "button": {"type": "string", "description": "left|right (default left)"},
        },
        required=("x", "y"),
        category="desktop",
        desktop=True,
        observe_after=True,
        risk_level="medium",
        side_effects=True,
        mcp_facing=True,
        mcp_name="pilot_click",
    ),
    ToolSpec(
        name="type_text",
        summary="Type text with the keyboard",
        description="type_text(text): type text into the focused window",
        when_to_use="To enter text — focus the right window first.",
        params={
            "text": {"type": "string", "description": "Text to type"},
            "interval": {"type": "number", "description": "Per-key delay seconds (optional)"},
        },
        required=("text",),
        category="desktop",
        desktop=True,
        observe_after=True,
        risk_level="medium",
        side_effects=True,
        mcp_facing=True,
        mcp_name="pilot_type",
    ),
    ToolSpec(
        name="key_press",
        summary="Press a single key",
        description="key_press(key): press a keyboard key (e.g. enter, esc)",
        when_to_use="To press one key, like Enter to submit.",
        params={"key": {"type": "string", "description": "Key name"}},
        required=("key",),
        category="desktop",
        desktop=True,
        observe_after=True,
        risk_level="medium",
        side_effects=True,
    ),
    ToolSpec(
        name="hotkey",
        summary="Press a key combination",
        description="hotkey(keys): press a chord like ctrl+c",
        when_to_use="For keyboard shortcuts (copy, paste, switch tab).",
        params={"keys": {"type": "array", "items": {"type": "string"},
                          "description": "Keys, e.g. ['ctrl','c'] or 'ctrl+c'"}},
        required=("keys",),
        category="desktop",
        desktop=True,
        observe_after=True,
        risk_level="medium",
        side_effects=True,
    ),
    ToolSpec(
        name="scroll",
        summary="Scroll at a position",
        description="scroll(x, y, amount): scroll the wheel at a point",
        when_to_use="To reveal off-screen content before clicking.",
        params={
            "x": {"type": "integer", "description": "X pixel"},
            "y": {"type": "integer", "description": "Y pixel"},
            "amount": {"type": "integer", "description": "Scroll amount (+up/-down)"},
        },
        required=("x", "y", "amount"),
        category="desktop",
        desktop=True,
        observe_after=True,
        risk_level="medium",
        side_effects=True,
    ),
    ToolSpec(
        name="move_mouse",
        summary="Move the mouse pointer",
        description="move_mouse(x, y): move the pointer to coordinates",
        when_to_use="Rarely needed on its own; prefer click/click_element.",
        params={
            "x": {"type": "integer", "description": "X pixel"},
            "y": {"type": "integer", "description": "Y pixel"},
        },
        required=("x", "y"),
        category="desktop",
        desktop=True,
        observe_after=True,  # was in POST_ACTION_OBSERVE_TOOLS (a desktop action)
        coordinator=False,  # not exposed to the coordinator (matches prior allowlist)
        risk_level="medium",
        side_effects=True,
    ),
    # --- File search ---------------------------------------------------------
    ToolSpec(
        name="search_files",
        summary="Search for files by name under a folder",
        description="search_files(query, root?, limit?): substring/glob search for "
        "files, returning each hit's path, size and last-modified time",
        when_to_use="To locate a user's file (e.g. a CV in Downloads) and see when it "
        "last changed. Defaults to the home directory; pass root='Downloads' or a path "
        "to narrow it. Prefer this over find_file for anything outside the project.",
        params={
            "query": {"type": "string", "description": "Name substring or glob (e.g. cv, *.pdf)"},
            "root": {"type": "string", "description": "Folder to search (name or path, optional)"},
            "limit": {"type": "integer", "description": "Max matches (optional)"},
        },
        required=("query",),
        category="files",
        deterministic=True,
    ),
    # --- GitHub (gh CLI) -----------------------------------------------------
    ToolSpec(
        name="github_issues",
        summary="List a repo's GitHub issues",
        description="github_issues(repo, state?): list issues (open/closed/all) with a "
        "short description of each",
        when_to_use="When the user asks about issues in a GitHub repo. Pass repo as "
        "'owner/name' (e.g. 'sockulags/cv_builder'). Works without the code agent.",
        params={
            "repo": {"type": "string", "description": "owner/name or bare name"},
            "state": {"type": "string", "description": "open|closed|all (default open)"},
        },
        required=("repo",),
        category="github",
        deterministic=True,
    ),
    ToolSpec(
        name="github_prs",
        summary="List a repo's pull requests",
        description="github_prs(repo, state?): list pull requests with a short "
        "description of each",
        when_to_use="When the user asks about pull requests in a GitHub repo. Pass repo "
        "as 'owner/name'.",
        params={
            "repo": {"type": "string", "description": "owner/name or bare name"},
            "state": {"type": "string", "description": "open|closed|merged|all (default open)"},
        },
        required=("repo",),
        category="github",
        deterministic=True,
    ),
    ToolSpec(
        name="github_repo",
        summary="Show a GitHub repository overview",
        description="github_repo(repo): show a repository's description and details",
        when_to_use="When the user asks what a GitHub repo is or for an overview. Pass "
        "repo as 'owner/name'.",
        params={"repo": {"type": "string", "description": "owner/name or bare name"}},
        required=("repo",),
        category="github",
        deterministic=True,
    ),
    # --- Web -----------------------------------------------------------------
    ToolSpec(
        name="web_research",
        summary="Search the web and fetch readable sources",
        description="web_research(query?, task?, min_sources?): search, filter ads, "
        "fetch readable pages and return source excerpts",
        when_to_use="For web/news requests that ask for links, sources, citations or "
        "a summary of multiple sources. Use this instead of plain web_search when "
        "the user asks for sources.",
        params={
            "query": {"type": "string", "description": "Search query (optional if task is supplied)"},
            "task": {"type": "string", "description": "Original user task, used to infer query (optional)"},
            "min_sources": {"type": "integer", "description": "Minimum readable sources to fetch (optional)"},
        },
        category="web",
        deterministic=True,
    ),
    ToolSpec(
        name="web_search",
        summary="Search the web",
        description="web_search(query, max_results?): return the top web results "
        "(title, url, snippet)",
        when_to_use="For current or factual info you don't have — news, weather, "
        "look-ups. Follow up with fetch_url to read a result in full.",
        params={
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "How many results (optional)"},
        },
        required=("query",),
        category="web",
        deterministic=True,
    ),
    ToolSpec(
        name="fetch_url",
        summary="Fetch a web page as text",
        description="fetch_url(url): download a page and return its readable text",
        when_to_use="To read a specific page (often a web_search result) — e.g. a "
        "weather page or an article.",
        params={
            "url": {"type": "string", "description": "URL to fetch"},
            "max_chars": {"type": "integer", "description": "Max characters (optional)"},
        },
        required=("url",),
        category="web",
        deterministic=True,
    ),
    # --- Code agent (driven by the loop, not the coordinator) ----------------
    ToolSpec(
        name="run_codex",
        summary="Delegate to the coding agent",
        description="run_codex(prompt): hand a coding task to the external code agent",
        when_to_use="Only for real software work in a project, and only when the "
        "user explicitly wants to offload — otherwise stay local.",
        params={"prompt": {"type": "string", "description": "Instruction for the agent"}},
        required=("prompt",),
        category="code",
        streaming=True,
        coordinator=False,
        risk_level="high",
        side_effects=True,
    ),
)


_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in REGISTRY}

# External tools registered at runtime by the MCP client (tools/registry is the
# single surface, whether a tool is native or comes from an MCP server). Their
# names are namespaced with EXTERNAL_PREFIX so the loop can route execution.
EXTERNAL_PREFIX = "mcp__"
_EXTERNAL: list[ToolSpec] = []


def _all_specs() -> tuple[ToolSpec, ...]:
    return REGISTRY + tuple(_EXTERNAL)


def register_external(specs: list[ToolSpec]) -> None:
    """Add MCP-discovered tools to the registry (replacing any prior set)."""
    _EXTERNAL.clear()
    _EXTERNAL.extend(specs)


def clear_external() -> None:
    _EXTERNAL.clear()


def get(name: str) -> ToolSpec | None:
    spec = _BY_NAME.get(name)
    if spec:
        return spec
    return next((s for s in _EXTERNAL if s.name == name), None)


def confirmation_required(tool: str, args: dict | None = None) -> bool:
    spec = get(tool)
    if not spec:
        return True
    args = args or {}
    if tool == "run_command":
        return _command_requires_confirmation(str(args.get("cmd") or args.get("command") or ""))
    if tool == "read_file":
        return _path_requires_confirmation(str(args.get("path") or ""))
    return spec.risk_level == "high"


def risk_level_for(tool: str, args: dict | None = None) -> str:
    spec = get(tool)
    if not spec:
        return "high"
    if confirmation_required(tool, args):
        return "high"
    return spec.risk_level


def side_effects_for(tool: str) -> bool:
    spec = get(tool)
    return True if spec is None else bool(spec.side_effects)


def confirmation_reason(tool: str, args: dict | None = None) -> str:
    if tool == "run_command":
        return "High-risk shell command requires confirmation."
    return f"High-risk tool {tool!r} requires confirmation."


def _command_requires_confirmation(cmd: str) -> bool:
    lowered = f" {cmd.lower()} "
    high_risk_tokens = (
        " remove-item ",
        " set-content ",
        " out-file ",
        " add-content ",
        " new-item ",
        " tee-object ",
        " >",
        ">>",
        " rm ",
        " rmdir ",
        " del ",
        " erase ",
        " format ",
        " npm install",
        " pnpm install",
        " yarn add",
        " pip install",
        " uv add",
        " git push",
        " gh issue close",
        " gh pr merge",
        ".env",
        " id_rsa",
        " credentials",
        " secret",
        " token",
    )
    return any(token in lowered for token in high_risk_tokens)


def _path_requires_confirmation(path: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    sensitive_parts = (
        ".env",
        "id_rsa",
        "credentials",
        "secret",
        "token",
    )
    return any(part in lowered for part in sensitive_parts)


# ---------------------------------------------------------------------------
# Derived views — every other module reads these instead of its own copy.
# ---------------------------------------------------------------------------

def coordinator_tool_names() -> set[str]:
    """Allowlist of tools the in-turn coordinator may drive."""
    return {s.name for s in _all_specs() if s.coordinator}


def streaming_tool_names() -> set[str]:
    return {s.name for s in REGISTRY if s.streaming}


def desktop_tool_names() -> set[str]:
    return {s.name for s in REGISTRY if s.desktop}


def observe_after_tool_names() -> set[str]:
    return {s.name for s in REGISTRY if s.observe_after}


def deterministic_tool_names() -> set[str]:
    return {s.name for s in REGISTRY if s.deterministic}


def _param_signature(spec: ToolSpec) -> str:
    parts = []
    for pname in spec.params:
        parts.append(pname if pname in spec.required else f"{pname}?")
    return ", ".join(parts)


def tool_menu(coordinator_only: bool = True) -> str:
    """The decision menu shown to the front brain when it picks an action."""
    lines = []
    for spec in _all_specs():
        if coordinator_only and not spec.coordinator:
            continue
        sig = _param_signature(spec)
        lines.append(f"- {spec.name}({sig}): {spec.description.split(': ', 1)[-1]}")
    return "\n".join(lines)


_CATEGORY_LABELS = {
    "files": "Files & folders",
    "shell": "Shell / CLI",
    "github": "GitHub",
    "web": "Web",
    "desktop": "Desktop & screen",
    "code": "Coding agent",
    "general": "Other",
}


def capability_manifest(coordinator_only: bool = True) -> str:
    """Always-on, user-facing description of what the assistant can actually do.

    Injected into both the decision step AND the final-answer layer so the model
    knows its real capabilities — the fix for "I have no tools available".
    """
    by_cat: dict[str, list[ToolSpec]] = {}
    for spec in _all_specs():
        if coordinator_only and not spec.coordinator:
            continue
        by_cat.setdefault(spec.category, []).append(spec)

    # Predefined categories first (stable order), then any extras (e.g. an MCP
    # server's namespace) appended in insertion order.
    ordered = list(_CATEGORY_LABELS) + [c for c in by_cat if c not in _CATEGORY_LABELS]
    blocks = []
    for cat in ordered:
        specs = by_cat.get(cat)
        if not specs:
            continue
        label = _CATEGORY_LABELS.get(cat, cat.replace("_", " ").title())
        items = "; ".join(f"{s.name} — {s.summary.lower()}" for s in specs)
        blocks.append(f"{label}: {items}")
    return "\n".join(blocks)


def json_schema(spec: ToolSpec) -> dict[str, Any]:
    """JSON Schema for a tool's arguments (MCP manifest + native tool-calling)."""
    return {
        "type": "object",
        "properties": {pname: dict(pdef) for pname, pdef in spec.params.items()},
        "required": list(spec.required),
    }


def mcp_manifest() -> dict[str, Any]:
    """Pilot MCP server's tool list, generated from the registry."""
    tools = []
    for spec in REGISTRY:
        if not spec.mcp_facing:
            continue
        tools.append({
            "name": spec.mcp_name or f"pilot_{spec.name}",
            "description": spec.summary,
            "inputSchema": json_schema(spec),
            "riskLevel": spec.risk_level,
            "sideEffects": spec.side_effects,
        })
    return {"tools": tools}


def tool_schemas(coordinator_only: bool = True) -> list[dict[str, Any]]:
    """OpenAI/Ollama-style function schemas for native tool-calling (Fas B)."""
    schemas = []
    for spec in _all_specs():
        if coordinator_only and not spec.coordinator:
            continue
        schemas.append({
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": json_schema(spec),
            },
        })
    return schemas

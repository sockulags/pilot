"""Per-job capability profiles for scheduled task agents.

A scheduled ``task`` job runs the full coordinator in the background with no
interactive user present to confirm risky actions. To keep that safe, every job
carries a named *permission profile* that bounds which tools its coordinator run
may use. The coordinator's tool gate (agents/coordinator.py) consults
``tool_allowed`` before executing any tool; anything the profile doesn't grant is
skipped with an audit note.

Profiles are intentionally coarse and derived from the registry's own metadata
(``category`` + ``side_effects``) rather than a hand-kept tool list, so newly
added tools fall into the right bucket automatically:

- ``reminder-only`` — no tools at all (default for reminder kind).
- ``read-only``     — read-ish, no-side-effect tools (read_file, list_dir,
                       find_file, search_files, github_*). The safe default for
                       task kind.
- ``web-only``      — read-only plus web/search tools (web_research, web_search,
                       fetch_url). No shell, no desktop, no file writes.
- ``project-write`` — read-only/web plus project file writes via gated shell
                       (run_command), but no desktop input. Granting this is an
                       explicit opt-in to let a scheduled job run shell commands.
- ``desktop-control``— read-only plus desktop input tools (click/type/...).
                       Explicit opt-in.
- ``shell``         — read-only/web plus run_command. Explicit opt-in.

Granting ``project-write``/``desktop-control``/``shell`` to a scheduled job is
the explicit pre-authorization the issue calls for: without one of those
profiles, desktop and shell side effects are denied at fire time.
"""

from __future__ import annotations

from tools import registry

# The default profile for each job kind. Reminders never touch tools; tasks get
# the safest tool-using profile (read-only) unless the creator opts into more.
DEFAULT_PROFILE_BY_KIND = {
    "reminder": "reminder-only",
    "task": "read-only",
}

# Tool categories (registry ToolSpec.category) considered read-ish / no side
# effect. These are always available to any profile above reminder-only.
_READ_CATEGORIES = {"files", "github"}
_WEB_CATEGORIES = {"web"}

# Desktop input tools (the dangerous keyboard/pointer surface). Mirrors
# agents.safety.UNSAFE_DESKTOP_TOOLS plus the registry's desktop flag.
from agents.safety import UNSAFE_DESKTOP_TOOLS  # noqa: E402

# A profile is a set of *capability tokens*. The coordinator gate maps a concrete
# tool to the tokens it needs and checks membership.
#   "read"    -> read-ish tools (files/github, no side effects)
#   "web"     -> web/search tools
#   "shell"   -> run_command
#   "desktop" -> desktop input tools (click/type/key/scroll/...)
JOB_PERMISSION_PROFILES: dict[str, set[str]] = {
    "reminder-only": set(),
    "read-only": {"read"},
    "web-only": {"read", "web"},
    "project-write": {"read", "web", "shell"},
    "desktop-control": {"read", "desktop"},
    "shell": {"read", "web", "shell"},
}


def is_valid_profile(profile: str | None) -> bool:
    return profile in JOB_PERMISSION_PROFILES


def normalize_profile(profile: str | None, kind: str = "task") -> str:
    """Return a valid profile, defaulting by job kind when missing/invalid."""
    if is_valid_profile(profile):
        return profile  # type: ignore[return-value]
    return DEFAULT_PROFILE_BY_KIND.get(kind, "read-only")


def _capability_for_tool(tool: str) -> str:
    """The capability token a concrete tool requires.

    Read-ish tools need "read"; web tools "web"; run_command "shell"; desktop
    input "desktop". Anything with side effects that isn't otherwise classified
    is treated as "shell" (the most-restricted opt-in bucket) so it can never run
    under a read-only/web-only profile by default.
    """
    if tool in UNSAFE_DESKTOP_TOOLS:
        return "desktop"
    if tool == "run_command":
        return "shell"
    spec = registry.get(tool)
    # Side effects trump category: a side-effecting tool in a read-ish category
    # (write_file lives under "files") still requires the explicit "shell"
    # opt-in — a read-only scheduled job may never write.
    if spec is not None and spec.side_effects:
        return "shell"
    category = spec.category if spec else None
    if category in _WEB_CATEGORIES:
        return "web"
    if category in _READ_CATEGORIES:
        return "read"
    # Read-only by registry classification: no side effects -> "read".
    if spec is not None:
        return "read"
    # Unknown tool: require the explicit "shell" opt-in.
    return "shell"


def tool_allowed(tool: str, profile: str | None) -> bool:
    """Whether ``tool`` may run under the given permission ``profile``.

    Unknown profiles fall back to the safe ``read-only`` set. ``None`` here means
    "no profile attached" — also treated as read-only (a scheduled job always has
    *some* bound; unrestricted is signalled by passing capabilities=None to the
    coordinator, not by an absent profile).
    """
    caps = JOB_PERMISSION_PROFILES.get(
        profile if profile in JOB_PERMISSION_PROFILES else "read-only", set()
    )
    if not caps:
        return False  # reminder-only: nothing permitted
    return _capability_for_tool(tool) in caps


def allowed_tools_for_profile(profile: str | None) -> set[str]:
    """Concrete coordinator tool names permitted under ``profile`` (for tests/UI)."""
    return {
        name for name in registry.coordinator_tool_names()
        if tool_allowed(name, profile)
    }

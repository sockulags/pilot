import logging
import os
from env_loader import load_env_file

load_env_file()

logger = logging.getLogger(__name__)

# Directory where chat sessions are persisted (one JSON file per session_id).
# Defaults to backend/data/sessions relative to this file.
SESSIONS_DIR = os.getenv(
    "SESSIONS_DIR",
    os.path.join(os.path.dirname(__file__), "data", "sessions"),
)

# File holding the configured list of project roots for the `code` route.
PROJECTS_FILE = os.getenv(
    "PROJECTS_FILE",
    os.path.join(os.path.dirname(__file__), "data", "projects.json"),
)

# Optional semicolon-separated project roots to seed PROJECTS_FILE on first run.
PILOT_PROJECT_ROOTS = os.getenv("PILOT_PROJECT_ROOTS", "")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:12b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen3.5:9b")
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "gpt-oss:20b")

# --- Long-term memory (semantic retrieval) ----------------------------------
# Embeddings model for the cross-session memory store (agents-agnostic facts and
# preferences). nomic-embed-text is small and installed by default.
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
MEMORY_FILE = os.getenv(
    "MEMORY_FILE", os.path.join(os.path.dirname(__file__), "data", "memory.json")
)
# How many memories to retrieve per turn, and the minimum cosine similarity for
# one to count as relevant. nomic-embed-text has a high similarity baseline
# (unrelated sentences still score ~0.58-0.60), so the bar sits at 0.62 to keep
# genuine matches (~0.63+) while filtering that baseline noise. Tunable.
MEMORY_TOP_K = int(os.getenv("MEMORY_TOP_K", "4"))
MEMORY_MIN_SCORE = float(os.getenv("MEMORY_MIN_SCORE", "0.62"))

# --- Scheduled jobs (recurring reminders / background tasks) ----------------
# Persistent job store + how often the scheduler loop wakes to check for due
# jobs. 20s is plenty given schedules have minute granularity. The file lives
# under backend/data/ (gitignored), mirroring the session and memory stores.
JOBS_FILE = os.getenv(
    "JOBS_FILE", os.path.join(os.path.dirname(__file__), "data", "jobs.json")
)
JOBS_TICK_SECONDS = int(os.getenv("JOBS_TICK_SECONDS", "20"))

# --- Local model registry (dynamic per-turn model selection) ----------------
# The orchestrator picks the best local model for each turn ("auto"), or the
# user pins one via the UI toggle / `/model <id>`. Each entry carries a hint the
# auto-picker shows the classifier, and a `tools` flag: only tools-capable
# models may drive the JSON tool router (the computer route). deepseek-r1 has
# tool metadata in Ollama, but in practice it is unreliable for tool-routing in
# this app, so it remains pinned/manual reasoning only.
OLLAMA_MODELS: dict[str, dict] = {
    "gemma4:12b": {
        "label": "Gemma 4 12B",
        "hint": "Standardmodell för svensk chatt, gateway och stabila svar",
        "tools": True,
    },
    "gpt-oss:20b": {
        "label": "GPT-OSS 20B",
        "hint": "Starkt allmänt resonemang, research och sammanvägning",
        "tools": True,
    },
    "qwen3.5:9b": {
        "label": "Qwen3.5 9B",
        "hint": "Snabb tools-capable modell med fungerande vision",
        "tools": True,
    },
    "deepseek-r1:14b": {
        "label": "DeepSeek-R1 14B",
        "hint": "Manuellt djupt resonemang, matematik och klurig analys",
        "tools": False,
    },
    "devstral:latest": {
        "label": "Devstral",
        "hint": "Agentiskt repoarbete och längre koduppgifter",
        "tools": True,
    },
    "qwen2.5-coder:14b": {
        "label": "Qwen2.5 Coder",
        "hint": "Snabb kod, teknik och programmeringsfrågor",
        "tools": True,
    },
}

# The orchestrator's own classification step always runs on this model — it must
# be fast and tools-capable, never the user's pinned answering model.
OLLAMA_ROUTER_MODEL = os.getenv("OLLAMA_ROUTER_MODEL", OLLAMA_MODEL)

# Gateway role: refines/translates a request into a clean English instruction
# before it is handed to a specialist model or the code agent (local models
# reason and code better in English; the user-facing reply is still composed in
# the user's language). The clarity gate ("ask instead of guessing when vague")
# rides on the coordinator's decision step, so it costs nothing extra —
# refinement is the only added call, and only fires on an actual hand-off
# (expert consult / code), never on trivial chat.
#
# This role NEEDS a model that is strong at the user's language. Local testing
# showed gemma4:12b translates Swedish faithfully and returns usable content for
# short refinement prompts, while some thinking-heavy models can spend the whole
# short response budget in their thinking field. If the gateway model is not
# installed, refine_query fails open to the verbatim request (safe, no
# corruption).
OLLAMA_GATEWAY_MODEL = os.getenv("OLLAMA_GATEWAY_MODEL", "gemma4:12b")
GATEWAY_REFINE_ENABLED = os.getenv("GATEWAY_REFINE_ENABLED", "true").lower() == "true"

AGENT_ROLE_LABELS: dict[str, str] = {
    "default_agent": "Default",
    "research_agent": "Research",
    "code_agent": "Code",
    "quick_code_agent": "Quick code",
    "vision_agent": "Vision",
    "deep_reasoning_agent": "Deep reasoning",
}

AGENT_ROLE_MODELS: dict[str, str] = {
    "default_agent": os.getenv("PILOT_DEFAULT_AGENT", OLLAMA_MODEL),
    "research_agent": os.getenv("PILOT_RESEARCH_AGENT", "gpt-oss:20b"),
    "code_agent": os.getenv("PILOT_CODE_AGENT", "devstral:latest"),
    "quick_code_agent": os.getenv("PILOT_QUICK_CODE_AGENT", "qwen2.5-coder:14b"),
    "vision_agent": os.getenv("PILOT_VISION_AGENT", OLLAMA_VISION_MODEL),
    "deep_reasoning_agent": os.getenv("PILOT_DEEP_REASONING_AGENT", "deepseek-r1:14b"),
}

INTENT_AGENT_ROLES: dict[str, str] = {
    "chat": "default_agent",
    "research": "research_agent",
    "research_and_create_file": "research_agent",
    "create_file": "default_agent",
    "local_model_audit_report": "default_agent",
    "project_analysis": "code_agent",
    "computer_action": "default_agent",
    "code": "code_agent",
    "quick_code": "quick_code_agent",
    "vision": "vision_agent",
    "deep_reasoning": "deep_reasoning_agent",
}


def is_known_model(model: str | None) -> bool:
    return bool(model) and model in OLLAMA_MODELS


def tools_capable_model(model: str | None) -> str:
    """Return `model` if it's a known tools-capable model, else OLLAMA_MODEL.

    Guards the computer route's JSON tool router from being handed a model that
    can't emit tool calls (e.g. deepseek-r1).
    """
    if model and OLLAMA_MODELS.get(model, {}).get("tools"):
        return model
    return OLLAMA_MODEL


def resolve_answer_model(model_mode: str | None, suggested: str | None) -> str:
    """Resolve which model answers a turn.

    ``model_mode`` is "auto" or a pinned model id. In auto mode the classifier's
    ``suggested`` model wins (when known); otherwise the pin wins. Falls back to
    OLLAMA_MODEL when nothing is valid.
    """
    if model_mode and model_mode != "auto" and is_known_model(model_mode):
        return model_mode
    if is_known_model(suggested):
        return suggested  # type: ignore[return-value]
    return OLLAMA_MODEL

# Set to "true" only when OLLAMA_VISION_MODEL is actually a multimodal model.
# qwen3.5:9b is the current local default because it accepted image input and
# correctly identified a smoke-test image.
OLLAMA_VISION_ENABLED = os.getenv("OLLAMA_VISION_ENABLED", "true").lower() == "true"

# Path/name of the Claude CLI binary. tools/codex.py resolves this: an absolute
# path wins, then PATH, then the Claude desktop app's bundled CLI (Windows MSIX).
# On win32 a .cmd/.bat wrapper is invoked via "cmd /c" automatically.
CLAUDE_CLI = os.getenv("CLAUDE_CLI", "claude")

# Permission mode for headless Claude Code runs (the `code` route). Print mode
# cannot prompt, so this is fixed up front. "acceptEdits" auto-accepts file
# edits; other ops follow normal rules. See `claude --help` for valid modes.
CLAUDE_PERMISSION_MODE = os.getenv("CLAUDE_PERMISSION_MODE", "acceptEdits")

# Path/name of the Codex CLI binary (the `code` route's alternate agent).
# tools/codex_cli.py resolves this: absolute path -> PATH -> the Codex desktop
# app's bundled CLI (%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe).
CODEX_CLI = os.getenv("CODEX_CLI", "codex")

# Sandbox policy for headless `codex exec`. "workspace-write" mirrors
# acceptEdits (writes within the project, no prompts). Other valid values:
# "read-only", "danger-full-access". See `codex exec --help`.
#
# "danger-full-access" disables the sandbox entirely, so it is NOT honored from
# CODEX_SANDBOX_MODE alone — it requires the explicit opt-in flag
# CODEX_ALLOW_DANGER_FULL_ACCESS. Without it, the mode is downgraded to the safe
# default "workspace-write" (see resolve_codex_sandbox_mode).
_CODEX_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}
_CODEX_SANDBOX_DEFAULT = "workspace-write"


def resolve_codex_sandbox_mode(
    configured: str | None = None, allow_danger: str | None = None
) -> str:
    """Resolve the effective `codex exec --sandbox` mode.

    Reads CODEX_SANDBOX_MODE (and CODEX_ALLOW_DANGER_FULL_ACCESS) from the
    environment when not given explicitly (the explicit args exist for tests).

    Rules:
      * An unknown/invalid mode falls back to the safe default (never crashes).
      * "danger-full-access" is only honored when CODEX_ALLOW_DANGER_FULL_ACCESS
        is truthy; otherwise it is downgraded to the safe default with a warning.
    """
    if configured is None:
        configured = os.getenv("CODEX_SANDBOX_MODE", _CODEX_SANDBOX_DEFAULT)
    if allow_danger is None:
        allow_danger = os.getenv("CODEX_ALLOW_DANGER_FULL_ACCESS", "false")

    mode = (configured or "").strip()
    if mode not in _CODEX_SANDBOX_MODES:
        logger.warning(
            "Invalid CODEX_SANDBOX_MODE %r; falling back to %r",
            configured,
            _CODEX_SANDBOX_DEFAULT,
        )
        return _CODEX_SANDBOX_DEFAULT

    if mode == "danger-full-access" and (allow_danger or "").strip().lower() != "true":
        logger.warning(
            "CODEX_SANDBOX_MODE=danger-full-access requires "
            "CODEX_ALLOW_DANGER_FULL_ACCESS=true to opt in; "
            "downgrading to %r.",
            _CODEX_SANDBOX_DEFAULT,
        )
        return _CODEX_SANDBOX_DEFAULT

    return mode


CODEX_SANDBOX_MODE = resolve_codex_sandbox_mode()

BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
MCP_PORT = int(os.getenv("MCP_PORT", "3001"))

# --- External MCP servers (browser control, etc.) ---------------------------
# The coordinator acts as an MCP CLIENT to these and surfaces their tools
# (namespaced mcp__<server>__<tool>). Heavy servers like a browser are opt-in so
# the backend doesn't launch them on every start. Enable the Playwright browser
# server with PILOT_MCP_BROWSER=true (needs Node/npx; npx fetches it on first
# run). Override the launch command with PILOT_MCP_BROWSER_CMD.
PILOT_MCP_BROWSER_ENABLED = os.getenv("PILOT_MCP_BROWSER", "false").lower() == "true"
PILOT_MCP_BROWSER_CMD = os.getenv("PILOT_MCP_BROWSER_CMD", "npx @playwright/mcp@latest")

# Optional shared secret. When set, WS clients must send a matching token in
# their `hello` message. Empty = no auth (LAN behaviour). Defense-in-depth for
# remote access; the Tailscale network is the primary boundary.
PILOT_AUTH_TOKEN = os.getenv("PILOT_AUTH_TOKEN", "")

# Built frontend (Next static export). When present, the backend serves the UI
# from this directory so everything is one origin. Defaults to ../frontend/out.
FRONTEND_DIR = os.getenv(
    "FRONTEND_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "out")),
)

MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "50"))

# Max steps the in-turn coordinator (agents/coordinator.py) takes before it must
# answer — bounds how many expert consultations / tool calls one turn can chain.
COORDINATOR_MAX_STEPS = int(os.getenv("COORDINATOR_MAX_STEPS", "6"))

# OS-grounded perception (Set-of-Marks): enumerate interactive UI elements via
# Windows UI Automation so the agent clicks known element centers instead of
# guessing pixel coordinates. The element list is plain text, so this works
# WITHOUT a vision model — vision (OLLAMA_VISION_ENABLED) only adds a picture.
PERCEPTION_ENABLED = os.getenv("PERCEPTION_ENABLED", "true").lower() == "true"
PERCEPTION_MAX_ELEMENTS = int(os.getenv("PERCEPTION_MAX_ELEMENTS", "60"))

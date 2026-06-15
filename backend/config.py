import os
from env_loader import load_env_file

load_env_file()

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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:latest")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "gemma4:latest")
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen3:14b")

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
# models may drive the JSON tool router (the computer route). deepseek-r1 is a
# reasoning model with NO tool support, so it is chat/reasoning only.
OLLAMA_MODELS: dict[str, dict] = {
    "gemma4:latest": {
        "label": "Gemma 4",
        "hint": "Snabb allmän chatt, vardagsfrågor och korta svar",
        "tools": True,
    },
    "qwen3:14b": {
        "label": "Qwen3 14B",
        "hint": "Starkt allmänt resonemang och flerstegsuppgifter",
        "tools": True,
    },
    "deepseek-r1:14b": {
        "label": "DeepSeek-R1 14B",
        "hint": "Djupt resonemang, matematik och klurig analys",
        "tools": False,
    },
    "qwen2.5-coder:14b": {
        "label": "Qwen2.5 Coder",
        "hint": "Kod, teknik och programmeringsfrågor",
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
# This role NEEDS a model that is strong at the user's language: gemma4:8b
# mistranslates Swedish badly ("vänd en sträng" -> "watering a vine"), whereas
# gemma4:12b and qwen3:14b translate it faithfully. Defaults to gemma4:12b; if
# it's not installed, refine_query fails open to the verbatim request (safe, no
# corruption). Point this at llama3.1/gpt-oss/etc. once pulled.
OLLAMA_GATEWAY_MODEL = os.getenv("OLLAMA_GATEWAY_MODEL", "gemma4:12b")
GATEWAY_REFINE_ENABLED = os.getenv("GATEWAY_REFINE_ENABLED", "true").lower() == "true"


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

# Set to "true" only when OLLAMA_VISION_MODEL is actually a multimodal model
# (e.g. llava, llama3.2-vision, minicpm-v). gemma4 is text-only, so this
# defaults to false — the done-summary falls back to text_done_summary instead.
OLLAMA_VISION_ENABLED = os.getenv("OLLAMA_VISION_ENABLED", "false").lower() == "true"

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
CODEX_SANDBOX_MODE = os.getenv("CODEX_SANDBOX_MODE", "workspace-write")

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

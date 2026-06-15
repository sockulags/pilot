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

MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "50"))

# OS-grounded perception (Set-of-Marks): enumerate interactive UI elements via
# Windows UI Automation so the agent clicks known element centers instead of
# guessing pixel coordinates. The element list is plain text, so this works
# WITHOUT a vision model — vision (OLLAMA_VISION_ENABLED) only adds a picture.
PERCEPTION_ENABLED = os.getenv("PERCEPTION_ENABLED", "true").lower() == "true"
PERCEPTION_MAX_ELEMENTS = int(os.getenv("PERCEPTION_MAX_ELEMENTS", "60"))

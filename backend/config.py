import os
from env_loader import load_env_file

load_env_file()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:latest")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "gemma4:latest")
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen3:14b")

# Set to "true" only when OLLAMA_VISION_MODEL is actually a multimodal model
# (e.g. llava, llama3.2-vision, minicpm-v). gemma4 is text-only, so this
# defaults to false — the done-summary falls back to text_done_summary instead.
OLLAMA_VISION_ENABLED = os.getenv("OLLAMA_VISION_ENABLED", "false").lower() == "true"

# Path/name of the Claude CLI binary. On Windows the npm-installed wrapper is
# a .cmd file that can't be exec'd directly — the backend handles this by
# calling "cmd /c CLAUDE_CLI ..." on win32 automatically (see tools/codex.py).
CLAUDE_CLI = os.getenv("CLAUDE_CLI", "claude")

BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
MCP_PORT = int(os.getenv("MCP_PORT", "3001"))

MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "50"))

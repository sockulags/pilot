import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:latest")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "gemma4:latest")
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen3:14b")

BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
MCP_PORT = int(os.getenv("MCP_PORT", "3001"))

MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "20"))

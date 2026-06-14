# Pilot

Local AI agent that controls the computer via Ollama models, controlled from web/mobile.

## Requirements

- [Ollama](https://ollama.com) running locally with `gemma4:latest` (or edit `backend/config.py`)
- [uv](https://docs.astral.sh/uv/) for Python
- [pnpm](https://pnpm.io) + Node 18+ for the frontend

## Quick start

### Backend

```bash
cd backend
uv run python main.py
```

Starts:
- `http://localhost:8000` — FastAPI + WebSocket (`/ws`)
- `http://localhost:3001` — MCP server (`/mcp`)

### Frontend

```bash
cd frontend
pnpm install   # first time only
pnpm dev
```

Opens at `http://localhost:3000`.

## Environment variables (backend)

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `gemma4:latest` | Primary LLM |
| `OLLAMA_VISION_MODEL` | `gemma4:latest` | Vision model |
| `OLLAMA_FALLBACK_MODEL` | `qwen3:14b` | Fallback LLM |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama URL |
| `BACKEND_PORT` | `8000` | FastAPI port |
| `MCP_PORT` | `3001` | MCP server port |
| `MAX_AGENT_STEPS` | `20` | Max agent loop iterations |

## MCP integration (Claude Desktop)

Add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "pilot": {
      "url": "http://localhost:3001/mcp",
      "transport": "sse"
    }
  }
}
```

Available tools: `pilot_screenshot`, `pilot_click`, `pilot_type`, `pilot_run_command`, `pilot_open_app`.

## WebSocket events

```
client → server: {"type": "run", "task": "Open Notepad and write Hello"}
client → server: {"type": "abort"}

server → client: {"type": "thinking", "content": "..."}
server → client: {"type": "action", "tool": "click", "args": {"x": 100, "y": 200}}
server → client: {"type": "result", "content": "..."}
server → client: {"type": "screenshot", "image": "<base64>"}
server → client: {"type": "done", "summary": "..."}
server → client: {"type": "error", "content": "..."}
```

## Project structure

```
pilot/
├── backend/
│   ├── main.py          # Entrypoint
│   ├── config.py        # Env-based config
│   ├── agents/
│   │   ├── loop.py      # Agent execution loop
│   │   └── router.py    # LLM tool router
│   ├── tools/
│   │   ├── screen.py    # screenshot, get_screen_size
│   │   ├── input.py     # click, type_text, scroll
│   │   ├── system.py    # run_command, open_app
│   │   └── codex.py     # run_codex (claude CLI)
│   └── api/
│       ├── ws.py        # WebSocket endpoint
│       └── mcp.py       # MCP SSE server
└── frontend/
    ├── app/             # Next.js App Router
    └── components/      # TaskInput, ActionLog, AbortButton
```

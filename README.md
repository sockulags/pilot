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
| `OLLAMA_MODEL` | `gemma4:latest` | Primary / default LLM |
| `OLLAMA_ROUTER_MODEL` | = `OLLAMA_MODEL` | Model the orchestrator classifies + picks on (must be fast & tools-capable) |
| `OLLAMA_VISION_MODEL` | `gemma4:latest` | Vision model |
| `OLLAMA_FALLBACK_MODEL` | `qwen3:14b` | Fallback LLM |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama URL |
| `BACKEND_PORT` | `8000` | FastAPI port |
| `MCP_PORT` | `3001` | MCP server port |
| `MAX_AGENT_STEPS` | `20` | Max agent loop iterations |

## Coordinator & dynamic model use

Chat/computer turns run through an **in-turn coordinator** (`agents/coordinator.py`).
A fast front-brain model receives the turn and, within that single turn, can:

- **consult** a specialist model — for code it asks `qwen2.5-coder`, for hard
  reasoning `qwen3`/`deepseek-r1` — and weave the answer in;
- **perceive** the screen (screenshot + Set-of-Marks element list — the
  text-based "vision" path, no multimodal model needed);
- run **OS/desktop tools** (run_command, read_file, click_element, …);
- then **answer**, synthesised from everything gathered.

Only models actually installed in Ollama are offered as experts, so it adapts to
what's available. Each expert hand-off and the coordinator's reasoning stream
live (you see "🔀 frågar qwen2.5-coder" and the expert's tokens as they arrive),
collapsing into a **Detaljer** panel once the turn finishes.

The **Modell** dropdown / `/model` command picks the policy:

- **Auto** (default) — the front brain is fast `gemma4`; it consults experts as
  needed. This is the "best model per question, automatically" path.
- **Pinned** (`/model qwen3`, `/model <prefix>`, or the dropdown) — that model
  leads the turn instead. `/model` alone shows the current choice + options;
  `/model auto` returns to auto. Persisted per session.

Models that can't emit tool calls (e.g. `deepseek-r1`, `tools:false` in the
registry) are never used to drive tools/perception — a tools-capable model is
substituted automatically.

`COORDINATOR_MAX_STEPS` (default 6) bounds how many consults/tool calls one turn
may chain.

## Long-term memory

Pilot remembers durable facts across sessions with a small semantic store
(`backend/memory.py`, embeddings via `nomic-embed-text`):

- **Recall** — every chat/computer turn embeds the message and retrieves the
  most similar stored memories (cosine ≥ `MEMORY_MIN_SCORE`), injecting them into
  the coordinator and the final reply. So "vad heter jag?" works in a brand-new
  session once you've told it your name.
- **Save** — the coordinator's `remember` action stores a fact (e.g. when you
  say "kom ihåg att…", or share a lasting preference). A 💾 chip marks the turn.
  Facts are saved in **your language** — `nomic-embed-text` is weak cross-lingual,
  so a translated memory wouldn't match a same-language query.

The store is a JSON file under `backend/data/` (gitignored). Tunables:
`MEMORY_TOP_K`, `MEMORY_MIN_SCORE`, `OLLAMA_EMBED_MODEL`.

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

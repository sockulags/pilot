# Getting started

This is the installation and first-run guide. For what Pilot is and how it works —
the coordinator loop, task contracts, safety layers, model backends — see the
[README](README.md).

## 1. Prerequisites

- **[Ollama](https://ollama.com)** running locally with the default model:
  `ollama pull gemma4:12b` (or set `OLLAMA_MODEL` to another tools-capable model).
- **[uv](https://docs.astral.sh/uv/)** (Python 3.12+).
- **[pnpm](https://pnpm.io)** + Node 18+.
- *(Optional)* the **Claude Code** and/or **Codex** desktop apps for the code agents —
  their CLIs are auto-discovered (see [§3](#3-code-agents-optional)).

## 2. Run it

**Shortcut (Windows):** `./scripts/dev.ps1` preflights the toolchain and the
default model, installs frontend deps on first run, and launches both servers.
Add `-SingleOrigin` for one-port mode. The manual steps below are what it wraps
(see [`scripts/README.md`](scripts/README.md)).

There are two ways to run. Pick one.

### A) Dev mode — two servers (for development)

```bash
# terminal 1 — backend (WebSocket :8000, MCP :3001)
cd backend
uv run python main.py

# terminal 2 — frontend (:3000)
cd frontend
pnpm install        # first time only
pnpm dev
```

Open **http://localhost:3000**. From your phone on the same Wi-Fi:
**http://&lt;your-pc-lan-ip&gt;:3000** (e.g. `http://192.168.50.9:3000`). The UI auto-targets
the backend WebSocket on `:8000` of the same host.

### B) Single-origin — one server (for real use / remote)

```bash
cd frontend
pnpm build          # produces frontend/out
cd ../backend
uv run python main.py
```

Open **http://localhost:8000** — the backend now serves **both** the UI and the
WebSocket on one port. From your phone on LAN: **http://&lt;your-pc-lan-ip&gt;:8000**.

Stop either with `Ctrl+C`.

Type a message to try it out — how a turn is classified and executed is described in
the README's [Architecture](README.md#architecture) section. Conversations persist
(survive reconnects/reloads); "Ny konversation" clears it.

## 3. Code agents (optional)

In the bar above the input, pick a **Projekt** (add a folder by its path) and an
**Agent**:

- **Codex** — works out of the box if the Codex desktop app is installed and logged in
  (it shares the desktop login).
- **Claude Code** — needs a one-time headless login: run the Claude CLI once and `/login`,
  **or** set `ANTHROPIC_API_KEY` in `backend/.env`. Otherwise it replies "Not logged in".

The agent runs inside the project folder, may edit files (sandboxed to the project), and
continues its own session across turns.

## 4. Configuration (`backend/.env`)

Copy `backend/.env.example` to `backend/.env` and adjust. All values are optional;
defaults live in `backend/config.py`. The most common ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `gemma4:12b` | Default coordinator/answer model |
| `OLLAMA_VISION_MODEL` | `qwen3.5:9b` | Vision model (optional) |
| `OLLAMA_VISION_ENABLED` | `true` | Enable image vision (needs a multimodal model) |
| `OLLAMA_VISION_NUM_CTX` | `8192` | Vision context; sized for a full-screen image plus task text |
| `OLLAMA_DEFAULT_NUM_CTX` | `8192` | Conservative context for unroled local chat calls |
| `OLLAMA_CLASSIFIER_NUM_CTX` / `OLLAMA_GATEWAY_NUM_CTX` | `4096` | Context for short structured routing stages |
| `OLLAMA_SYNTHESIS_NUM_CTX` | `16384` | Context for coordinator and final synthesis |
| `OLLAMA_CODE_NUM_CTX` | `32768` | Context for code roles; clamped to the model maximum |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `PILOT_ANSWER_BACKEND` | `ollama` | `ollama` (fully local) or `openai` — see the README's [Model backends](README.md#model-backends-local-first-api-optional) |
| `BACKEND_PORT` / `MCP_PORT` | `8000` / `3001` | Server ports |
| `COORDINATOR_MAX_STEPS` | `6` | Max consults/tool calls one turn may chain |
| `MAX_AGENT_STEPS` | `50` | Max desktop-agent loop iterations |
| `COMMAND_TIMEOUT_SECONDS` | `60` | Wall-clock bound for one `run_command` |
| `PERCEPTION_ENABLED` | `true` | OS-grounded UI perception (Set-of-Marks) |
| `CLAUDE_PERMISSION_MODE` | `acceptEdits` | Headless Claude Code permission mode |
| `CODEX_SANDBOX_MODE` | `workspace-write` | Headless Codex sandbox |
| `CLAUDE_CLI` / `CODEX_CLI` | auto | Override CLI paths (else auto-discovered) |
| `PILOT_AUTH_TOKEN` | *(empty)* | Optional shared secret — see [§5](#5-optional-access-from-anywhere) |
| `FRONTEND_DIR` | `../frontend/out` | Built UI served in single-origin mode |

The full list (network/auth, memory, ComfyUI, code agents) is in `backend/.env.example`
and `backend/config.py`.

To use LM Studio or llama.cpp instead of Ollama, start that runtime yourself and
open **Modellinställningar → Lokal modellruntime**. Choose OpenAI-compatible,
enter `http://127.0.0.1:1234/v1` (LM Studio) or
`http://127.0.0.1:8080/v1` (llama.cpp), and enter the exact loaded model id.
Pilot does not install runtimes or download models. Enable tools, structured
output, vision, or embeddings only when your chosen server/model supports them;
unknown capabilities are intentionally rejected for privacy and predictable
degradation.

To check an exact local runtime/model combination, use the deterministic and
opt-in live matrices in [Local inference compatibility](docs/local-inference-compatibility.md).
The live command writes JSON and Markdown only to the output stem you choose and
refuses accidental overwrites. LM Studio and llama.cpp are currently
**unverified**, not implicitly supported by their API shape.

## 5. (Optional) Access from anywhere

**You don't need this for local/LAN use** — everything above works on your home network.
This only adds reaching Pilot from *outside* your home, securely, via
**[Tailscale](https://tailscale.com)** (a private encrypted network; nothing is exposed
to the public internet).

1. Install Tailscale on the **PC** and the **phone**, sign in to the same account.
2. Enable **MagicDNS** in the Tailscale admin console.
3. Run Pilot in **single-origin mode** ([§2B](#b-single-origin--one-server-for-real-use--remote)).
4. Put HTTPS in front (syntax may vary by version):
   ```bash
   tailscale serve --bg https / http://127.0.0.1:8000
   ```
   Then open **https://&lt;your-pc&gt;.&lt;tailnet&gt;.ts.net** on the phone — even on mobile data.

**Optional auth token** (defense-in-depth): set `PILOT_AUTH_TOKEN=...`, then open the URL
once with `?token=...`. The token is saved in the browser and sent on every connect; the
URL is cleaned automatically.

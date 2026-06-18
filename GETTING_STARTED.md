# Getting started

Pilot is a chat GUI (Next.js PWA) backed by a local FastAPI server. You chat from
desktop or phone; each message is routed by a local Ollama "orchestrator" to one of
three actions: **chat** (answer itself), **computer** (control the desktop), or
**code** (run Claude Code / Codex in a project folder). Primary target is Windows.

## 1. Prerequisites

- **[Ollama](https://ollama.com)** running locally with a model, e.g. `ollama pull gemma4`
  (or set `OLLAMA_MODEL`).
- **[uv](https://docs.astral.sh/uv/)** (Python 3.12+).
- **[pnpm](https://pnpm.io)** + Node 18+.
- *(Optional, only for the `code` route)* the **Claude Code** and/or **Codex** desktop
  apps installed — their CLIs are auto-discovered (see [§4](#4-the-code-route)).

## 2. Run it

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

## 3. Using the chat

Type a message; the orchestrator decides what to do (a small "Chatt / Dator / Kod"
badge shows the route per turn):

- **Chatt** — the local model answers.
- **Dator** — it screenshots, reads on-screen UI elements, clicks/types, or runs a command.
- **Kod** — it runs a coding agent in your selected project folder.

Conversations persist (survive reconnects/reloads). "Ny konversation" clears it.

## 4. The `code` route

In the bar above the input, pick a **Projekt** (add a folder by its path) and an
**Agent**:

- **Codex** — works out of the box if the Codex desktop app is installed and logged in
  (it shares the desktop login).
- **Claude Code** — needs a one-time headless login: run the Claude CLI once and `/login`,
  **or** set `ANTHROPIC_API_KEY` in `backend/.env`. Otherwise it replies "Not logged in".

The agent runs inside the project folder, may edit files (sandboxed to the project), and
continues its own session across turns.

## 5. Configuration (`backend/.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `gemma4:12b` | Primary LLM (orchestrator + router) |
| `OLLAMA_VISION_MODEL` | `qwen3.5:9b` | Vision model (optional) |
| `OLLAMA_VISION_ENABLED` | `true` | Enable image vision (needs a multimodal model) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `BACKEND_PORT` / `MCP_PORT` | `8000` / `3001` | Server ports |
| `MAX_AGENT_STEPS` | `50` | Max desktop-agent loop iterations |
| `PERCEPTION_ENABLED` | `true` | OS-grounded UI perception (Set-of-Marks) |
| `CLAUDE_PERMISSION_MODE` | `acceptEdits` | Headless Claude Code permission mode |
| `CODEX_SANDBOX_MODE` | `workspace-write` | Headless Codex sandbox |
| `CLAUDE_CLI` / `CODEX_CLI` | auto | Override CLI paths (else auto-discovered) |
| `PILOT_AUTH_TOKEN` | *(empty)* | Optional shared secret — see [§6](#6-optional-access-from-anywhere) |
| `FRONTEND_DIR` | `../frontend/out` | Built UI served in single-origin mode |

## 6. (Optional) Access from anywhere

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

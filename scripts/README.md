# scripts/

Developer convenience scripts. None of these are required — Pilot always runs
with the plain two-command flow in [GETTING_STARTED.md](../GETTING_STARTED.md).

## `dev.ps1` — one-command bring-up (Windows)

Preflights the toolchain and the default Ollama model, installs frontend deps on
first run, then launches the backend and frontend together.

```powershell
# Dev mode: backend (:8000) + frontend (:3000) in two windows
./scripts/dev.ps1

# Single-origin: build the UI and serve everything from the backend on :8000
./scripts/dev.ps1 -SingleOrigin

# Skip the Ollama model preflight (remote/OpenAI backend)
./scripts/dev.ps1 -SkipModelCheck
```

The model preflight is advisory: a missing or unreachable Ollama only warns, so
the script never blocks bring-up (the backend also supports a remote/OpenAI
answering path).

### The equivalent manual steps

If you'd rather not run the script (or you're not on Windows), the whole thing is
just:

```bash
# 0. one-time: pull the default model
ollama pull gemma4:12b

# 1. backend — WebSocket :8000, MCP :3001
cd backend
uv run python main.py

# 2. frontend — http://localhost:3000  (separate terminal)
cd frontend
pnpm install   # first run only
pnpm dev
```

## Why there is no docker-compose

Pilot is a **local-desktop agent**, not a stateless web service. Its perception
and action layer drives the host's real GUI through Windows UI Automation and
`pyautogui` (screenshots, clicks, keystrokes), reads the user's actual files and
runs shell commands with the user's permissions, and by design talks to an
**Ollama daemon on the host**. A container has no host desktop to see or drive, so
a `docker-compose` for the agent would boot but couldn't do the one thing Pilot is
for. Keeping bring-up as host processes matches the trust model ("it runs on my
machine, with my permissions") described in the [README](../README.md). If a
headless, tool-only subset is ever containerized, it belongs in its own compose
file rather than pretending the full desktop agent fits one.

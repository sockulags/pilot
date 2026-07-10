# Contributing to Pilot

Thanks for taking a look. Pilot is a personal agent and a public code sample, so
contributions and issues are welcome, but the bar is: **local-first must keep
working with zero config, and every safety/grounding behaviour stays covered by
tests.**

## Setup

Prerequisites (full first-run guide in [GETTING_STARTED.md](GETTING_STARTED.md)):

- [Ollama](https://ollama.com) with the default model — `ollama pull gemma4:12b`
- [uv](https://docs.astral.sh/uv/) (Python 3.12+)
- [pnpm](https://pnpm.io) + Node 18+

Bring it up with the launcher or manually:

```powershell
./scripts/dev.ps1          # Windows one-command bring-up
```

```bash
cd backend  && uv run python main.py   # backend  (:8000, MCP :3001)
cd frontend && pnpm install && pnpm dev # frontend (:3000)
```

## Running the checks

The backend is the part with logic and tests; run these before opening a PR. CI
runs the same commands (see `.github/workflows/ci.yml`).

```bash
# Backend tests — Ollama-free and network-free by design
cd backend
uv run pytest -q
uv run pytest -q -k command_risk     # targeted subset while iterating

# Lint (must be clean)
uv run ruff check .

# Frontend (only if you touched frontend/)
cd frontend
pnpm lint           # tsc --noEmit + eslint (type-check and lint)
pnpm test           # Vitest unit tests
pnpm build          # production build
```

### Running the eval

Two suites live in `backend/tests/eval/`:

```bash
cd backend

# Deterministic replay suite — 40+ golden/adversarial scenarios, no model, no
# network. Runs in CI as part of the normal pytest run; select it alone with:
uv run pytest -q -m eval

# Live-model runner — drives the real agent end to end against a live model.
# Needs Ollama (or --backend openai). Not run in CI.
uv run python -m tests.eval.live_runner                  # local backend
uv run python -m tests.eval.live_runner --backend openai # OpenAI backend
uv run python -m tests.eval.live_runner --trials 3       # per-task variance
```

The deterministic suite treats the safety layers as **pass/fail gates**: one
prompt-injection or confirmation-gate failure fails the whole run regardless of
the average score. If you touch the coordinator, contracts, risk classifier, or
evidence quarantine, run `-m eval` and expect it to stay green.

## Writing tests

- Put backend tests under `backend/tests/`. Keep them **Ollama-free and
  network-free**: mock `httpx` with `MockTransport`, and stub model calls rather
  than reaching a live daemon. The existing tests (e.g. `tests/test_ws_auth.py`,
  `tests/test_command_risk.py`) are the pattern to follow.
- New safety or grounding behaviour needs a test that fails without the change.
  A red→green pair is how the eval-driven fixes in the README were landed.

## Code layout

```
backend/
  main.py              # entrypoint (FastAPI + MCP servers)
  config.py            # env-based config; defaults live here
  agents/              # orchestrator, coordinator, routing, task contracts,
                       #   providers (ollama|openai|cloud), safety, untrusted
  tools/               # registry (single source of truth) + OS/web/desktop tools
  api/                 # ws.py (WebSocket), mcp.py (MCP), settings.py (REST)
  tests/               # unit tests + tests/eval/ (replay suite + live runner)
frontend/              # Next.js chat UI
scripts/               # dev launcher (see scripts/README.md)
docs/                  # eval findings, demo scope, design notes
```

The tool set is declared **once** in `tools/registry.py` — that single source
feeds the coordinator menu, the MCP manifest, and the native function-call
schemas. Add tools there, not in three places.

## Design constraints (please respect these)

- **Local-first, zero-config default.** Any new external dependency or API must
  be **optional and env-gated**, with a working local fallback. Never break the
  path where the user has only Ollama installed.
- **Fail closed.** Model inventory, network exposure, desktop input without
  observation, MCP auth — when discovery or auth fails, shrink capability rather
  than assume.
- **Verify, don't trust the model's word.** Grounding and honesty are enforced in
  code (task contracts, verified file outputs), not in prompts.

## Commit & PR conventions

- **Commits:** a short imperative subject line (e.g. "Add command-risk case for
  inline interpreters"), optionally a brief body explaining the *why*. Group a
  change and its tests in the same commit. Prefer a new commit over amending
  someone else's.
- **Before you push:** the full backend suite passes, `ruff check .` is clean,
  and the frontend builds if you touched it.
- **PRs:** fill in the checklist in the PR template. Keep a PR focused on one
  change; don't fold unrelated refactors into it. Link the issue it closes.

## License

By contributing you agree your contributions are licensed under the project's
[MIT License](LICENSE).

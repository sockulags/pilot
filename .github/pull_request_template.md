<!--
Thanks for the PR. Keep it focused on one change; don't fold unrelated refactors
in. See CONTRIBUTING.md for the full conventions.
-->

## What & why

<!-- What does this change, and why? Link the issue it closes, if any. -->

Closes #

## How it was verified

<!-- What did you run/observe? Include the eval if you touched agent behaviour. -->

## Checklist

- [ ] `cd backend && uv run pytest -q` passes
- [ ] `cd backend && uv run ruff check .` is clean
- [ ] Frontend builds (`cd frontend && pnpm build`) — **if** `frontend/` was touched
- [ ] Tests added/updated for this change (backend tests stay Ollama-free and network-free)
- [ ] Local-first intact: any new dependency/API is **optional + env-gated** with a working fallback; the zero-config default path still works
- [ ] `-m eval` still green if I touched the coordinator, contracts, risk classifier, or evidence quarantine
- [ ] The change is focused; no unrelated refactors

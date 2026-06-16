# Runbook

## Local commands

- Backend tests: `cd backend; uv run pytest`
- Frontend typecheck/lint: `cd frontend; pnpm lint`
- Frontend production build: `cd frontend; pnpm build`
- Repo state: `git status --short`, `git diff --stat`

## Dispatch policy

- Delegate read-only diff review and design-gap analysis in parallel.
- Delegate local-model analysis as a bounded worker that may create exactly one findings document in a dedicated docs path.
- Keep integration, review decisions, frontend integration, and final verification in the controller session.

## Review gates

1. Accept only findings that are concrete, reproducible, and in scope.
2. Fix P1/P2 items before final commit.
3. Re-run validation after integration and again before claiming completion.

## Expected findings artifact

- Preferred path: `docs/model-findings-2026-06.md`

## Failure handling

- If backend tests fail due to pre-existing issues, isolate failures caused by this diff and document the rest in `status.md`.
- If frontend build or visual verification fails, repair locally before moving to commit.
- If delegated output conflicts with controller edits, prefer controller integration over direct cherry-pick.

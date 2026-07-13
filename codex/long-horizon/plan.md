# Long-Horizon Plan

## Milestone 1: Issue #62 — Model capabilities and role budgets

Goal: Discover declared model limits/capabilities, resolve conservative effective budgets per role, and pass explicit context to every Ollama chat request.
Owner mode: subagent-implement
Parallel group: M1 research may run alongside controller work; implementation is serialized.
Can run in parallel: no
Blocked by: none
Touched areas: `backend/config.py`, `backend/agents/model_inventory.py`, `backend/agents/providers.py`, direct local vision/router calls, settings/model discovery APIs, tests, environment/docs.
Acceptance criteria: all criteria in #62, including full-screen vision at >=8192, clamping, cloud-routing preservation, and deterministic tests.
Validation commands: `cd backend; uv run pytest -q; uv run pytest -q -m eval; uv run ruff check .`; focused live Ollama vision smoke when available.
Stop rule: do not add compaction, UI telemetry, or another runtime; repair all tests/review findings before PR/closure.
Status: completed

## Milestone 2: Issue #63 — Adaptive context manager

Goal: Build deterministic request budgeting, progressive compaction, completion reserve, and exactly-one overflow retry without losing safety contracts or verified evidence.
Owner mode: subagent-implement
Parallel group: read-only architecture/test research may run in parallel before implementation.
Can run in parallel: no
Blocked by: Milestone 1 merged
Touched areas: new context-management module, providers, coordinator/orchestrator prompt builders, vision/media estimation, diagnostics, tests/evals.
Acceptance criteria: all criteria in #63, including deterministic pressure levels, media/tool costs, one retry, and no repeated-perceive loop.
Validation commands: `cd backend; uv run pytest -q; uv run pytest -q -m eval; uv run ruff check .`.
Stop rule: do not implement the UI meter or runtime adapters; repair before continuing.
Status: completed

## Milestone 3: Issue #64 — Context telemetry UI

Goal: Emit/persist safe backend context reports and replace the frontend's hard-coded approximate denominator with truthful per-turn telemetry.
Owner mode: subagent-implement
Parallel group: frontend implementation follows the stable backend telemetry contract.
Can run in parallel: no
Blocked by: Milestone 2 merged
Touched areas: backend WS/session message schemas, frontend transcript/types/context dialog/styles/tests.
Acceptance criteria: all criteria in #64, including no chain-of-thought exposure, fallback for old history, responsive UI, and browser QA.
Validation commands: backend focused tests; `cd frontend; pnpm lint; pnpm test; pnpm build`; browser normal and near-limit flows.
Stop rule: do not add provider configuration; repair rendered and test findings before continuing.
Status: completed

## Milestone 4: Issue #65 — Pluggable local inference runtimes

Goal: Keep Ollama as default while adding a capability-aware local OpenAI-compatible adapter for LM Studio/llama.cpp, including local-only vision/embedding policy.
Owner mode: subagent-implement
Parallel group: read-only API/contract research may run before implementation.
Can run in parallel: no
Blocked by: Milestone 2 merged; Milestone 3 may be complete or its stable telemetry contract preserved.
Touched areas: provider/runtime interfaces, settings and API, model discovery, vision, embeddings/memory, health diagnostics, docs/tests.
Acceptance criteria: all criteria in #65, including loopback local endpoints, fail-closed capability probing, unchanged stock behavior, and screenshot privacy.
Validation commands: `cd backend; uv run pytest -q; uv run pytest -q -m eval; uv run ruff check .`; frontend checks if settings UI changes; available live runtime smoke.
Stop rule: do not install runtimes/models or claim unsupported combinations.
Status: completed

## Milestone 5: Issue #66 — Compatibility matrix and pressure evals

Goal: Prove provider normalization and context-pressure behavior with deterministic CI fixtures plus evidence-bound opt-in live smoke reports.
Owner mode: subagent-implement
Parallel group: docs matrix and fixture research may run in parallel if files are disjoint.
Can run in parallel: no
Blocked by: Milestones 1, 2, and 4 merged
Touched areas: backend provider tests, eval fixtures/scenarios/runner, live smoke scripts, compatibility docs.
Acceptance criteria: all criteria in #66, including >4096 full-screen case, retry/no-loop regression, exact version metadata, and no unverified support claims.
Validation commands: `cd backend; uv run pytest -q; uv run pytest -q -m eval; uv run ruff check .`; available live matrix commands.
Stop rule: mark unavailable combinations unverified; do not weaken deterministic CI.
Status: in_progress

## Milestone 6: Epic #67 completion audit

Goal: Verify every child and cross-cutting criterion against merged/current evidence, run broad regression and end-to-end browser/runtime checks, then close #67.
Owner mode: controller
Parallel group: independent read-only completion reviews may run in parallel.
Can run in parallel: yes
Blocked by: Milestones 1-5 complete
Touched areas: whole repository, GitHub issues/PRs, runtime/browser evidence.
Acceptance criteria: every item in `spec.md` Done When and #67 is proven; no unresolved review findings or scoped dirty changes.
Validation commands: full backend + eval + frontend checks; browser smoke; git diff/status audit; GitHub state audit.
Stop rule: do not close the epic on indirect or missing evidence.
Status: pending

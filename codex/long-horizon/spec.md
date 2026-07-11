# Long-Horizon Spec

## Goal

Complete GitHub issues #62 through #66 in dependency order and close epic #67 only after the full local-inference/context-management flow is implemented, independently reviewed, verified, merged to `master`, and the child issues are closed.

## Non-Goals

- Do not auto-install Ollama, LM Studio, llama.cpp, models, or drivers.
- Do not claim runtime/model support without reproducible evidence.
- Do not weaken the local-only boundary for screenshots or embeddings.
- Do not replace Pilot's safety/tool execution layer with runtime-provided tools.
- Do not mix unrelated existing worktree changes into issue branches or commits.

## Constraints

- Work one issue at a time: #62, #63, #64, #65, #66.
- Use test-first development where practical.
- Every milestone requires an implementer plus independent spec-compliance and code-quality review. A single reviewer may perform the two passes separately when concurrency is limited.
- Fix review findings, rerun validation, and re-review before acceptance.
- Preserve the current dirty worktree. `frontend/next-env.d.ts` is pre-existing/generated and must remain outside scoped commits unless a milestone explicitly requires it.
- The current uncommitted vision/context patch belongs to #62 and must be audited, completed, and included only after review.
- Ollama remains the zero-config default.
- CI must stay deterministic and must not require live model servers.
- Local runtime calls may use loopback endpoints without credentials; non-loopback local-only data must remain subject to the existing authenticated/private-network safety policy.
- Do not add Codex as a commit co-author.

## Deliverables

1. #62: truthful model capability inventory and explicit per-role runtime context budgets.
2. #63: central adaptive context manager with deterministic compaction and bounded overflow recovery.
3. #64: backend context telemetry and a truthful responsive UI meter.
4. #65: pluggable local runtime adapters for Ollama and OpenAI-compatible LM Studio/llama.cpp endpoints.
5. #66: deterministic provider contracts, context-pressure evals, opt-in live smoke tests, and a compatibility matrix.
6. #67: epic updated and closed after all children and end-to-end verification are complete.
7. Per-milestone commits/PRs/issues with validation evidence and review results.

## Done When

- Every acceptance criterion in #62-#66 is proven by current code, tests, runtime/browser evidence where required, and independent reviews.
- Each child issue is closed by a merged PR or an equally traceable merged commit.
- Stock Ollama UI-analysis flow observes a full-screen image and returns a grounded answer without context overflow or repeated-perceive loops.
- The context UI reports the effective budget and compaction/retry state from backend telemetry.
- At least one supported OpenAI-compatible local runtime completes the documented text/tool smoke flow, or the issue's explicit environment-independent contract plus available live evidence meets its acceptance criteria without overstating support.
- Full backend tests/evals, frontend lint/tests/build, diff review, and relevant browser smoke tests are green.
- Epic #67 is closed only after a requirement-by-requirement completion audit.

## Risks

- Context interfaces touch shared provider, coordinator, model inventory, WebSocket, persistence, and frontend types; implementation milestones must remain serialized.
- Model-declared context is much larger than safe effective context on 16 GB VRAM; policy must distinguish theoretical and effective limits.
- Provider tokenizers and multimodal accounting vary; conservative estimates and explicit uncertainty are required.
- Live LM Studio/llama.cpp binaries may not be installed; deterministic mocks must cover CI while live claims remain evidence-bound.
- Open PR #61 and issue #57 are unrelated and must not be absorbed into this program.

## Human Decisions Needed

None currently. The user explicitly authorized autonomous execution and subagent orchestration.

# Long-Horizon Status

## Current Milestone

Milestone 5 / issue #66 — local inference compatibility matrix and reproducible context-pressure evals. Implementation, repair, independent review, deterministic validation, and live evidence are complete; publication/CI/merge is next.

## Completed

- GitHub issues #62-#66 and epic #67 created with dependencies, acceptance criteria, validation, and stop rules.
- Real vision failures reproduced: hidden-thinking/empty-content and 4544-token request exceeding an effective 4096 context.
- The initial vision failure was integrated into #62 with shared role budgets, clamping, and capability discovery.
- Durable long-horizon planning artifacts created.
- First #62 implementation pass completed with 655 tests, 40 evals, Ruff, and live full-screen vision smoke green.
- Independent two-pass review completed and correctly blocked acceptance.
- Two repair/re-review loops closed live-limit propagation, real context-role wiring, bounded discovery, config validation, stale-cache invalidation, and unknown-model pre-discovery safety.
- Final independent spec-compliance and code-quality reviews are APPROVED with no actionable findings.
- PR #68 merged to `master` at `749280c`; backend/frontend CI passed and issue #62 closed.
- #63 implements deterministic request planning, completion reserve, 70/85/95 structural compaction, complete UTF-8/provider-payload estimation, bounded Ollama/OpenAI overflow recovery, direct-vision handling, safe diagnostics, and typed perception exhaustion.
- #63 independent review required two repair loops; final spec and quality passes are APPROVED with no actionable findings.
- PR #69 merged to `master` at `2b22fbe`; CI passed and issue #63 closed.
- Current `master` also includes unrelated completed work through `6a1a737`; the #64 branch was fast-forwarded before reconstruction.
- #64 was reconstructed from the recovered patch record, adapted to current `master`, and completed through three independent two-pass review cycles.
- #64 final independent spec-compliance and code-quality review is APPROVED with no actionable findings.
- Browser QA passed for legacy, normal, persisted reconnect, synthetic near-limit (4096 effective denominator), desktop and 375px mobile states with no console errors.
- PR #76 merged to `master` at `2ed4263`; backend/frontend CI passed and issue #64 closed.
- #65 now centralizes local endpoint policy and runtime snapshots, preserves stock Ollama, adds the generic local OpenAI-compatible contract for LM Studio/llama.cpp, and routes local chat/tools/vision/embeddings through the privacy boundary.
- #65 independent review found and repaired a DNS-rebinding hostname gap plus missing authenticated-Ollama headers; the re-review is APPROVED with no remaining findings.
- #65 browser QA passed for stock Ollama discovery/test, unsaved OpenAI-compatible controls, 375px mobile layout, persisted-settings protection, and console diagnostics.
- PR #77 merged to `master` at `b625752`; backend/frontend CI passed and issue #65 closed.
- #66 adds a network-free provider/context compatibility matrix, exact boundary and bounded-retry regressions, an opt-in evidence runner, stable comparison/source digests, and a documented support vocabulary.
- #66 independent review found and repaired unstable live-report comparison, missing exact boundary coverage, incomplete source identity and generic metadata, dishonest profile verdicts, and unverified diagnostic redaction; final re-review is APPROVED.
- #66 live evidence verifies Ollama 0.31.1 native and Ollama's generic OpenAI-compatible `/v1` facade for text/native tools plus separate `nomic-embed-text:latest` embeddings; native vision also passed a generated 1920x1080 long-task case estimated at 5,206 tokens under an 8,192 effective window. LM Studio and llama.cpp remain unverified.

## Active Blockers

None.

## Last Validation

- Before durable planning: backend full suite passed with 648 tests and Ruff clean for the initial vision patch.
- Live Ollama probe: 4096 context failed at 4544 tokens; the same request succeeded at 8192.
- Independent reviewer reran 655 backend tests, 40 evals, 92 focused tests, and Ruff successfully before requesting semantic changes.
- Final reviewer validation: 85 focused tests, 669 full backend tests, 40 evals, Ruff clean.
- Final controller validation: 669 full backend tests, 40 evals, Ruff clean, `git diff --check` clean apart from expected line-ending notices.
- #63 final reviewer validation: 705 full backend tests, 42 evals, 131 focused tests, Ruff and diff check clean apart from expected line-ending notices.
- #64 final controller validation: 715 full backend tests, 42 evals, Ruff clean, Pyright 0 errors, 18 frontend tests, TypeScript/ESLint 0 errors (3 unrelated pre-existing warnings), and production build green.
- #65 final controller validation: 749 full backend tests, 42 evals, Ruff clean, Pyright 0 errors, 18 frontend tests, TypeScript/ESLint 0 errors (3 pre-existing warnings), production build green, and `git diff --check` clean apart from the existing `backend/memory.py` line-ending notice.
- #65 live Ollama smoke: 7 models discovered, `gemma4:12b` returned a non-empty chat response, and `nomic-embed-text:latest` returned a 768-dimensional embedding. LM Studio and llama.cpp remain honestly unverified live because neither runtime is listening; their contracts are deterministic mocked tests only.
- #66 final controller validation: 776 full backend tests, 69 eval tests, Ruff clean, Pyright 0 errors, `git diff --check` clean apart from the existing results README line-ending notice, and live evidence comparison/source digests independently recomputed.

## Subagents

- `issue62_investigator`: DONE_WITH_CONCERNS; complete call-site/test audit delivered.
- `issue62_implementer`: DONE after two review-driven repair loops.
- `issue62_reviewer`: final APPROVED after two repair loops; both review gates passed.
- `issue63_investigator`: DONE_WITH_CONCERNS; prompt-path/retry/compaction audit delivered.
- `issue63_implementer`: interrupted after becoming unresponsive; shared edits preserved.
- `issue63_finisher`: completed first full implementation pass.
- `issue63_repair`: DONE after two review-driven repair loops.
- `issue63_reviewer`: final APPROVED; both review gates passed.
- `issue64_recovery`: DONE_WITH_CONCERNS; recovered the complete patch sequence from the original rollout, with no reachable commit available.
- `issue64_implementer_v2`: DONE after adapting the recovered implementation and two review-driven repair rounds.
- `issue64_reviewer_v2`: final APPROVED after three explicit spec-compliance and code-quality passes.
- `issue65_investigator`: DONE_WITH_CONCERNS; delivered the local-runtime/privacy call-site audit and validation matrix.
- `issue65_implementer`: DONE after one review-driven security/authentication repair loop.
- `issue65_reviewer3`: final APPROVED after explicit spec/privacy and code-quality passes plus repair re-review.
- `issue66_investigator`: DONE_WITH_CONCERNS; delivered the provider/eval/live-report gap audit and honest runtime matrix.
- `issue66_implementer`: DONE after one review-driven evidence/comparison repair loop.
- `issue66_reviewer`: final APPROVED after explicit acceptance/evidence and quality/determinism passes plus repair re-review.

## Known Issues

- `frontend/next-env.d.ts` is generated and out of scope; do not replay or include its prior unrelated modification.
- The lost #64 work had no commit or reachable dangling commit. Recovery source is rollout `019f534b-f84e-7f41-b152-e80b63a846e1`, whose successful patch records contain the complete implementation.
- Issue #57 and the Pyright work merged as #71 are unrelated and must remain intact.

## Next Action

Commit and publish #66, wait for GitHub CI, merge and close it, then perform the epic #67 requirement-by-requirement and end-to-end completion audit.

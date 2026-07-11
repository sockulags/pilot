# Long-Horizon Status

## Current Milestone

Milestone 2 / issue #63 — adaptive context manager. Implementation and independent two-pass review are approved; controller validation/publication is next.

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

## Active Blockers

None.

## Last Validation

- Before durable planning: backend full suite passed with 648 tests and Ruff clean for the initial vision patch.
- Live Ollama probe: 4096 context failed at 4544 tokens; the same request succeeded at 8192.
- Independent reviewer reran 655 backend tests, 40 evals, 92 focused tests, and Ruff successfully before requesting semantic changes.
- Final reviewer validation: 85 focused tests, 669 full backend tests, 40 evals, Ruff clean.
- Final controller validation: 669 full backend tests, 40 evals, Ruff clean, `git diff --check` clean apart from expected line-ending notices.
- #63 final reviewer validation: 705 full backend tests, 42 evals, 131 focused tests, Ruff and diff check clean apart from expected line-ending notices.

## Subagents

- `issue62_investigator`: DONE_WITH_CONCERNS; complete call-site/test audit delivered.
- `issue62_implementer`: DONE after two review-driven repair loops.
- `issue62_reviewer`: final APPROVED after two repair loops; both review gates passed.
- `issue63_investigator`: DONE_WITH_CONCERNS; prompt-path/retry/compaction audit delivered.
- `issue63_implementer`: interrupted after becoming unresponsive; shared edits preserved.
- `issue63_finisher`: completed first full implementation pass.
- `issue63_repair`: DONE after two review-driven repair loops.
- `issue63_reviewer`: final APPROVED; both review gates passed.

## Known Issues

- `frontend/next-env.d.ts` is modified/generated and unrelated to the program; preserve and exclude from scoped commits.
- Open PR #61 and issue #57 are unrelated.
- Current branch is `agent/issue-63-adaptive-context`; only unrelated `frontend/next-env.d.ts` is dirty before #63 edits.

## Next Action

Run controller validation, stage only #63 and durable status files, publish PR, obtain green CI, merge, and close #63 before #64.

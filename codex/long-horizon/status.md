# Long-Horizon Status

## Current Milestone

Milestone 1 / issue #62 — model capabilities and explicit per-role runtime budgets. Implementation and independent reviews are approved; controller validation is green and publication is next.

## Completed

- GitHub issues #62-#66 and epic #67 created with dependencies, acceptance criteria, validation, and stop rules.
- Real vision failures reproduced: hidden-thinking/empty-content and 4544-token request exceeding an effective 4096 context.
- An initial uncommitted #62 patch sets vision `think: false`, rejects empty content, surfaces errors, and sets vision context to 8192; it remains to be integrated with the full #62 model/role budget contract and reviewed.
- Durable long-horizon planning artifacts created.
- First #62 implementation pass completed with 655 tests, 40 evals, Ruff, and live full-screen vision smoke green.
- Independent two-pass review completed and correctly blocked acceptance.
- Two repair/re-review loops closed live-limit propagation, real context-role wiring, bounded discovery, config validation, stale-cache invalidation, and unknown-model pre-discovery safety.
- Final independent spec-compliance and code-quality reviews are APPROVED with no actionable findings.

## Active Blockers

None.

## Last Validation

- Before durable planning: backend full suite passed with 648 tests and Ruff clean for the initial vision patch.
- Live Ollama probe: 4096 context failed at 4544 tokens; the same request succeeded at 8192.
- Independent reviewer reran 655 backend tests, 40 evals, 92 focused tests, and Ruff successfully before requesting semantic changes.
- Final reviewer validation: 85 focused tests, 669 full backend tests, 40 evals, Ruff clean.
- Final controller validation: 669 full backend tests, 40 evals, Ruff clean, `git diff --check` clean apart from expected line-ending notices.

## Subagents

- `issue62_investigator`: DONE_WITH_CONCERNS; complete call-site/test audit delivered.
- `issue62_implementer`: first pass DONE; follow-up repair active after review.
- `issue62_reviewer`: CHANGES_REQUESTED; spec and quality findings recorded.
- `issue62_reviewer`: final APPROVED after two repair loops; both review gates passed.

## Known Issues

- `frontend/next-env.d.ts` is modified/generated and unrelated to #62; preserve and exclude from scoped commits.
- Open PR #61 and issue #57 are unrelated.
- Current branch is `master`; no #62 branch/PR exists yet.

## Next Action

Stage only #62 plus durable orchestration files, excluding `frontend/next-env.d.ts`; commit, push, open PR, obtain green CI, merge, and close #62 before #63.

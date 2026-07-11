# Long-Horizon Runbook

## Source Of Truth

Use `spec.md`, `plan.md`, `status.md`, `decisions.md`, GitHub issues #62-#67, and current Git/GitHub/runtime state. Live state overrides stale notes.

## Execution Rules

- Execute #62, #63, #64, #65, #66 in order; close #67 only after the completion audit.
- Re-read the active issue and current diff before every dispatch.
- Use subagents for isolated implementation/research and an independent reviewer for both required review gates.
- Each implementer must return `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`.
- Each reviewer must perform two explicit passes: spec compliance, then code quality.
- Fix every actionable finding and rerun the affected review.
- Use TDD where practical; retain the failing-test evidence in status notes.
- Validate focused tests first, then milestone-wide commands, then live/browser checks proportional to risk.
- Keep commits scoped; never stage `frontend/next-env.d.ts` unless the active milestone intentionally changes it.
- Do not add Codex co-author trailers.
- Prefer one issue branch/PR at a time. Merge/close the current issue before implementing its dependent successor.
- Update `status.md` and `decisions.md` after each review/validation/merge checkpoint.

## Git And Worktree Rules

- The initial dirty worktree contains an in-progress #62 vision/context fix plus an unrelated/generated `frontend/next-env.d.ts` change.
- Preserve user/unrelated changes. Inspect before staging and stage paths explicitly.
- Do not use destructive reset/checkout operations.
- Before branching or committing, confirm branch, remote, diff, and open PR state.
- Publish through a focused branch and draft/ready PR; ensure GitHub CI is green before merge.

## Subagent Prompt Checklist

- Task title and GitHub issue URL/number.
- Goal and exact current-state evidence.
- Relevant files/areas and worktree ownership.
- Constraints, dependencies, and non-goals.
- Acceptance criteria copied from the issue/spec.
- Required focused and broad validation commands.
- Explicit instruction to inspect existing changes rather than overwrite them.
- Required return status and structured summary.

## Review Checklist

### Spec compliance

- Every issue acceptance criterion is mapped to code/test/runtime evidence.
- Scope/non-goals are respected.
- Required validation exists and is credible.
- Documentation and configuration match behavior.

### Code quality

- Edge/error cases and privacy/safety boundaries are handled.
- Tests would fail for the original bug and cover shared behavior.
- Interfaces follow repo patterns and avoid duplicate token/context models.
- No hidden coupling, infinite retry, silent exception, or misleading telemetry.

## Stop Conditions

- A required external runtime/credential is unavailable and deterministic evidence cannot satisfy the stated criterion.
- Validation repeatedly fails with the same external blocker for three goal turns.
- A task requires a material scope decision not covered by the issues/spec.
- Existing user changes overlap irreconcilably with the active milestone.

Otherwise continue autonomously without asking whether to proceed.

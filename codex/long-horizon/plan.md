# Execution Plan

## Milestone 1: Plan, inspect, and classify work

Goal: Establish durable execution context, inspect the repo state, and separate controller work from safe delegated work.
Owner mode: controller
Parallel group: A
Can run in parallel: no
Blocked by: none
Touched areas: `codex/long-horizon/*`
Acceptance criteria:
- Long-horizon files exist and reflect the actual task.
- Repo state, stack, current diff, and design target are captured.
- Safe subagent tasks are identified.
Validation commands:
- `Get-ChildItem codex\\long-horizon`
- `git status --short`
Stop rule: Stop only if repo state is ambiguous enough to risk reverting user work.
Status: in_progress

## Milestone 2: Parallel investigations

Goal: Gather independent inputs for review, design alignment, and local model selection without blocking controller integration work.
Owner mode: subagent-research
Parallel group: B
Can run in parallel: yes
Blocked by: Milestone 1
Touched areas:
- Read-only repo inspection for review/design agents
- Findings doc path for model-analysis worker
Acceptance criteria:
- Review-oriented analysis identifies likely P1/P2 risks.
- Design-gap analysis compares current UI against `mockups/v3/02-inline.html`.
- Model-analysis worker writes findings to a dedicated project file.
Validation commands:
- Subagent return status review
- `git status --short`
Stop rule: If delegated scopes overlap or a subagent needs missing context, narrow and resend.
Status: pending

## Milestone 3: Controller review fixes and integration

Goal: Fix all accepted P1/P2 issues in backend/frontend code and integrate safe subagent outputs.
Owner mode: controller
Parallel group: C
Can run in parallel: no
Blocked by: Milestone 2 inputs for sidecar context only
Touched areas:
- `backend/**/*`
- `frontend/**/*`
- any findings doc added by delegated worker
Acceptance criteria:
- All controller-accepted P1/P2 findings are fixed.
- No unrelated user changes are reverted.
- Integrated code remains coherent across backend and frontend.
Validation commands:
- `cd backend; uv run pytest`
- `cd frontend; pnpm lint`
Stop rule: Stop if an unresolved blocker requires user product direction rather than engineering judgment.
Status: pending

## Milestone 4: Design-system rollout and visual match

Goal: Apply the new design system through the frontend and align the experience with `mockups/v3/02-inline.html`.
Owner mode: controller
Parallel group: D
Can run in parallel: no
Blocked by: Milestone 3
Touched areas:
- `frontend/app/page.tsx`
- `frontend/app/globals.css`
- `frontend/components/*`
- `frontend/styles/*`
Acceptance criteria:
- Layout, hierarchy, visual language, and key interactions clearly match the inline mockup.
- Existing UI functions still work after the redesign.
- Styling remains responsive on mobile and desktop.
Validation commands:
- `cd frontend; pnpm build`
- Browser/manual visual comparison against `mockups/v3/02-inline.html`
Stop rule: Stop if the app cannot be run locally for visual verification.
Status: pending

## Milestone 5: Final verification, commit, and reporting

Goal: Run broad verification, inspect final diff, commit the changes, and produce the orchestration summary.
Owner mode: controller
Parallel group: E
Can run in parallel: no
Blocked by: Milestone 4
Touched areas:
- whole repo
- git metadata
- `codex/long-horizon/status.md`
- `codex/long-horizon/decisions.md`
Acceptance criteria:
- Broadest reasonable checks have been run and results captured.
- Final diff is reviewed for unrelated edits.
- All intended files are committed.
- Final report includes what each worker did and three paths forward.
Validation commands:
- `git diff --stat`
- `git status --short`
- `git log -1 --stat`
Stop rule: Stop only if verification fails and cannot be repaired in-session.
Status: pending

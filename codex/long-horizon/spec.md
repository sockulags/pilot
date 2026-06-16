# Goal Spec

## Objective

Complete a local code review of the current uncommitted work, fix every P1 and P2 issue found, apply the project's new design system throughout the frontend, align the UI with `mockups/v3/02-inline.html`, run a delegated model-selection analysis for local models that are current in June 2026 and fit a 16 GB GPU budget, commit the final result, and report what each worker did plus three recommended next paths.

## Constraints

- Work on top of the current dirty worktree without reverting unrelated user changes.
- Use subagents only for safe parallel sidecar tasks or disjoint write scopes.
- Keep design work faithful to `mockups/v3/02-inline.html` and existing project structure.
- The model-analysis subagent must leave its findings in the repository.
- Final claims require fresh verification evidence.

## Non-goals

- No branch changes unless required later.
- No unrelated refactors outside review fixes and design-system rollout.
- No remote web research unless required for current facts; local model inventory should prefer local machine evidence first.

## Deliverables

- Fixed backend/frontend code for all discovered P1/P2 issues in the active diff.
- Frontend styling and structure aligned with `mockups/v3/02-inline.html`.
- A checked-in findings document for local-model recommendations on 16 GB GPU.
- Updated long-horizon status and decisions logs.
- One git commit containing the integrated work.

## Done Criteria

- Review findings are resolved or explicitly downgraded with justification.
- Frontend passes reasonable validation and visually tracks the target mockup closely.
- Backend and frontend verification commands pass, or any unavoidable failure is documented precisely.
- Findings file exists in the repo and is referenced in the final summary.
- Changes are committed.

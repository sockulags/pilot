# Decisions

- 2026-06-16: Work will proceed in the current checkout because the repo already contains active uncommitted changes tied to this goal.
- 2026-06-16: Long-horizon docs are stored under `codex/long-horizon/` as required by the orchestration skill.
- 2026-06-16: Controller owns integration, review triage, design-system rollout, verification, and commit; subagents are limited to bounded parallel sidecar tasks.
- 2026-06-16: The model-analysis task should prefer local machine evidence of installed/live models and write a findings document into the repo.
- 2026-06-16: Backend P2 fixes were implemented in-controller to keep review ownership centralized: diagnostic status is derived from emitted events, diagnostic file writes are non-fatal, and web-research source counts now preserve inferred user intent.
- 2026-06-16: Frontend integration reused the existing mockup-aligned CSS modules and replaced the prior narrow single-column shell with a full-shell React structure matching `mockups/v3/02-inline.html`.
- 2026-06-17: A follow-up pass was split into three low-cost, documentation-only subagent tracks to conserve usage: fidelity audit, productization backlog, and model-stack validation plan. Each worker owns exactly one output file under `docs/`.

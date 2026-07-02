# Live-eval findings — 2026-07-02

First runs of the **live-model eval runner** (`backend/tests/eval/live_runner.py`)
against the default local coordinator model `gemma4:12b`. This is the "live-model
mode" the public-demo scope (`docs/public-demo-scope.md` §4) requires: the real
agent, driven end to end, measured by deterministic checkers. The committed
report is `backend/tests/eval/results/latest.md` / `latest.json`.

## How to reproduce

From `backend/`:

```
uv run python -m tests.eval.live_runner
```

Fail-closed: if Ollama is down or `gemma4:12b` is not installed the runner exits
non-zero without inventing results. Exit code is 0 only when every safety gate
AND both primary-scenario tasks pass. (Run it directly — piping through `tee`
masks the non-zero exit code.)

## Result progression

The suite drove two coordinator fixes; solve rate climbed as each landed. All
three safety gates (confirmation + two injection-resistance tasks) held in every
run — injected file/memory instructions never escalated to a shell command, and a
destructive delete always halted for confirmation.

| Stage | Solved | Safety gates | What changed |
|---|---|---|---|
| Baseline | 5/10 | 3/3 held | every tool-using task failed |
| + decision-retry / nested-tool unwrap | 6/10 | 3/3 held | `shell_echo` fixed |
| + bounded compose grounding | **8/10** | 3/3 held | both Project Q&A tasks fixed |

Latency (final run): median ~42s, p90 ~180s per task on this machine.

## Bug #1 — coordinator spins instead of calling a tool

Every tool-using task originally failed for one reason: `gemma4:12b` frequently
*narrates a plan* ("Here's what I'll do: 1. …") instead of emitting a tool call.
Mid-contract that empty "answer" is blocked by the contract gate and the loop
spins to `max_steps` — the tool never fires (`shell_echo` ended with `tools=[]`).

A first fix (`_decide_step` re-asks once with a strict JSON-only instruction when
a prose plan arrives mid-contract) surfaced a second layer: under the re-ask the
model emits a *nested* shape —
`{"tool":"tool","args":{"tool":"run_command","args":{"cmd":"echo …"}}}` — so the
loop saw `tool="tool"` (unknown) and skipped it. `_unwrap_nested_tool` recovers
the real `{tool, args}` from the nested keys.

Both fixes are in `backend/agents/coordinator.py`, covered by
`backend/tests/test_decide_step_retry.py`.
**Before → after:** `shell_echo_token` went FAIL (`wrong_tool`, `tools=[]`,
`max_steps`) → PASS — `run_command` fires and the answer is
"Kommandot skrev ut: `pilot-eval`" with the contract satisfied.

## Bug #2 — answer collapses under a huge grounding block

With tools firing, the two Project Q&A tasks still failed `ungrounded_answer`:
the coordinator gathered all evidence correctly (the backend-flow playbook read
all six source files) but the final `compose_reply` was fed a ~26 000-char
activity-log block and `gemma4:12b` emitted **3 characters** ("Bas…") before
stopping.

Fix (`backend/agents/orchestrator.py` + `runtime_state.py`): a bounded compose
grounding budget. The structured evidence stays primary (its per-action summaries
shrink to `COMPOSE_ACTION_SUMMARY_CHARS`), the redundant text log takes whatever
budget remains, and every structured field (sources, files_read, requirements) is
preserved so grounding is never dropped. Covered by
`backend/tests/test_compose_evidence_bound.py`.
**Before → after:** the compose prompt dropped 26 154 → 11 911 chars and both
Project Q&A tasks went FAIL → PASS, naming real source files
(`api/ws.py`, …) in a substantive answer.

## Known remaining limitations (measured, not hidden)

These are real findings the suite exposes — model-capability limits, not runner
bugs — and they are left visible on purpose:

- **Research-to-file not verified (`missing_verification`, primary task).** The
  model runs `web_research` but then *claims* it wrote `report.md` without
  actually writing + verifying it; the `require_file_output` gate correctly
  refuses to report success. The production WS path has a fallback writer that
  guarantees the user still gets a verified file — the eval deliberately does not
  use it, so this measures the model's own multi-step reliability. This is the
  one primary-scenario task still red, and the clearest candidate to re-measure
  once the gate-8 OpenAI-API answering path lands.
- **File-count task is flaky (`wrong_answer` / `safety_over_block`).** The model
  either miscounts or wraps its command in an explicit `powershell -Command` /
  `cmd` invocation, which correctly trips the `PROCESS_SPAWN` risk class and halts
  for confirmation. Both are model command-quality issues, not a classifier bug.

## Why the suite still reports FAIL

Honestly: one of the two primary-scenario tasks (research-to-file) does not pass
unaided on `gemma4:12b`, so the scope's success bar ("the primary scenario's two
tasks pass on the default local model") is not fully met. The runner reports this
rather than masking it. Closing research-to-file — via a stronger answering model
and/or the planned OpenAI-API path — is the next unit of work for gate 8, and the
same harness will measure the delta.

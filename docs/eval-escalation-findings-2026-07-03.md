# Verification-driven escalation — does the "team" pay off? (2026-07-03)

Pilot's founding idea is *leverage small specialist models as a team* to build a
safe, useful, local tool. This experiment tests that idea directly, after first
answering the design question it hinges on: **how do we know a specialist is
needed, and who decides?**

## The mechanism: verification decides, not the model

The naive answer — let the small coordinator model choose when to consult a
specialist — is circular: it asks the model to self-assess the very competence
whose limits we're trying to detect, and small models are poor at knowing what
they don't know. So Pilot uses its existing "verify, don't trust" philosophy for
the escalation trigger too:

> A `code_task` runs a playbook: the lead model authors a solution → the **test
> suite is run** → on a **verified failure** the coordinator escalates authoring
> to a coder specialist (qwen2.5-coder / devstral) and re-verifies, bounded to a
> few attempts. The decision to escalate is the objective test result, not the
> model's opinion.

This is code-driven and measurable. The runner's `--escalate off|on` toggles it
with the attempt budget held equal, so an off-vs-on run isolates the specialist's
value (with off, retries stay on the lead; with on, retries go to the specialist).

Mechanism correctness is proven by unit tests (`tests/test_escalation.py`):
a verified failure IS recovered by escalating; escalation-off stays on the lead;
no coder available degrades gracefully; no spurious escalation on a first-attempt
pass.

## The measurement: the specialist never needed to fire

Six code tasks, each with a hidden pytest suite that the checker re-runs
**independently** (it does not trust the agent's own test run), spanning trivial
to LeetCode-Hard. Lead pinned to `gemma4:12b` (the weak generalist), escalation
target the installed coder specialists.

| Task | Difficulty | Trap | Result (lead alone) |
|---|---|---|---|
| roman_to_int | easy | subtractive notation | ✅ attempt 1 |
| is_balanced | easy | nested bracket types | ✅ attempt 1 |
| excel_column | medium | bijective base-26 | ✅ attempt 1 |
| min_coins | medium-hard | greedy ≠ DP ([1,3,4]→6) | ✅ attempt 1 |
| eval_rpn | medium-hard | truncate-toward-zero, not floor | ✅ attempt 1 |
| is_match | **LeetCode Hard #10** | `*` zero-or-more DP | ✅ attempt 1 |

**`gemma4:12b` solved all six on the FIRST attempt** — no verification failure, no
self-retry, and therefore no escalation, with escalation on or off. The
with/without delta is **zero** across the whole set.

## What this means for the founding thesis (honest)

The "specialist team" hypothesis is **not validated by these experiments** — but
note *why*, because it matters:

- **Not** because the mechanism is broken (unit tests prove it recovers failures).
- **Not** because the tasks were easy (one is LeetCode-Hard, two are classic
  general-model traps).
- Because **a 12B generalist plus the harness is already sufficient** for
  well-specified, test-covered coding tasks.

So the evidence points, again, to the conclusion from the earlier round:

> **The lever is the harness, not the model team.** Grounding contracts,
> tools-that-teach, and verification-gated completion turn a single small model
> into something that one-shots LeetCode-Hard. That is a stronger, cheaper and
> simpler story than multi-model orchestration — and it is measured, not asserted.

Where a specialist *would* pay off is a narrower band: tasks a 12B genuinely
cannot do but a 14B coder can. Six probes across the full difficulty spectrum did
not hit that band, which suggests it is narrow for objectively-testable coding
problems. Crucially, **the harness now measures this automatically** — the moment
such a task appears, the off-vs-on delta will surface it. The team mechanism is
built, correct, and idle until it's genuinely needed; that is the right place for
it to be.

## Caveats

- One model family (Ollama `gemma4:12b` lead). A weaker lead, or non-coding
  domains (deep math for `deepseek-r1`, long agentic repo work for `devstral`),
  might show a positive delta — untested here.
- These are self-contained, fully-specified problems with objective tests. Real
  work is fuzzier; the escalation trigger (a failing test) still applies wherever
  an objective check exists.

Backend suite green; escalation covered by unit tests + these live measurements.

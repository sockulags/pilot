# Live-eval findings — 2026-07-03: tool ergonomics + the model team

Continuation of [2026-07-02](eval-live-findings-2026-07-02.md). That round proved
the harness and showed the remaining failures were the **tool layer, not the
answering model**. This round acted on it, in two tracks: *tools that teach the model their
environment*, and *local models working as a team*. Every change was driven by a
measured failure and re-measured after.

## Result: local `gemma4:12b` 7/10 → **9/11**

| Task | Before | After | What changed |
|---|---|---|---|
| shell_count | ❌ flaky (wrong cmd / 329s on API) | ✅ ~52s local, ~5.5s API | explicit PowerShell + hints |
| file output (new task) | — (impossible by design) | ✅ local **and** API | first-class `write_file` |
| Project Q&A | ✅ 2/2 | ✅ 2/2 | held |
| Safety gates | 3/3 | 3/3 every run | held |
| research_to_file / grounded | ❌ | ❌ (environmental) | DDG rate-limited during final runs — see below |

## Track A — tools that teach the model

1. **`run_command` is now explicitly PowerShell** (was: `create_subprocess_shell`
   → cmd.exe, while every prompt example was PowerShell-flavoured). The result
   header states the shell; the registry description shows correct idioms. This
   also solved the 329s mystery: the model's `dir *.py | find /c ":"` resolved
   `find` to **Git's Unix find**, which walked the whole drive (~50s/spawn).
   Explicit PS runs the count in 0.5s.
2. **Failure output carries one actionable hint** (`tools/system.py::_HINT_RULES`):
   cmd-syntax-in-PS, command-not-found, parser errors, the Unix-find trap — each
   maps to "use X instead". A small model cannot know the environment; the tool
   must tell it.
3. **`web_research` explains itself and retries smarter**: a lite-endpoint search
   fallback, ONE deterministic simplified-query retry, URL dedupe across
   attempts, and failure text that says *why* (search failed / zero results / all
   fetches blocked) and *what to do* ("do not repeat the same query"). Observed
   effect: identical-call loops dropped from 6× to 2× and the model now reports
   the real cause ("403 Forbidden") instead of a vague failure.
4. **First-class `write_file`** — the biggest find. The eval exposed a design
   contradiction: file-output turns *required* a write while the risk classifier
   confirmation-gated **every** shell write (`Set-Content`/`Out-File`/`>`), so
   research-to-file was impossible to complete autonomously. `write_file` makes
   creating a NEW file inside the project a normal, verifiable act (no
   confirmation); overwriting an existing file, path traversal, and targets
   outside the project stay gated; read-only job profiles still cannot write at
   all (capability mapping now puts side-effects above category). Two live
   iterations refined the gates: gemma sets `overwrite=true` preemptively on new
   files (now only gated when something would actually be replaced), and models
   echo the absolute workspace path (now allowed when inside the project).
   gpt-4o-mini then exposed a rewrite loop — writing the same file repeatedly
   until gated — fixed by skipping re-writes once the turn's file output is
   verified ("you are done — answer now").

## Track B — the model team

5. **Consulted experts see the evidence.** `_consult_expert` now receives the
   turn's gathered (bounded, untrusted-wrapped) evidence, so a specialist answers
   grounded in what the coordinator actually found instead of blind.
6. **Experts can propose ONE command; the front brain vets and runs it.** A
   consult may end with `PROPOSED_COMMAND: <cmd>`; the coordinator passes it
   through **the same gates as its own decisions** (job profile, contract
   allowlist, risk/confirmation, repeat guard, tool budget) and executes only if
   clean — recorded as normal evidence. A destructive proposal is refused with a
   note; it never halts the turn and never bypasses safety. Covered by unit
   tests and two new deterministic scenarios (safe proposal executed; risky
   proposal refused).

## Honest remaining state

- **DDG rate-limiting** blocked both network tasks in the final runs (0 results
  with 403 on both endpoints — a day of eval traffic from one IP). The 22:16
  local run, before the block, showed retrieval working and grounded-answer
  passing. Re-run when unblocked; a longer-term fix is a second search provider.
- **research_to_file** now decomposes cleanly: retrieval (environmental today) →
  `write_file` (fixed, proven live on both backends) → verify (automatic). The
  new network-free `file_create_summary` task isolates the file-output leg so the
  suite distinguishes retrieval failures from write failures.
- Team behaviours (evidence-grounded consults, command proposals) are
  unit/scenario-tested; their live solve-rate impact will show on tasks that
  actually trigger consults, which the current 11-task suite rarely does — a
  candidate for a future suite extension.

Backend test suite: **473+ passing** (44 new this round).

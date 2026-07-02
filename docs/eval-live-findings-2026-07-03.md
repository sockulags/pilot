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

## Adversarial security review (the important part)

The whole changeset went through a 4-dimension adversarial review with per-finding
verification, focused on the two new capabilities that let the agent act more
autonomously — `write_file` (writes without confirmation) and expert command
proposals (executes a model-suggested command). It found **7 confirmed defects,
including two HIGH-severity security holes I had not caught by hand:**

1. **`write_file` sandbox escape (HIGH).** The confirmation gate decided
   "inside the project?" by resolving the path against `cwd` — but the *model*
   supplied `cwd`, and `apply_project_cwd_to_args` only filled it when absent. So
   `{path:"pwned.txt", cwd:"C:\\Windows\\Temp"}` escaped the project with no
   confirmation: silent arbitrary-location file creation. **Fixed:** the loop now
   *forces* `write_file`'s cwd to the trusted project base (overriding any model
   value), and the gate was rewritten as a uniform resolve-and-contain check —
   confirm unless the target resolves inside the trusted base and is not an
   overwrite. Verified: the exact escape payload now lands in the project, not
   `C:\Windows\Temp`.
2. **Expert-proposal prompt-injection → command execution (HIGH).** An expert
   answer is generated from evidence that can contain attacker-controlled web/file
   text, which could inject `PROPOSED_COMMAND: certutil -urlcache ...` — and the
   coordinator ran it because the *denylist* risk classifier doesn't recognise
   `certutil`/`python`/`schtasks`/`reg`/`net` as risky. **Fixed:** auto-executed
   proposals are now constrained by a **positive allowlist** of read-only
   inspection commands (Get-ChildItem/Get-Content/Select-String/Test-Path/git
   read subcommands, pipelines of them, no chaining/redirection/eval). Anything
   else is surfaced as a note, never run. Verified: every injection payload is
   refused; legitimate read-only proposals still run.

Plus five lower-severity fixes: a corrective command hint no longer attaches to a
*successful* command (exit code is now checked), the MCP `run_command_sync` path
now uses PowerShell too (shell consistency), `write_file` can no longer satisfy
`require_file_output` on an unverified write, and two `web_research` guidance
mislabels were corrected. New adversarial tests pin every one — including the
escape payload and the injection payloads as regression tests.

The lesson, again: the features that make the agent *more capable and autonomous*
are exactly the ones that need adversarial review — a self-probe found the
write_file path gate but missed the cwd escape and the injection sink entirely.

Backend test suite: **479 passing** (this round added tool ergonomics, the team
behaviours, write_file, and the security regression tests).

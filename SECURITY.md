# Security Policy

## Reporting a vulnerability

Email **lucasskog@gmail.com** with a description of the issue and, where possible,
steps to reproduce. Please do **not** open a public GitHub issue for anything that
includes exploit details — report it privately first.

There is no formal SLA on this project (it is a personal agent and a public code
sample, not a hosted service), but reports are read and acknowledged.

## Trust model

Pilot is a **local-first, single-user** agent that runs on one person's own Windows
machine with that person's own permissions. It is deliberately **not** a hosted,
multi-user, or internet-exposed product. The security posture follows from that:
the operator is trusted, the machine is trusted, and the network model is loopback
by default (optionally LAN behind something like Tailscale, with tokens set).

Understanding this boundary is what separates a security issue from a stated
limitation (below).

## Scope — what counts as a security issue

**In scope** (please report):

- A way to bypass one of the safety layers within its stated trust model — for
  example, a shell command that reaches a destructive/`install`/download-and-execute
  path **without** hitting confirmation, prompt-injected content in gathered evidence
  that escapes the `UNTRUSTED_EVIDENCE` quarantine and steers a tool call, or a
  desktop input action firing without fresh visual context.
- A network binding, auth, or token-comparison defect — e.g. a non-loopback bind
  that is **not** downgraded when its token is empty, or an auth check that can be
  defeated.
- A grounding/honesty gate that can be made to claim a file was written or an action
  succeeded when it was not.
- Any credential/token leak (e.g. an API key from the local settings file being
  returned to the browser or written to logs).

**Out of scope** (these are deliberate design limitations, not vulnerabilities —
see [Limitations](README.md#limitations-deliberate-scope)):

- Anything that assumes a multi-user or internet-exposed deployment. Exposing the
  backend publicly is explicitly unsupported.
- Risk that follows from the operator confirming a risky action. Confirmation-gated
  commands are meant to run once approved; that is the human-in-the-loop, not a bypass.
- Unreliability of small local models (inconsistent answers, missed tasks). This is
  a quality property measured in the eval, not a security boundary.
- The absence of features Pilot intentionally does not have: it does not enter
  credentials, perform financial transactions, or do irreversible bulk operations
  without explicit confirmation.

## Threat model summary

Pilot's defenses live in the agent harness, not in the model, and each is enforced
in code and covered by tests. In brief:

- **Network fail-closed** — loopback by default; a non-loopback host with an empty
  token is downgraded to `127.0.0.1` with a warning. WebSocket and MCP require a
  token when one is set, compared in constant time.
- **Command-risk classification** — every shell command is classified; read-only
  runs directly, while delete / write / install / encoded / download-and-execute /
  nested-shell / inline-interpreter / persistence-tool commands require explicit
  confirmation.
- **Prompt-injection quarantine** — everything gathered (tool output, web, memory,
  screen text) is wrapped as untrusted data with a "data, not instructions" rule;
  break-out attempts are defanged.
- **Desktop-action safety** — input tools are blocked without fresh visual context
  and when the observed window has changed.
- **Runaway guards** — per-command wall-clock timeout with process-tree kill,
  repeated-failure blocking, and per-job tool budgets for scheduled tasks.
- **Grounding / honesty gates** — task contracts gate answers on real evidence, and
  file outputs must be verified before completion is claimed.

The eval suite treats these layers as **pass/fail gates**: a single injection or
confirmation-gate failure fails the whole run regardless of the average score.

For the detailed table — each layer's exact behaviour, the source file that enforces
it, and the test that covers it — see
[Safety boundaries](README.md#safety-boundaries) in the README. That table is the
source of truth; this document summarizes it and should not diverge from it.

## Supported versions

There is a single rolling release: the `master` branch. Fixes land on `master`, and
that is the only supported version — there are no tagged releases, no LTS lines, and
no backports to older commits. If you are running Pilot, run a recent `master`.

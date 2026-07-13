# Long-Horizon Decisions

## Decisions

### 2026-07-11 — Serialize implementation by issue dependency

#62-#66 modify shared context/provider contracts, so implementation is serialized. Parallelism is limited to read-only investigation and independent review.

### 2026-07-11 — Keep Ollama as zero-config default

The runtime abstraction in #65 adds local alternatives without changing stock behavior or forcing duplicate model downloads.

### 2026-07-11 — Separate declared and effective context

Model metadata maximums are not safe runtime defaults on 16 GB VRAM. Pilot will expose both values and use conservative role budgets clamped to declared capability.

### 2026-07-11 — Preserve local-only data boundary

Screenshots and embeddings may use different local runtime adapters, but never a provider classified as cloud. Non-loopback behavior remains subject to existing network/auth safety policy.

### 2026-07-11 — Existing vision patch belongs to #62

The uncommitted vision changes are relevant evidence and implementation for #62, but are incomplete until all Ollama chat paths receive explicit role budgets and model inventory exposes limits. They must be reviewed as part of #62, not committed separately as an untracked hotfix.

### 2026-07-11 — Two-pass independent review

Each milestone receives explicit spec-compliance and code-quality passes. With limited agent slots, one independent reviewer may perform both passes sequentially, but the implementer may not self-approve.

### 2026-07-13 — Telemetry is per call and provider-visible

Different model calls may have different effective windows, so telemetry never sums them. Estimation uses the exact provider-visible payload while Pilot-only policy metadata remains available for deterministic compaction and category accounting.

### 2026-07-11 — Separate model routing role from context budget role

The first #62 review showed that reusing the provider `role` argument for both concerns either leaves real coordinator/code paths on the default budget or risks changing a preselected model. Provider calls will carry an independent context/budget role while model routing semantics remain unchanged.

### 2026-07-11 — Discovered maxima must constrain execution

`/api/show` metadata is not merely UI/inventory information. Once discovered, its declared maximum must be available to request-time payload resolution, with a conservative registry/unknown fallback and tests spanning discovery through actual chat payloads.

### 2026-07-13 — Local runtime locality fails closed at the hostname boundary

Local runtime URLs accept loopback IP literals or the exact `localhost` hostname, with `localhost` required to resolve exclusively to loopback. Arbitrary hostnames are rejected even when an initial lookup returns loopback, avoiding a second-resolution DNS-rebinding gap. Private IP literals additionally require explicit opt-in and a separate runtime credential, which is propagated across every Ollama and OpenAI-compatible operation.

## Assumptions

- GitHub CLI authentication remains available for issue/PR operations; the GitHub connector currently has read access but returned 403 for issue creation.
- Live Ollama is available for proportionate smoke tests; LM Studio/llama.cpp may be absent and must then remain marked unverified until available.
- The user authorizes autonomous continuation, subagent orchestration, commits, pushes, PRs, merges, and issue closure within #62-#67.

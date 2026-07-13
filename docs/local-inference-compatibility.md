# Local inference compatibility

Pilot separates deterministic provider-contract coverage from live runtime
evidence. A green mock contract proves the adapter shape; it does not prove that
an arbitrary runtime, model, quantization, or context setting works.

## Verdicts

- **Supported** — the exact runtime/model combination completed the named live
  scenario through Pilot's production local-runtime boundary.
- **Limited** — at least one named scenario passed, but a relevant capability is
  absent, failed, or was not exercised. The limitation is stated explicitly.
- **Unverified** — no successful live request exists for that exact combination.
  Discovery, an offline contract, or another product's compatible API is not a
  substitute.
- **Unsupported** — the runtime/model returned a definitive unsupported result,
  or Pilot rejects the capability by design. Unknown capabilities fail closed.

## Current evidence (2026-07-13)

The committed evidence is
[`backend/tests/eval/results/compatibility/2026-07-13-local-runtime-compatibility-v9.json`](../backend/tests/eval/results/compatibility/2026-07-13-local-runtime-compatibility-v9.json)
with a generated
[Markdown view](../backend/tests/eval/results/compatibility/2026-07-13-local-runtime-compatibility-v9.md).
It records schema version 1, full environment metadata and per-scenario budgets,
latency and provider token usage.

| Runtime/API profile | Exact version | Exact model | Digest / quantization | Declared / effective context | Live verdict |
|---|---|---|---|---:|---|
| Ollama native API | 0.31.1 | `qwen3.5:9b` | `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7` / `Q4_K_M` | 262144 / 8192 | **Limited overall**; supported live: text, native tool call, 1920x1080 vision + long task, and separate `nomic-embed-text` embeddings |
| Ollama OpenAI-compatible API | 0.31.1 | `qwen3.5:9b` | same full digest / `Q4_K_M`, queried from the proven underlying Ollama model store | 262144 / 8192 | **Limited overall**; supported live: text, native tool call, and separate `nomic-embed-text` embeddings through the generic adapter |
| LM Studio OpenAI-compatible API | not running | not loaded | not observed | unknown / 8192 configured | **Unverified** |
| llama.cpp OpenAI-compatible API | not running | not loaded | not observed | unknown / 8192 configured | **Unverified** |

The realistic vision fixture generates a 1920x1080 PNG in memory. Pilot's
conservative estimate was 5,206 prompt tokens—above 4,096 and below the 6,144
prompt budget left by an explicit 2,048-token completion reserve in the 8,192
window—and `qwen3.5:9b` returned a non-empty grounded response. The machine was
Windows 11, Python 3.12.13, AMD64 (16 logical CPUs), 65,805,840,384 bytes RAM,
and an NVIDIA GeForce RTX 5060 Ti with 16,311 MiB VRAM, driver 595.71.

The Ollama OpenAI-compatible profile is evidence for Pilot's **generic adapter**
against Ollama's `/v1` facade. It is deliberately not relabelled as LM Studio or
llama.cpp evidence.

Both embedding scenarios used `nomic-embed-text:latest` (requested by its
`nomic-embed-text` alias), digest
`0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`,
F16, not `qwen3.5:9b`. The report
records its own full digest, quantization and observed vector dimension under
`embedding_model` and the `embeddings` scenario result.

## Deterministic matrix (CI-safe)

From `backend/`:

```powershell
uv run pytest -q -m eval
uv run pytest -q tests/test_compatibility_eval.py
```

The declarative contracts live in `tests/eval/compatibility.py`. Scripted
transports exercise production budgeting and provider normalization with no DNS,
network or active model server. Coverage includes text/tool normalization,
OpenAI data URLs and fragmented SSE tool arguments, below/above budgets, a large
tool schema, an explicit completion reserve, exactly one successful compacted
overflow retry, a second overflow that stops after two total attempts, immutable
request objects, and fail-closed unknown/unsupported capabilities.

## Opt-in live matrix

Write reports outside the repository for routine runs, using a unique output
stem so history remains immutable:

```powershell
cd backend
uv run python -m tests.eval.compatibility_live --preset all `
  --output C:\path\outside\pilot-reports\compat-2026-07-13T2217
```

Use `--preset ollama`, `ollama-openai`, `lm-studio`, or `llama-cpp` to isolate a
profile. The runner refuses an existing `.json` or `.md` pair unless
`--overwrite` is explicitly supplied. It fails closed when an endpoint or model
is absent, writes `unverified` rows instead of fabricated results, and never
records endpoint URLs, API keys, environment values, response bodies on error,
or user/home paths.

LM Studio and llama.cpp do not expose one portable metadata endpoint. A
reachable generic preset therefore remains `unverified` and makes no live
support claim until all four identifying values are supplied for that single
run. Copy them from the running server's version view and exact loaded model
metadata (do not guess):

```powershell
uv run python -m tests.eval.compatibility_live `
  --preset lm-studio `
  --model exact-loaded-model-id `
  --runtime-version 0.3.30 `
  --model-digest exact-model-file-digest `
  --quantization Q4_K_M `
  --declared-context 32768 `
  --output C:\path\outside\pilot-reports\lm-studio-0.3.30-model-run
```

The report marks these fields `user_supplied_cli`; it never presents them as
auto-discovered. Missing any value prevents scenario measurement and yields an
`unverified` profile. Metadata overrides are rejected for `all`, `ollama`, and
`ollama-openai` runs because those profiles use Ollama's authoritative metadata.

Reports use stable schema keys plus a comparison key that recursively excludes
timestamps, git state, latency, token usage, free-form details, and the stored
key itself while retaining verdicts, budgets, model metadata, and the source
digest. The source digest binds the Git commit identity, tracked diff, and
untracked source content. Keep exact JSON as the machine-readable authority;
Markdown is a generated human view.

## Stop rule

Do not claim runtime, model, tool, vision, structured-output, embedding, context,
or quantization support unless the exact combination has a successful live
scenario in a retained report. Mark all other combinations **unverified** or
**unsupported**. CI must remain network-free; live evidence is always opt-in.

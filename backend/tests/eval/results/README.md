# Live-eval results

`latest.json` / `latest.md` are the most recent live-model eval report, produced by:

```
uv run python -m tests.eval.live_runner      # from backend/
```

(Run it directly — piping through `tee` masks the non-zero exit code.)

The report measures the real agent against `gemma4:12b`: solve rate per category,
latency (median/p90), a failure taxonomy, and pass/fail safety gates. See
`docs/eval-live-findings-2026-07-02.md` for the analysis of the first run and the
coordinator fixes it drove.

## Local-runtime compatibility evidence

The deterministic provider/context matrix is part of pytest and never opens a
socket:

```powershell
uv run pytest -q -m eval
uv run pytest -q tests/test_compatibility_eval.py
```

The separate live runner is opt-in and requires a caller-selected output stem:

```powershell
uv run python -m tests.eval.compatibility_live --preset all `
  --output C:\path\outside\pilot-reports\compat-2026-07-13T2217
```

Prefer an external output directory for normal runs. The runner refuses to
overwrite prior `.json`/`.md` files unless `--overwrite` is explicit. A reviewed,
redacted evidence pair may be committed under `results/compatibility/`; never
commit API keys, endpoint URLs, environment dumps, response bodies, or user
paths. An absent runtime/model is `unverified`, never support. CI does not run
this command and needs neither network nor a model server.

A reachable LM Studio/llama.cpp endpoint still remains `unverified` until a
single-preset run supplies `--runtime-version`, `--model-digest`,
`--quantization`, and `--declared-context`; see
`docs/local-inference-compatibility.md` for the exact command. The report records
those values as user-supplied provenance rather than pretending they were
auto-discovered.

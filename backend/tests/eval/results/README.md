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

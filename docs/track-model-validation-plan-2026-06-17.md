# Track 3 - model stack validation follow-up

Date: 2026-06-18

This plan turns the current local findings into a short validation queue for choosing the safest default stack on this machine. It is specific to the installed Ollama models and to the repo's existing config surface in `backend/config.py`, `start-backend.bat`, and `README.md`.

## What we already know

- `devstral:latest`, `gpt-oss:20b`, `qwen3.5:9b`, `gemma4:12b`, `qwen2.5-coder:14b`, and `deepseek-r1:14b` are installed locally and appear to fit on a 16 GB GPU one-at-a-time.
- `gemma4:latest` is redundant after validation: it overlaps with `gemma4:12b`, failed the red-image vision smoke probe, and should no longer be referenced by defaults.
- The repo now defaults to `OLLAMA_MODEL=gemma4:12b` and `OLLAMA_VISION_MODEL=qwen3.5:9b` in startup docs and the Windows launcher.
- `backend/config.py` treats `OLLAMA_ROUTER_MODEL` as the classifier/router, `OLLAMA_GATEWAY_MODEL` as the refinement model, `OLLAMA_FALLBACK_MODEL` as the reasoning fallback, and `OLLAMA_VISION_ENABLED` as the switch for image-based perception.
- The remaining uncertainty is app-level behavior under real WebSocket turns, not basic model availability.

## Validation experiments

1. Router tool-use sanity check
- Run a mixed set of routing turns in auto mode: a simple chat, a code request, and a tool-driving request that should trigger the JSON tool router.
- Success signal: `gemma4:12b` remains fast enough as the coordinator, picks the correct specialist for code, and does not stall on tool calls or produce malformed tool routing.
- Failure signal: repeated misroutes, slow first-token latency, or malformed tool call output that makes the computer route fall back.
- Config affected if adopted: already updated in `backend/config.py`, `start-backend.bat`, `README.md`, and `GETTING_STARTED.md`.

2. Swedish refinement check
- Keep `gemma4:12b` as the gateway unless app-level turns show refinement drift.
- Success signal: the gateway preserves meaning, especially for short imperative code requests, with fewer mistranslations than the smaller model.
- Failure signal: obvious semantic drift, wrong verb/object interpretation, or a broken handoff prompt that changes the task.
- Config affected if adopted: already updated/confirmed in `backend/config.py`.

3. Code specialist check
- Run a small code-focused benchmark set through the auto stack, pinned `devstral:latest`, and pinned `qwen2.5-coder:14b`, then compare edits, explanation quality, and whether each stays on-task.
- Success signal: `devstral:latest` handles agentic repo work well, while `qwen2.5-coder:14b` remains the quicker snippet/small-fix specialist.
- Failure signal: no clear win over `gemma4:12b` / `gpt-oss:20b`, or worse tool/insert behavior than expected.
- Config affected if adopted: the `qwen2.5-coder:14b` entry in `backend/config.py`'s `OLLAMA_MODELS` registry, and any UI/default copy that names the code specialist.

4. Reasoning fallback comparison
- Compare `gpt-oss:20b`, `qwen3.5:9b`, and `deepseek-r1:14b` on hard reasoning prompts and on turns that may need tool calls, with attention to how often each model can safely stay in the automatic stack.
- Success signal: `gpt-oss:20b` is the better all-around fallback because it returns clean content and native tool-calls, while `deepseek-r1:14b` remains stronger only for manual deep reasoning.
- Failure signal: `gpt-oss:20b` is too slow for practical fallback, or `qwen3.5:9b` proves reliable enough to replace it for most auto turns.
- Config affected if adopted: `OLLAMA_FALLBACK_MODEL` and the `OLLAMA_MODELS` registry in `backend/config.py`.

5. Vision runtime check
- Exercise the screenshot/image flow with `OLLAMA_VISION_ENABLED=true` and `OLLAMA_VISION_MODEL=qwen3.5:9b`, then repeat with vision disabled as a control.
- Success signal: the model accepts image input at runtime and the done-summary path produces a grounded visual description without errors.
- Failure signal: Ollama rejects image input, summaries are nonsense, or the vision path is slower or less stable than the plain text perception path.
- Config affected if adopted: `OLLAMA_VISION_ENABLED` and `OLLAMA_VISION_MODEL` in `backend/config.py`, plus the matching defaults in `start-backend.bat` and the docs.

6. Memory and embedding health check
- Run a few representative memory writes and retrievals using the local embed model, then verify that cross-session recall stays useful and does not swamp unrelated turns.
- Success signal: `nomic-embed-text` returns stable retrieval quality and the current thresholds still keep relevant memories while filtering noise.
- Failure signal: recall is too sparse, too noisy, or adds latency that makes the default stack feel worse than expected.
- Config affected if adopted: `OLLAMA_EMBED_MODEL`, `MEMORY_TOP_K`, and `MEMORY_MIN_SCORE` in `backend/config.py`.

## Safest next default pending validation

Use the validated default stack for the next app-level test pass:
`OLLAMA_MODEL=gemma4:12b`, `OLLAMA_VISION_MODEL=qwen3.5:9b`,
`OLLAMA_FALLBACK_MODEL=gpt-oss:20b`, with `devstral:latest`,
`qwen2.5-coder:14b`, and `deepseek-r1:14b` remaining specialist/pinned choices.

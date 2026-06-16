# Track 3 - model stack validation follow-up

Date: 2026-06-17

This plan turns the current local findings into a short validation queue for choosing the safest default stack on this machine. It is specific to the installed Ollama models and to the repo's existing config surface in `backend/config.py`, `start-backend.bat`, and `README.md`.

## What we already know

- `gemma4:latest`, `gemma4:12b`, `qwen3:14b`, `qwen2.5-coder:14b`, and `deepseek-r1:14b` are all installed locally and appear to fit on a 16 GB GPU one-at-a-time.
- The repo currently defaults to `OLLAMA_MODEL=gemma4:latest` and `OLLAMA_VISION_MODEL=gemma4:latest` in startup docs and the Windows launcher.
- `backend/config.py` treats `OLLAMA_ROUTER_MODEL` as the classifier/router, `OLLAMA_GATEWAY_MODEL` as the refinement model, `OLLAMA_FALLBACK_MODEL` as the reasoning fallback, and `OLLAMA_VISION_ENABLED` as the switch for image-based perception.
- The remaining uncertainty is not whether the models exist, but which one should be the safest default for the router, gateway, and vision path on this specific machine under real load.

## Validation experiments

1. Router tool-use sanity check
- Run a mixed set of routing turns in auto mode: a simple chat, a code request, and a tool-driving request that should trigger the JSON tool router.
- Success signal: `gemma4:latest` remains fast enough as the coordinator, picks the correct specialist for code, and does not stall on tool calls or produce malformed tool routing.
- Failure signal: repeated misroutes, slow first-token latency, or malformed tool call output that makes the computer route fall back.
- Config affected if adopted: `OLLAMA_MODEL`, `OLLAMA_ROUTER_MODEL` in `backend/config.py`, plus `start-backend.bat` and the defaults documented in `README.md` / `GETTING_STARTED.md`.

2. Swedish refinement check
- Compare `gemma4:latest` versus `gemma4:12b` as the gateway on a few Swedish prompts that require faithful translation into clean English before specialist handoff.
- Success signal: the gateway preserves meaning, especially for short imperative code requests, with fewer mistranslations than the smaller model.
- Failure signal: obvious semantic drift, wrong verb/object interpretation, or a broken handoff prompt that changes the task.
- Config affected if adopted: `OLLAMA_GATEWAY_MODEL` in `backend/config.py`.

3. Code specialist check
- Run a small code-focused benchmark set through the auto stack and through a pinned `qwen2.5-coder:14b` session, then compare edits, explanation quality, and whether it stays on-task.
- Success signal: `qwen2.5-coder:14b` consistently produces better code and fewer repairs than the general models, justifying it as the specialist to consult rather than the default router.
- Failure signal: no clear win over `gemma4:latest` / `qwen3:14b`, or worse tool/insert behavior than expected.
- Config affected if adopted: the `qwen2.5-coder:14b` entry in `backend/config.py`'s `OLLAMA_MODELS` registry, and any UI/default copy that names the code specialist.

4. Reasoning fallback comparison
- Compare `qwen3:14b` and `deepseek-r1:14b` on hard reasoning prompts and on turns that may need tool calls, with attention to how often each model can safely stay in the automatic stack.
- Success signal: `qwen3:14b` is the better all-around fallback because it is tools-capable and behaves reliably in the auto path, while `deepseek-r1:14b` remains stronger only for manual deep reasoning.
- Failure signal: `deepseek-r1:14b` proves clearly better on practical reasoning without needing tools, or `qwen3:14b` underperforms enough that it should not stay the fallback.
- Config affected if adopted: `OLLAMA_FALLBACK_MODEL` in `backend/config.py`, plus the `tools` flag in the `OLLAMA_MODELS` registry if policy changes.

5. Vision runtime check
- Exercise the screenshot/image flow with `OLLAMA_VISION_ENABLED=true` and `OLLAMA_VISION_MODEL=gemma4:latest`, then repeat with vision disabled as a control.
- Success signal: the model accepts image input at runtime and the done-summary path produces a grounded visual description without errors.
- Failure signal: Ollama rejects image input, summaries are nonsense, or the vision path is slower or less stable than the plain text perception path.
- Config affected if adopted: `OLLAMA_VISION_ENABLED` and `OLLAMA_VISION_MODEL` in `backend/config.py`, plus the matching defaults in `start-backend.bat` and the docs.

6. Memory and embedding health check
- Run a few representative memory writes and retrievals using the local embed model, then verify that cross-session recall stays useful and does not swamp unrelated turns.
- Success signal: `nomic-embed-text` returns stable retrieval quality and the current thresholds still keep relevant memories while filtering noise.
- Failure signal: recall is too sparse, too noisy, or adds latency that makes the default stack feel worse than expected.
- Config affected if adopted: `OLLAMA_EMBED_MODEL`, `MEMORY_TOP_K`, and `MEMORY_MIN_SCORE` in `backend/config.py`.

## Safest next default pending validation

Keep the current default stack in place for now: `OLLAMA_MODEL=gemma4:latest`, `OLLAMA_VISION_MODEL=gemma4:latest`, `OLLAMA_FALLBACK_MODEL=qwen3:14b`, with `qwen2.5-coder:14b` and `deepseek-r1:14b` remaining specialists rather than defaults. That is the safest choice until the router, gateway, and vision checks above all pass on this machine.


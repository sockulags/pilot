# Local model findings for Pilot on 16 GB GPU

Date: 2026-06-18

## Observed locally

- GPU: `NVIDIA GeForce RTX 5060 Ti`, `16311 MiB` VRAM (`nvidia-smi`).
- Runtime: `ollama.exe` is installed at `C:\Users\lucas\AppData\Local\Programs\Ollama\ollama.exe`.
- Current loaded models before validation: none (`ollama ps` was empty).
- Repo defaults:
  - `OLLAMA_MODEL=gemma4:12b`
  - `OLLAMA_VISION_MODEL=qwen3.5:9b`
  - `OLLAMA_VISION_ENABLED=true`
  - `OLLAMA_FALLBACK_MODEL=gpt-oss:20b` in `backend/config.py`

### Installed local models

| Model | Local size | Observed capabilities | Likely 16 GB fit |
|---|---:|---|---|
| `devstral:latest` | 14 GB | completion, tools | Yes, but largest |
| `gpt-oss:20b` | 13 GB | completion, tools, thinking | Yes, but largest |
| `qwen3.5:9b` | 6.6 GB | completion, vision, tools, thinking | Yes |
| `gemma4:12b` | 7.6 GB | completion, vision, audio, tools, thinking | Yes |
| `gemma4:latest` | 9.6 GB | completion, vision, audio, tools, thinking | Yes, redundant |
| `deepseek-r1:14b` | 9.0 GB | completion, tools, thinking | Yes |
| `qwen2.5-coder:14b` | 9.0 GB | completion, tools, insert | Yes |
| `nomic-embed-text:latest` | 274 MB | embeddings | Yes |

### 2026-06-18 runtime probes

Small Ollama API probes were run for Swedish refinement, native tool-calling,
code generation, vision, and embeddings.

| Model | Swedish refine | Tool-call probe | Code probe | Vision probe |
|---|---|---|---|---|
| `gemma4:12b` | Passed; preserved "reverse a string" | Native `tool_calls` | Thinking-heavy, usable with enough budget | Passed red image |
| `gpt-oss:20b` | Passed, but slower | Native `tool_calls` | Passed with clean code | Not a vision model |
| `qwen3.5:9b` | Thinking-heavy; short content can be empty | Native `tool_calls` | Thinking-heavy; short content can be empty | Passed red image |
| `devstral:latest` | Passed | Native `tool_calls` | Passed, but ignored "no markdown" | Not a vision model |
| `qwen2.5-coder:14b` | Passed | Emits OpenAI-style JSON in content, which app fallback handles | Passed | Not a vision model |
| `deepseek-r1:14b` | Thinking-heavy; short content can be empty | Did not produce a tool call | Thinking-heavy; short content can be empty | Not a vision model |
| `gemma4:latest` | Thinking-heavy on short refinement | Native `tool_calls` | Verbose/unfinished under short budget | Failed red-image smoke probe |
| `nomic-embed-text:latest` | N/A | N/A | N/A | Embedding smoke passed, 768 dimensions |

## Inferred fit and tradeoffs

- All installed chat/coder models appear usable on this machine one-at-a-time,
  but `devstral` and `gpt-oss` are large enough that frequent handoffs can cause
  noticeable reload latency.
- Do not assume multiple 12B to 14B models will stay resident together. On this GPU, frequent expert handoffs may cause unload/reload churn and latency spikes.
- `gemma4:12b` is the best local default for the coordinator/router role in this repo:
  - passed Swedish refinement and tool-call probes
  - tools-capable
  - less prone than `qwen3.5`/`deepseek-r1` to returning only thinking text on
    short non-tool prompts
- `gpt-oss:20b` is the strongest installed general/research fallback to keep in
  the automatic stack. It is slower, but returned clean content and native
  tool-calls.
- `devstral:latest` and `qwen2.5-coder:14b` both remain useful code specialists:
  `devstral` for agentic repo work, `qwen2.5-coder` for quicker code snippets.
- `deepseek-r1:14b` is useful for hard analysis, but in this repo it is intentionally marked `tools: false` in the local registry, so it should not be the default model for computer/tool-driving turns even if Ollama metadata now advertises tool capability.
- Vision is the main uncertainty:
  - `qwen3.5:9b` correctly answered the red-image smoke probe
  - `gemma4:12b` also passed the smoke probe
  - `gemma4:latest` answered incorrectly on the same probe, so it should not be the vision default

## Recommended default stack

- Default coordinator/router: `gemma4:12b`
- Gateway/refinement model: `gemma4:12b`
- Code specialists: `devstral:latest` for agentic repo work, `qwen2.5-coder:14b` for quicker code
- Research/reasoning fallback: `gpt-oss:20b`
- Deep analysis fallback: `deepseek-r1:14b` for pinned/manual use, not tool-driving auto turns
- Embeddings: `nomic-embed-text:latest`
- Vision: `qwen3.5:9b`
- Redundant model to remove: `gemma4:latest`; it overlaps with `gemma4:12b`,
  failed the vision smoke test, and is no longer referenced by the app defaults.

## Bottom line

This environment is usable for Pilot after aligning config with the installed
models. The safest default stack is `gemma4:12b` as the front brain/gateway,
`gpt-oss:20b` for research fallback, `devstral` plus `qwen2.5-coder` for code,
`qwen3.5:9b` for vision, and `nomic-embed-text` for memory.

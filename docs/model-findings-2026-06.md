# Local model findings for Pilot on 16 GB GPU

Date: 2026-06-16

## Observed locally

- GPU: `NVIDIA GeForce RTX 5060 Ti`, `16311 MiB` VRAM (`nvidia-smi`).
- Runtime: `ollama.exe` is installed at `C:\Users\lucas\AppData\Local\Programs\Ollama\ollama.exe`.
- Current loaded models: none (`ollama ps` was empty).
- Repo defaults:
  - `OLLAMA_MODEL=gemma4:latest`
  - `OLLAMA_VISION_MODEL=gemma4:latest`
  - `OLLAMA_VISION_ENABLED=true`
  - `OLLAMA_FALLBACK_MODEL=qwen3:14b` in `backend/config.py`

### Installed local models

| Model | Local size | Observed capabilities | Likely 16 GB fit |
|---|---:|---|---|
| `gemma4:12b` | 7.6 GB | completion, vision, audio, tools, thinking | Yes |
| `gemma4:latest` | 9.6 GB | completion, vision, audio, tools, thinking | Yes |
| `qwen3:14b` | 9.3 GB | completion, tools, thinking | Yes |
| `deepseek-r1:14b` | 9.0 GB | completion, tools, thinking | Yes |
| `qwen2.5-coder:14b` | 9.0 GB | completion, tools, insert | Yes |
| `nomic-embed-text:latest` | 274 MB | embeddings | Yes |

## Inferred fit and tradeoffs

- All installed chat/coder models appear usable on this machine one-at-a-time. Their on-disk sizes are all below 10 GB, so a single active model is a reasonable fit for a 16 GB GPU.
- Do not assume multiple 12B to 14B models will stay resident together. On this GPU, frequent expert handoffs may cause unload/reload churn and latency spikes.
- `gemma4:latest` is the best local default for the coordinator/router role in this repo:
  - already configured as default
  - tools-capable
  - highest context among the installed front-brain candidates shown here
  - `ollama show` reports vision support, which is newer than the repo comment claiming Gemma 4 is text-only
- `qwen2.5-coder:14b` is the strongest installed specialist for code tasks. It should stay a consulted specialist, not the default router.
- `qwen3:14b` is the best general reasoning specialist to keep in the automatic stack. It is tools-capable and already used as fallback in config.
- `deepseek-r1:14b` is useful for hard analysis, but in this repo it is intentionally marked `tools: false` in the local registry, so it should not be the default model for computer/tool-driving turns even if Ollama metadata now advertises tool capability.
- Vision is the main uncertainty:
  - observed local metadata says `gemma4:latest` supports vision
  - repo comments still describe Gemma 4 as text-only
  - current config is therefore plausible, but should be treated as "runtime-valid until proven otherwise"

## Recommended default stack

- Default coordinator/router: `gemma4:latest`
- Gateway/refinement model: `gemma4:12b`
- Code specialist: `qwen2.5-coder:14b`
- Reasoning specialist: `qwen3:14b`
- Deep analysis fallback: `deepseek-r1:14b` for pinned/manual use, not tool-driving auto turns
- Embeddings: `nomic-embed-text:latest`
- Vision: keep `gemma4:latest` for now because local Ollama metadata says it supports vision; if runtime validation fails, replace it with a dedicated multimodal model before relying on screenshot/image flows

## Bottom line

This environment is already in a usable state for a 16 GB GPU. The safest default is to keep `gemma4:latest` as the front brain, use `qwen2.5-coder:14b` for code and `qwen3:14b` for reasoning, and treat vision support as locally observed but worth validating at runtime because repo comments lag the installed model metadata.

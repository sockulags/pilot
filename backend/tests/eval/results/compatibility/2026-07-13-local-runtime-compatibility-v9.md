# Pilot local inference compatibility evidence

- Schema: `1`
- Timestamp (UTC): `2026-07-13T20:40:21+00:00`
- Git: `b625752dd174b3a09d6b73d5166236a565d382f2` (dirty: `true`)
- Source digest: `6d88e27daee12640417c1c04f74c2402112c6d01b5c88a7fc830d829d8957130`
- Comparison key: `4119fa828b5493873d9f766625641d88d6ac9b038c5271a9fb2d2bf67b45bec7`

| Runtime profile | Version | Chat/vision model | Digest | Quant | Embedding model | Context | Availability | Verdict |
|---|---:|---|---|---|---|---:|---|---|
| Ollama native API | 0.31.1 | qwen3.5:9b | 6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7 | Q4_K_M | nomic-embed-text:latest / 0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f / F16 | 8192 | available | limited |
| Ollama OpenAI-compatible API | 0.31.1 | qwen3.5:9b | 6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7 | Q4_K_M | nomic-embed-text:latest / 0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f / F16 | 8192 | available | limited |
| LM Studio OpenAI-compatible API | unknown | qwen3.5:9b | unknown | unknown | not tested | 8192 | unverified | unverified |
| llama.cpp OpenAI-compatible API | unknown | qwen3.5:9b | unknown | unknown | not tested | 8192 | unverified | unverified |

## Scenario verdicts

| Profile | Scenario | Verdict | Estimated prompt | Latency ms | Detail |
|---|---|---|---:|---:|---|
| ollama | text | supported | 33 | 3640.69 | contract satisfied |
| ollama | native_tool | supported | 103 | 822.37 | contract satisfied |
| ollama | below_budget | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama | above_budget | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama | vision_long_8192 | supported | 5206 | 2951.85 | contract satisfied |
| ollama | large_tool_schema | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama | overflow_retry_once | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama | overflow_twice | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama | embeddings | supported | n/a | 35.04 | contract satisfied |
| ollama-openai | text | supported | 33 | 11002.83 | contract satisfied |
| ollama-openai | native_tool | supported | 103 | 1678.51 | contract satisfied |
| ollama-openai | below_budget | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama-openai | above_budget | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama-openai | vision_long_8192 | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama-openai | large_tool_schema | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama-openai | overflow_retry_once | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama-openai | overflow_twice | unverified | n/a | n/a | deterministic contract only; not issued to the live runtime |
| ollama-openai | embeddings | supported | n/a | 37.92 | contract satisfied |
| lm-studio | text | unverified | n/a | n/a | runtime/model unavailable |
| lm-studio | native_tool | unverified | n/a | n/a | runtime/model unavailable |
| lm-studio | below_budget | unverified | n/a | n/a | runtime/model unavailable |
| lm-studio | above_budget | unverified | n/a | n/a | runtime/model unavailable |
| lm-studio | vision_long_8192 | unverified | n/a | n/a | runtime/model unavailable |
| lm-studio | large_tool_schema | unverified | n/a | n/a | runtime/model unavailable |
| lm-studio | overflow_retry_once | unverified | n/a | n/a | runtime/model unavailable |
| lm-studio | overflow_twice | unverified | n/a | n/a | runtime/model unavailable |
| lm-studio | embeddings | unverified | n/a | n/a | runtime/model unavailable |
| llama-cpp | text | unverified | n/a | n/a | runtime/model unavailable |
| llama-cpp | native_tool | unverified | n/a | n/a | runtime/model unavailable |
| llama-cpp | below_budget | unverified | n/a | n/a | runtime/model unavailable |
| llama-cpp | above_budget | unverified | n/a | n/a | runtime/model unavailable |
| llama-cpp | vision_long_8192 | unverified | n/a | n/a | runtime/model unavailable |
| llama-cpp | large_tool_schema | unverified | n/a | n/a | runtime/model unavailable |
| llama-cpp | overflow_retry_once | unverified | n/a | n/a | runtime/model unavailable |
| llama-cpp | overflow_twice | unverified | n/a | n/a | runtime/model unavailable |
| llama-cpp | embeddings | unverified | n/a | n/a | runtime/model unavailable |

`unverified` means no successful live request was made. It is not a support claim.
Secrets, endpoint URLs, environment values, and user paths are intentionally absent.

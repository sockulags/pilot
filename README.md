# Pilot

[![CI](https://github.com/sockulags/pilot/actions/workflows/ci.yml/badge.svg)](https://github.com/sockulags/pilot/actions/workflows/ci.yml)

**A local-first, tool-using AI agent that carries out — and then verifies — grounded desktop-and-web tasks on a Windows machine.**

Pilot runs on your machine, on local [Ollama](https://ollama.com) models by default. You talk to it in natural language (Swedish or English); it classifies the turn, gathers what it needs (reads project files, runs read-only shell commands, does web research), performs the action, and answers **only from evidence it actually gathered**. When a task produces a file, Pilot writes it and verifies it exists before claiming success.

![A grounded task end-to-end: Pilot reasons about the next step, runs a PowerShell command, and answers from the verified command output — on a local gemma4:12b model](docs/screenshots/grounded-task.png)

<!-- DEMO PLACEHOLDER: a short screencast (GIF/MP4) of one live turn — classify →
     route → tool call → grounded answer — still needs to be recorded on a real
     machine and dropped in at docs/screenshots/demo.gif, then linked here.
     A static screenshot stands in above until then. -->
> **Demo clip coming soon.** A short screencast of a full live turn will land at
> `docs/screenshots/demo.gif`; the static screenshot above stands in for now.

**Trust model / intended user:** a technically comfortable person running Pilot on their own machine, who wants an assistant that can actually touch their files, shell and screen — not a cloud chatbot — and accepts "it runs on my machine with my permissions." Pilot is a working personal agent and a public code sample, **not** a hosted multi-user product, and this README never claims production-grade reliability: see [Evaluation](#evaluation--measured-not-claimed) for what is actually measured.

---

## Quick start

Requirements: [Ollama](https://ollama.com) with `gemma4:12b` pulled, [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io) + Node 18+. Optional: [ComfyUI](https://github.com/comfyanonymous/ComfyUI) for image generation.

```bash
# Backend — FastAPI + WebSocket on :8000, MCP server on :3001
cd backend
uv run python main.py

# Frontend — http://localhost:3000
cd frontend
pnpm install && pnpm dev
```

Configuration is env-based (`backend/.env`, template in `backend/.env.example`). The important ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `gemma4:12b` | Default coordinator/answer model |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama URL |
| `PILOT_ANSWER_BACKEND` | `ollama` | `ollama` (fully local) or `openai` — see [Model backends](#model-backends-local-first-api-optional) |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | Only used when the backend is `openai` |
| `BACKEND_HOST` / `MCP_HOST` | `127.0.0.1` | Loopback by default; expose deliberately only behind a private network **and** with tokens set — a non-loopback host with an empty token is downgraded to `127.0.0.1` with a warning |
| `PILOT_AUTH_TOKEN` / `PILOT_MCP_AUTH_TOKEN` | _(empty)_ | Shared secrets for the WebSocket `hello` and the MCP endpoints |
| `COMMAND_TIMEOUT_SECONDS` | `60` | Wall-clock bound for one `run_command`; the process tree is killed on timeout |
| `COORDINATOR_MAX_STEPS` | `6` | Max consults/tool calls one turn may chain |

The full list (memory, ComfyUI, code agents, scheduled jobs) is in `backend/.env.example` and `backend/config.py`.

---

## Architecture

One user turn flows through four stages, all in `backend/`:

```
WebSocket (api/ws.py)
  └─ classify_turn (agents/orchestrator.py)      what kind of turn is this?
      └─ RoutingDecision (agents/routing.py)     explainable engine choice, surfaced to the UI
          └─ run_coordinator (agents/coordinator.py)
              • decides the next step via native tool-calling
              • runs OS/web tools, consults installed specialist models
              • task contracts gate the answer on real evidence
              └─ compose_reply (agents/orchestrator.py)
                    final answer, grounded in (bounded) gathered evidence
```

The same turn as a flow — WebSocket in, grounded reply out, with the coordinator's
in-turn loop over tools and specialist experts in the middle:

```mermaid
flowchart TD
    WS["WebSocket<br/>api/ws.py"] --> C["classify_turn<br/>agents/orchestrator.py"]
    C --> R["RoutingDecision<br/>agents/routing.py<br/><i>explainable engine choice</i>"]
    R --> COORD["run_coordinator<br/>agents/coordinator.py<br/><i>the front brain</i>"]

    subgraph loop["in-turn loop (bounded by COORDINATOR_MAX_STEPS)"]
        direction LR
        COORD -->|tool| TOOLS["OS / web / desktop tools<br/>tools/registry.py"]
        COORD -->|consult| EXPERTS["specialist models<br/>code · research · reasoning"]
        COORD -->|perceive| PERC["screen perception<br/>Set-of-Marks"]
        TOOLS -->|evidence| COORD
        EXPERTS -->|evidence| COORD
        PERC -->|evidence| COORD
    end

    COORD --> GATE["task contracts<br/>agents/task_contracts.py<br/><i>gate answer on real evidence</i>"]
    GATE --> COMPOSE["compose_reply<br/>agents/orchestrator.py"]
    COMPOSE --> OUT["grounded reply → WebSocket"]
```

- **Coordinator ("front brain")** — a fast local model drives an in-turn loop: `consult` a specialist (code → `devstral`/`qwen2.5-coder`, research → `gpt-oss`, hard reasoning → `deepseek-r1`), `perceive` the screen, run a tool, `remember` a durable fact, `clarify`, or `answer`. Only models actually installed in Ollama are offered as experts (fail-closed inventory, `agents/model_inventory.py`).
- **Task contracts** (`agents/task_contracts.py`) — a research turn cannot claim completion without fetched sources; a file-creation turn cannot claim completion without a **written and verified** artifact; a project-analysis turn must actually read the playbook files first. The answer gate is enforced in code, not prompted.
- **Perception** — screenshot + a Set-of-Marks element list via Windows UI Automation (`PERCEPTION_ENABLED`), so desktop actions target known element centers. Works without a vision model; a multimodal model only adds a visual description.
- **Language gateway** (`agents/gateway.py`) — vague requests get one clarifying question instead of a guess; hand-offs to specialists are refined into a clear English instruction (local models reason better in English) while the reply stays in your language.
- **Long-term memory** (`memory.py`) — a small semantic store (embeddings via `nomic-embed-text`) recalled each turn and written via the coordinator's `remember` action.
- **Frontend** — Next.js chat UI streaming every thinking/action/result event live, with per-session Läge/Modell/Agent toggles.

### Model backends (local-first, API optional)

The model-driven calls — classification, the tool-decision loop, expert consults, final synthesis — go through one provider layer (`agents/providers.py`) with two backends:

- **`ollama`** (default): everything stays on your machine.
- **`openai`**: those calls go to an OpenAI-compatible API (`OPENAI_MODEL`, default `gpt-4o-mini`). Perception/vision and memory embeddings **always stay local**.

This is a deployment lever, not a mode switch buried in code: local for privacy and zero cost, the API path for harder multi-step tasks — with the trade-off measured (below) rather than asserted. **Privacy note:** on the `openai` backend, gathered evidence (file contents, screen text, web results) is sent to the API.

### Model settings — per-role models, cloud + local mixed

The env variables above set the **default** model chain, but the settings page (the ⚙ button, or `PUT /api/settings/models`) makes model selection a runtime, per-role decision that overrides those defaults:

- **Default runs everything.** With no settings saved, behaviour is exactly the env-driven default: `OLLAMA_MODEL` on the `ollama` backend. The settings layer only ever *overrides* — delete `backend/data/model_settings.json` to restore stock behaviour.
- **A model per role.** Roles are the agent roles the auto-picker chooses between per turn intent (`default_agent`, `research_agent`, `code_agent`, `quick_code_agent`, `deep_reasoning_agent`, `vision_agent`) and the fixed pipeline stages every turn runs through (`classifier`, `gateway`, `synthesis`). Any role can be pinned to a specific model; unassigned roles inherit the default assignment ("default runs everything else").
- **Cloud and local, mixed freely.** Add any number of OpenAI-compatible providers (OpenAI, OpenRouter, Groq, Mistral, or a custom base URL) with their own key and model list. A role can then run on a cloud model while everything else stays on local Ollama — e.g. research on a frontier cloud model, code and chat local. Cloud models are encoded as `cloud:<provider>:<model>` ids and flow through the same provider layer, so routing, consults and the eval meta handle them unchanged.
- **Fail closed.** A role pointing at a disabled/missing provider, or an Ollama model that isn't installed, silently falls back to the default chain with a recorded reason — a bad settings file never takes a turn down. API keys live only in the local settings file (gitignored) and are never returned to the browser; `vision` and memory embeddings stay local by design.

The settings page is fed by live discovery (`GET /api/models/available`): the installed Ollama models and each configured cloud provider's reachability, with a per-provider "Test" button.

---

## Safety boundaries

Layered, each enforced in code and covered by tests:

| Layer | What it does | Where |
|---|---|---|
| Network fail-closed | Binds loopback by default; WebSocket requires an authenticated `hello` when a token is set; MCP requires a bearer token; constant-time comparisons | `config.py`, `api/ws.py`, `api/mcp.py`; `tests/test_ws_auth.py`, `tests/test_mcp_auth.py` |
| Command risk classification | Each shell command is classified (delete / write / install / encoded / download-and-execute / nested shell …). Read-only runs directly; risky requires explicit confirmation | `tools/command_risk.py`; `tests/test_command_risk.py` |
| Prompt-injection quarantine | Everything gathered (tool output, web, memory, screen text) is wrapped in `UNTRUSTED_EVIDENCE` blocks with a "data, not instructions" rule; break-out attempts are defanged | `agents/untrusted.py`; `tests/test_untrusted.py` |
| Desktop action safety | Input tools are blocked without visual context, and when the observation is stale or the active window changed | `agents/safety.py`; `tests/test_freshness.py` |
| Runaway guards | Per-command wall-clock timeout with process-**tree** kill; identical failing commands blocked on the 3rd attempt; per-job tool budgets for scheduled tasks | `tools/system.py`, `agents/coordinator.py`, `job_permissions.py` |
| Grounding / honesty | Contracts gate answers on evidence; file outputs must be verified; tool-backed replies are guarded against raw-log or false-action-claim answers | `agents/task_contracts.py`, `agents/turn_policy.py` |

The eval suite treats the safety layers as **pass/fail gates** — a single injection or confirmation-gate failure fails the whole suite regardless of the average score.

---

## Evaluation — measured, not claimed

Two suites, both in `backend/tests/eval/`:

1. **Deterministic replay suite** (`runner.py`, `scenarios.py`) — 40+ golden and adversarial scenarios (prompt injection in files/web/memory/screen, contract gating, capability profiles, the command-risk escalation surfaces) that run in CI with no model and no network. Screen perception is stubbed, so the suite never takes a real screenshot or makes a live vision call.
2. **Live-model runner** (`live_runner.py`) — drives the **real agent** end to end against a live model with deterministic checkers: solve rate per category, latency (median/p90), a failure taxonomy, per-task tokens/cost, and hard safety gates. The task set spans single-turn scenarios (project Q&A, shell, research-to-file, grounded current-info, code) and **multi-turn** ones (memory round-trip, confirmation-then-approve).

```bash
cd backend
uv run python -m tests.eval.live_runner                    # local backend
uv run python -m tests.eval.live_runner --backend openai   # OpenAI backend
uv run python -m tests.eval.live_runner --trials 3         # per-task variance
```

Each run records a **reproducibility block** (git commit + dirty flag, OS/Python, exact model digest/quantization and Ollama version), archives an immutable copy under `results/history/`, and renders a **"change vs previous run"** delta so a regression is visible at a glance. `--trials N` runs every task N times and reports per-task pass rate and latency spread — small local models are noisy, and the report shows it rather than hiding behind one flappy verdict.

Reports land in `backend/tests/eval/results/` (`latest.*` committed; `history/` local). A prior 10-task run, local vs API (the suite has since grown — re-run to refresh these numbers):

| Metric | Local `gemma4:12b` | OpenAI `gpt-4o-mini` |
|---|---|---|
| Solve rate | 7–8/10 | 8/10 |
| Latency median / p90 | ~42s / ~222s | ~11s / ~31s |
| Cost per run | $0 (local) | ~$0.03 |
| Safety gates (injection, confirmation) | **3/3 held** | **3/3 held** |

Two honest takeaways from running this:

- **Safety behaviour is backend-independent** — the defenses live in the agent (contracts, risk classifier, evidence quarantine), not the model.
- **The remaining failures are the tool layer, not the LLM.** Both backends miss the research-to-file task because web retrieval returned no readable sources, and a file-count task because of shell-command quality — a bigger model doesn't fix either. The eval exists precisely to find this kind of thing: it has so far driven fixes for a coordinator decision-spin, a compose-grounding collapse, a missing command timeout, and a repeated-command loop, each verified red→green. Full analysis: [`docs/eval-live-findings-2026-07-02.md`](docs/eval-live-findings-2026-07-02.md).

---

## Limitations (deliberate scope)

- **Not** autonomous multi-application workflows with no user in the loop.
- **Not** built for guaranteed reliability or unattended operation; small local models are visibly inconsistent on some task types (see the eval variance notes).
- **Not** multi-user or internet-exposed hosting — the network model is single-user loopback/LAN (optionally behind e.g. Tailscale with tokens).
- **No** account/credential entry on your behalf, financial transactions, or irreversible bulk operations without explicit confirmation.
- Vision is optional; when no multimodal model is available, desktop input tools are **blocked** rather than run blind.
- Web retrieval quality bounds research tasks — the eval shows this is the current weakest link, not the answering model. Retrieval is pluggable: it scrapes DuckDuckGo out of the box (zero-config), and routes through a resilient JSON search API (Tavily/Brave-style) when you set `PILOT_SEARCH_PROVIDER` + `PILOT_SEARCH_API_KEY`, falling back to the scraper if no key is set.

---

## Design decisions

- **Local-first, API-optional.** Privacy and zero marginal cost by default; a measured, selectable API path where capability/latency genuinely pays for it — the eval quantifies the trade instead of guessing.
- **Verify, don't trust the model's word.** Task contracts gate answers on recorded evidence; file outputs require a verification command. The agent's honesty is a property of the harness, not the prompt.
- **Fail closed everywhere.** Model inventory, network exposure, desktop input without observation, MCP auth — when discovery or auth fails, capabilities shrink instead of assuming.
- **Explainable routing.** Every turn carries a `RoutingDecision` (route, engine, reason, required permissions) surfaced to the UI before anything expensive or risky runs.
- **Measure before polishing.** The evaluation task set and success criteria were defined *before* demo work ([`docs/public-demo-scope.md`](docs/public-demo-scope.md)), and every fix the suite drove is documented with before/after runs.

## Project structure

```
pilot/
├── backend/
│   ├── main.py                 # Entrypoint (FastAPI + MCP)
│   ├── config.py               # Env-based config
│   ├── model_settings.py       # runtime per-role model settings (providers + roles)
│   ├── agents/
│   │   ├── orchestrator.py     # classify_turn + compose_reply (final answer layer)
│   │   ├── coordinator.py      # in-turn tool/consult loop (the "front brain")
│   │   ├── providers.py        # model backends: ollama | openai | cloud providers, role-aware
│   │   ├── routing.py          # explainable RoutingDecision
│   │   ├── task_contracts.py   # evidence-gated completion contracts
│   │   ├── untrusted.py        # prompt-injection quarantine
│   │   └── safety.py           # desktop-action guards
│   ├── tools/                  # registry + run_command, files, web, screen/input, extras (grep/http/pdf/procs/clipboard)
│   ├── tests/eval/             # deterministic replay suite + live-model runner
│   └── api/                    # ws.py (WebSocket), mcp.py (MCP), settings.py (model settings REST)
└── frontend/                   # Next.js chat UI (+ SettingsPanel for model roles)
```

## Tools

The agent's tools are declared once in `tools/registry.py` (single source of truth for the coordinator menu, the loop's behaviour sets, the MCP manifest and the native function-call schemas). Beyond the OS/desktop and web basics, the set covers:

| Tool | What it does |
|---|---|
| `search_in_files` | grep file **contents** (text or regex) for "where does X live" — filename search finds files, this finds the lines |
| `read_document` | extract text from a **PDF** (page by page, via pypdf) or a text file — the research/"find my CV" flows land on PDFs |
| `http_request` | call a **JSON/HTTP API** (method, headers, body, params) — distinct from `fetch_url`, which returns page text; a non-GET method requires confirmation |
| `list_processes` | list running processes (name, pid, memory) — "is Ollama running?", read-only |
| `read_clipboard` / `write_clipboard` | read or set the OS clipboard — "summarize what I copied", "copy that result" |

Everything side-effecting flows through the same layered safety model (command-risk classification, contract allowlists, prompt-injection quarantine). The command-risk classifier now treats inline interpreter execution (`python -c`, `node -e`), direct executable invocation (`.\setup.exe`), Windows persistence tools (`reg`, `schtasks`, `sc`) and `find -delete/-exec` as confirmation-gated, closing the earlier default-allow gap for unrecognised commands.

## MCP integration

The backend exposes computer-control tools over MCP (`http://localhost:3001/mcp`, SSE): `pilot_screenshot`, `pilot_click`, `pilot_type`, `pilot_run_command`, `pilot_open_app`, file tools and more. Guard it with `PILOT_MCP_AUTH_TOKEN` before exposing beyond loopback.

## Contributing

Setup, tests (`uv run pytest`, `ruff`), running the eval, code layout and commit
conventions live in [CONTRIBUTING.md](CONTRIBUTING.md). A one-command Windows
bring-up is in [`scripts/dev.ps1`](scripts/README.md).

## License

MIT — see [LICENSE](LICENSE).

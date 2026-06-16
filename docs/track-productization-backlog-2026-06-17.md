# Track 2 - productization backlog for session/memory surfaces

Scope note: this backlog is grounded in the current implementation in `frontend/app/page.tsx`, `frontend/components/*`, and the backend session/memory/job plumbing under `backend/*`.

## MVP ordering

1. Make drawer/session browsing real and persistent.
2. Turn the context surface into an actionable memory/session control panel.
3. Expose memory recall and memory actions in the UI.
4. Make session state resumable, not just reloadable.
5. Productize project selection and working-directory feedback.
6. Tighten scheduled-job management into a proper surface.
7. Add state/error feedback so users can trust what the surfaces are doing.

## Backlog

### 1) Drawer: replace prompt-only session list with a real session browser

- User-visible outcome: the drawer shows actual saved sessions, their titles/summaries, project/cwd, agent, model mode, route mode, and last activity, not just user prompts filtered from the current transcript.
- Likely files: `frontend/app/page.tsx`, `backend/store.py`, `backend/api/ws.py`.
- Dependency/risk note: the backend currently persists sessions, but the drawer only reads the live transcript. This needs a backend session index or list endpoint; otherwise the UI cannot show more than the active session.

### 2) Drawer: make session actions first-class

- User-visible outcome: users can rename a session, pin/favorite it, reopen it, and clear or delete it from the drawer with immediate feedback.
- Likely files: `frontend/app/page.tsx`, `frontend/components/ActionLog.tsx`, `backend/store.py`, `backend/api/ws.py`.
- Dependency/risk note: `handleReset` currently creates a fresh session id and clears the transcript, but there is no durable session management UI. Delete/rename actions need backend support and a safe confirmation flow to avoid accidental data loss.

### 3) Context surface: show real memory and session context instead of an approximate token bar only

- User-visible outcome: the context modal shows what is actually being sent to the agent: recent conversation, retrieved memories, project cwd, active agent/model/route settings, and an explicit “what is trimmed” summary.
- Likely files: `frontend/app/page.tsx`, `frontend/components/ActionLog.tsx`, `backend/api/ws.py`, `backend/memory.py`, `backend/store.py`.
- Dependency/risk note: `ContextModal` currently uses `approximateTokens()` and hard-coded system/skills estimates. To productize this, the backend should expose real context composition or at least a stable context report per turn.

### 4) Memory surface: add a browsable memory list with delete and “remember this” controls

- User-visible outcome: users can see saved memories, search them, delete them, and promote a useful turn into long-term memory from the UI.
- Likely files: `backend/memory.py`, `backend/api/ws.py`, `frontend/app/page.tsx`, `frontend/components/ActionLog.tsx`.
- Dependency/risk note: `backend/memory.py` already supports `list_memories()` and `delete_memory()`, but the frontend does not expose them. A capture action also needs a clear rule for what text gets saved so the feature does not become noisy or duplicative.

### 5) Memory recall: surface retrieved memories inline during a turn

- User-visible outcome: when the backend recalls memories for a prompt, the transcript or context panel shows which memories were used and why they mattered.
- Likely files: `backend/api/ws.py`, `backend/memory.py`, `frontend/components/ActionLog.tsx`, `frontend/app/page.tsx`.
- Dependency/risk note: `search_memories()` already exists and `handle_message()` injects the formatted block into the prompt, but the user cannot inspect the retrieval result. Without visibility, memory feels magical rather than trustworthy.

### 6) Session persistence: make resume state explicit and recoverable

- User-visible outcome: after reload or reconnect, users can tell whether they resumed the same session, what project/model/agent/route were active, and whether any coding-agent sub-session was resumed too.
- Likely files: `backend/store.py`, `backend/api/ws.py`, `frontend/app/page.tsx`.
- Dependency/risk note: `store.py` already persists `cwd`, `agent`, `model_mode`, `route_mode`, and coding-agent session ids, but the frontend does not present them as resumable state. This needs careful handling around the current `hello` handshake and the reset flow.

### 7) Project surface: make project selection feel like a workspace switch, not a dropdown

- User-visible outcome: the project control clearly communicates the active workspace, lets users add/remove roots confidently, and shows the working directory in session and assistant output consistently.
- Likely files: `frontend/components/ProjectBar.tsx`, `frontend/app/page.tsx`, `backend/projects.py`, `backend/api/ws.py`.
- Dependency/risk note: project selection already updates `cwd` and clears coding-agent session ids on switch, but the UI does not explain that consequence. The risk is silent context loss when users switch workspaces.

### 8) Jobs surface: connect scheduled jobs to the same session/memory model as chat

- User-visible outcome: scheduled jobs show which session they belong to, what context they will run with, and whether they produced a memory, reminder, or task result that can be opened from the drawer.
- Likely files: `frontend/components/JobsPanel.tsx`, `frontend/app/page.tsx`, `backend/jobs.py`, `backend/api/ws.py`.
- Dependency/risk note: `jobs.py` already persists schedules and `send_jobs()` returns summaries, but the panel is still a thin CRUD view. Productizing it means linking job execution back into transcript/session history and clarifying how task jobs differ from reminders.

## Recommendation

Start with the drawer and context surfaces, because they are the main entry points for session/memory trust and they already have the most visible scaffold. Then connect memory browsing and session resume, and only after that expand jobs and project switching into richer workspace controls.

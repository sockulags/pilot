# Browser-only context telemetry fixture

`context_near_limit_session.json` is inert test data. It reaches the frontend
only when a developer deliberately copies it into the local session store.

From the repository root:

```powershell
Copy-Item backend/tests/fixtures/context_near_limit_session.json backend/data/sessions/browser-context-near-limit.json
```

In the browser console on the local Pilot page:

```js
localStorage.setItem("pilot_session_id", "browser-context-near-limit"); location.reload();
```

Open the context dialog and verify the near-limit status and the 4096-token
backend denominator. Remove `backend/data/sessions/browser-context-near-limit.json`
after QA. No production endpoint or client-side mutation hook is enabled.

# Track 1 - Fidelity Audit vs `docs/design/02-inline.html`

Date: 2026-06-17

Scope: current frontend implementation in `frontend/app/page.tsx` plus the components and styles it composes, compared against `docs/design/02-inline.html`.

## Top remaining mismatches

1. The session drawer still reads like a prompt filter, not the mockup's session browser.
   - Mockup: left drawer is a dedicated session list with a prominent "new conversation" action, search, and session metadata rows.
   - Current UI: `frontend/components/ActionLog.tsx` and `frontend/components/TaskInput.tsx` are unrelated to the drawer, while `frontend/components/ProjectBar.tsx` exposes project/model controls in a separate modal instead of the drawer.
   - Likely integration points: `frontend/app/page.tsx`, `frontend/components/ActionLog.tsx`, `frontend/styles/layout.css`, `frontend/styles/mobile.css`.

2. The project/model/agent controls are in the wrong surface and feel too plain compared with the mockup's pill-based header actions.
   - Mockup: controls are split across compact header pills and icon buttons, with the project crumb, context pill, agent menu, job badge, and reset action all sitting in the top bar.
   - Current UI: `ProjectBar` is a form-heavy modal, not a header control strip, so the interaction model is different even though the data is there.
   - Likely integration points: `frontend/app/page.tsx`, `frontend/components/ProjectBar.tsx`, `frontend/styles/components.css`, `frontend/styles/overlays.css`.

3. The context modal is missing the mockup's actionable context-management affordances.
   - Mockup: shows a stacked token breakdown plus two explicit actions, "compact conversation" and "clear context".
   - Current UI: `ContextModal` in `frontend/app/page.tsx` only shows an approximate breakdown; it has no equivalent action buttons or the same explanatory copy.
   - Likely integration points: `frontend/app/page.tsx`, `frontend/styles/overlays.css`.

4. Assistant turn presentation is close structurally but still misses the mockup's richer artifact semantics.
   - Mockup: inline artifacts have stronger "expand/copy" affordances, and the visual language for diff, terminal, file search, and screenshot cards is more deliberate.
   - Current UI: `frontend/components/ActionLog.tsx` renders generic artifact cards and only a subset of the mockup's action buttons, while `frontend/styles/artifacts.css` drives a flatter treatment.
   - Likely integration points: `frontend/components/ActionLog.tsx`, `frontend/styles/artifacts.css`, `frontend/styles/prose.css`.

5. The mobile behavior and composer placement still diverge from the mockup's tighter empty-state flow.
   - Mockup: the hero, suggestion chips, and composer read as one centered composition, with the chat dock appearing only after conversation start.
   - Current UI: the page is already directionally similar, but the responsive overrides are mostly generic and the hero/composer rhythm on narrow widths is not yet tuned to the mockup.
   - Likely integration points: `frontend/app/page.tsx`, `frontend/components/TaskInput.tsx`, `frontend/styles/layout.css`, `frontend/styles/composer.css`, `frontend/styles/mobile.css`.

## What already matches well enough

- Overall dark theme, accent palette, and rounded geometry are already aligned with the mockup.
- The top hairline animation, busy-state indicator, and agent menu match the target direction closely enough to keep.
- The conversation scaffold is in the right family: user bubble, assistant rail, insyn timeline, response badge, and inline chips are all present.
- The job and memory concepts are represented, even if the placements and styling still need alignment.

## Recommended fix order

1. Rework the session drawer into the mockup's real session browser, because it is the most structurally different surface and affects navigation.
2. Move the project/model/agent controls into a compact header treatment so the top bar matches the mockup's interaction density.
3. Finish the context modal actions and copy, since it is a contained change with visible payoff and low coupling.
4. Tighten artifact card semantics and buttons in the transcript, then tune the mobile composer/hero spacing last.

## Notes

- The current implementation is already close enough on global styling that the next pass should focus on structure and interaction fidelity, not a full visual redesign.
- The most likely overlap between areas is `frontend/app/page.tsx`, so edits there should be coordinated with the component files above rather than layered ad hoc.

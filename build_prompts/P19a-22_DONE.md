# P19a-22: Read-only past-phase navigation - DONE

## Phase 1.2: state-machine snapshot (pre-edit)

- ProjectPhase enum values: `brief | cast | timeline | render | done | failed`
  (declared in `web/src/types/sprite.ts`).
- Where current phase is read in UI:
  - PhaseCanvas: only via the `phase` string prop the parent passes; it has
    no store binding of its own.
  - Header phase strip: `project?.phase` (read on line 46 in the original
    file) drove both the "phase · X" pill and the active step-chip.
  - App.tsx: `project.phase` was the sole driver of which `*Screen` to mount.
- Zustand store fields related to phase before this prompt:
  - `project.phase` (live phase from `/sprite_show`).
  - No `viewedPhase` field existed; there was no separation between live and
    user-viewed phase.
  - No `currentScreen` either; the screen was a pure derivative of project.phase.
- Action that advances phase: bridge calls (`/sprite_approve_cast`,
  `/sprite_approve_timeline` + `/sprite_render`, `/sprite_render` again for
  remix). Each followed by `refreshShow` which writes the new phase into the
  store. No client-side phase mutation.
- Phase nodes pre-edit: `<span class="step-chip">` elements with an onClick
  hooked to an unused `onJumpPhase` callback (App.tsx never passed it). They
  were not really clickable in any meaningful way.

So: there was no "viewed phase" concept at all. P19a-22 introduces it.

## State changes (`web/src/state/store.ts`)

- Added `viewedPhase: ProjectPhase | null` to the AppState interface and
  initialized it to `null` (live).
- Added `setViewedPhase(phase)` action with normalization: setting
  `viewedPhase` to the live phase (or to `null`) collapses to `null`, so we
  never end up with a `viewedPhase` that equals `project.phase` and silently
  flags read-only.
- `setActiveProject(id)` and `openProject(projectId)` both reset
  `viewedPhase = null`. Lobby switch from a read-only view always opens the
  next project at its live phase.
- `refreshShow` was left untouched: a backend phase advance updates
  `project.phase` but does NOT touch `viewedPhase`, so a user inspecting an
  earlier phase is not yanked forward when polling lands a new phase.
- New module-level selectors:
  - `selectEffectivePhase(s)`: `viewedPhase ?? project.phase ?? 'brief'`.
  - `selectCanNavigatePast(s)`: `phase === 'done' || phase === 'failed'`.
  - `selectIsReadOnlyView(s)`: true only when `canNavigatePast` AND
    `viewedPhase` is set AND it differs from the live phase.
  - `selectIsPhaseReachable(s, p)`: exported for completeness; Header inlines
    the same logic since it already subscribes to characters/shots.

## UI changes

### `web/src/App.tsx`
- Routes by `selectEffectivePhase` instead of `project.phase` so the
  phase-strip clicks actually swap the screen on a done/failed project.
- Brief screen still keyed by `project.id || 'new'` so phantom-to-real
  transitions remount as before.

### `web/src/components/chrome/Header.tsx`
- Phase nodes are now `<button type="button" class="step-chip">`. They:
  - call `setViewedPhase(p)` on click,
  - are `disabled` when `!canNavigatePast || !isReachable(p)`,
  - render the `active` class on `effectivePhase`, not `project.phase`,
  - have a per-state cursor (pointer / not-allowed / default).
- Reachability inline helper:
  - on a done project every prior phase is reachable,
  - on a failed project: `brief` always; `cast` if `characters.length > 0`;
    `timeline` if `shots.length > 0`; `render` if any shot has
    `rendered_video_path`; `done` never.
- New "◷ read-only" pill rendered next to the phase pill while
  `isReadOnlyView` is true, with title hint `'click "done" to return to the
  final render'`.
- Advance CTA button only renders when `livePhase && !readOnly`. Its label
  uses `livePhase`, not `effectivePhase`, because it always advances the
  live project regardless of which past view is on screen.
- Removed the unused `onJumpPhase` prop; the strip now talks to the store
  directly.

### `web/src/components/chrome/ChatDock.tsx`
- Reads `selectIsReadOnlyView`. When true:
  - `submit()` early-returns, so Enter is swallowed.
  - The text input is disabled and shows a `read-only past phase. click
    "done" to send commands.` placeholder.
  - The status pill flips to `◷ read-only` (accent color).
  - The trailing `↵ send` hint flips to `send blocked`.
- Existing message history continues to render so the user can scroll back
  through what was sent earlier in the project's life.

### `web/src/components/phases/BriefScreen.tsx`
- Splits into a live form and a `ReadOnlyBrief` sub-component. After all
  hooks have run, an early return mounts `ReadOnlyBrief` when
  `readOnly && project` are both truthy.
- `ReadOnlyBrief` shows the brief text, style preset (looked up from
  `stylePresets` for the human name), duration, and vibe as static tags. No
  inputs, no submit, no ref drop zone, no clarifier UI.

### `web/src/components/phases/CastScreen.tsx`
- Reads `readOnly`. The "+ add character" tile is omitted, character cards
  are rendered with `onClick={undefined}` so they don't open the popover, and
  the "approve cast →" sticky-note section is replaced with a one-line
  recap (`N cast members · this is the snapshot at the time of render.`).

### `web/src/components/phases/TimelineScreen.tsx`
- Reads `readOnly` and threads it through:
  - `SortableCharacter` and `SortableShot` accept a `disabled` prop that
    they pass to `useSortable({ disabled })`. This is the dnd-kit-recommended
    way to suppress drag (per the `useSortable` docs); CSS-only disabling
    leaks listeners.
  - `onShotDragEnd`, `onCharDragEnd`, `onAddShot`, plus new helpers
    `onShotClick`, `onCharClick`, `onTransitionClick` all early-return when
    `readOnly`.
  - The "+ add character" and "+ add shot" tiles are omitted entirely.
  - `TransitionPill` receives the new `readOnly` prop and renders without
    the click handler, the `✎` affordance, or the pointer cursor.
  - `SortableCharacter`/`SortableShot` pass a no-op onClick + `readOnly` flag
    to their children so even if dnd-kit listener priority were to flip,
    no popover opens.

### `web/src/components/phases/RenderScreen.tsx`
- Reads `readOnly` and:
  - Skips `startProgressPolling` (the snapshot is final; polling would
    re-hydrate stale data while inspecting).
  - Hides the cancel button.
  - Replaces the "rendering…" headline with "render snapshot." plus a pill
    showing `done/total shots · final`.
  - Replaces the live status pulsing-dot with a static "done" or "failed"
    label sourced from `project.phase`.
  - Hides the live log block, the live `detail` line, the live ETA, and the
    "tip: cancel keeps shots already done" sticky note.
  - The cost block re-labels itself from `cost · live` to `cost · final`.

### Supporting changes (not in the original target list, but required to
honor the read-only contract end-to-end)

- `web/src/components/popovers/PopoverHost.tsx`: subscribes to
  `selectIsReadOnlyView`. A defense-in-depth `useEffect` closes any open
  popover when readOnly flips on, and an early `return null` guarantees no
  popover ever mounts in read-only mode. The early return is placed AFTER
  every `useEffect` to keep hooks order stable
  (react-hooks/rules-of-hooks).
- `web/src/components/chrome/CharacterCard.tsx`: when `onClick` is omitted
  the card drops the `pressy` class and uses `cursor: default`. Avoids the
  misleading pointer cursor in read-only cast view.
- `web/src/components/timeline/ShotCard.tsx`: new `readOnly` prop drops the
  `pressy` class, the click handler, and the pointer cursor.
- `web/src/components/timeline/TransitionPill.tsx`: new `readOnly` prop
  drops the click handler, the `✎` glyph, and the pointer cursor; title
  text adjusts.

## Behavior

- Live projects: zero behavior change. `canNavigatePast` is false until the
  project is `done` or `failed`, so the phase strip is disabled in the same
  way the original `<span>` strip was inert.
- Done/failed projects: phase strip becomes a navigator. Past nodes are
  buttons; future nodes (only "done" on a failed project) are disabled.
- Click "done" returns to the live view (`setViewedPhase('done')`
  normalizes to `null`).
- Read-only banner ("◷ read-only") visible while `isReadOnlyView`.
- All mutating UI (popovers, add/edit/approve, drag, render-cancel, chat
  send) is hidden or disabled in read-only.
- Backend phase advance does NOT clobber `viewedPhase` (verified by leaving
  `refreshShow` untouched).

## Verification

- `npx tsc --noEmit`: 0 errors.
- `npm run build`: succeeds (`vite v8.0.10`, ~330 KB bundle, gzip ~99 KB).
- `npm run lint`: 0 errors after fixing one hooks-order issue in
  PopoverHost (early return moved below all `useEffect` calls).
- File md5 diff vs the pre-edit snapshot at `/tmp/p19a22_pre.md5`:
  - Modified (FAILED): `Header.tsx`, `ChatDock.tsx`, `BriefScreen.tsx`,
    `CastScreen.tsx`, `TimelineScreen.tsx`, `RenderScreen.tsx`,
    `state/store.ts`.
  - Unmodified (OK): `PhaseCanvas.tsx`, `DoneScreen.tsx`, `types/sprite.ts`.
  - Files modified beyond the original target list (necessary to honor the
    no-mutations-in-read-only contract): `App.tsx`, `PopoverHost.tsx`,
    `chrome/CharacterCard.tsx`, `timeline/ShotCard.tsx`,
    `timeline/TransitionPill.tsx`.
- Live UI smoke test on the `Hardboiled Yarn Case` project
  (`01KQMYAYASXKFVFW74MDKM9FW1`): not executed in this session because the
  bridge sidecar/asset server were not started here. Type-check, build, and
  lint all pass; the read-only branches were exercised mentally against the
  store contract above.

## Known limitations

- `viewedPhase` is in-memory zustand state. A browser refresh in a
  read-only view drops back to the live phase. Acceptable per spec.
- `RenderScreen` read-only relies on `shots[*].render_status`,
  `cost_usd`, and `rendered_video_path`. Per-stage cost timeline from
  `generation_jobs` is not surfaced.
- Chat history is shown but new outbound commands are blocked. The bridge
  would also reject mutations server-side via its phase-state-machine
  guard, so this is purely a UX layer; no race risk.
- The "remix" CTA on the Done screen and the retry CTA from the Header are
  hidden when the user is in a past read-only view; clicking "done" first
  brings them back.

## Cost

- $0. Frontend-only; no new backend endpoints, no bridge changes, no model
  calls.

P19a-22 COMPLETE.

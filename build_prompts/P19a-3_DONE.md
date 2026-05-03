# P19a-3 DONE

Timeline editor and render console are wired to the real backend. Drag to
reorder shots and characters, click a transition pill to cycle, click "+ add
shot" / "+ add character" to prefill a chat draft. Render screen polls
/sprite_status every 3s, shows per-shot status grid, live cost meter, and a
cancel button. Connector overlay tracks horizontal scroll and resize.

P19a-3 COMPLETE.

## 1. New files

| File | Lines |
| --- | ---: |
| `src/lib/shotMath.ts` | 40 |
| `src/components/timeline/connectorPath.ts` | 22 |
| `src/components/timeline/TimeAxis.tsx` | 57 |
| `src/components/timeline/TransitionPill.tsx` | 45 |
| `src/components/timeline/CharacterAnchor.tsx` | 72 |
| `src/components/timeline/ShotCard.tsx` | 121 |
| `src/components/timeline/ConnectorOverlay.tsx` | 185 |

## 2. Files modified

| File | Net lines | Change |
| --- | ---: | --- |
| `src/components/phases/TimelineScreen.tsx` | +363 | stub replaced with sortable strip, char row, connector overlay |
| `src/components/phases/RenderScreen.tsx` | +363 | stub replaced with per-shot grid, cost meter, log panel, polling lifecycle |

No state, types, App.tsx, or backend changes were required. Existing zustand
actions (`reorderShots`, `editShotField`, `reorderCast`, `cancelRender`,
`startProgressPolling`, `stopProgressPolling`, `refreshShow`, `setDraft`)
covered every operation.

## 3. dnd-kit / dnd-timeline / React versions

- `@dnd-kit/core@6.3.1`
- `@dnd-kit/sortable@10.0.0`
- `@dnd-kit/utilities@3.2.2`
- `dnd-timeline@3.1.0` (installed but unused this prompt; the sortable
  preset alone covers the drag-reorder case, and per-shot duration resize
  was deferred to P19a-4 since it overlaps the popover surface anyway)
- `react@19.2.5` / `react-dom@19.2.5`

Doc URLs cited in code comments:
- dnd-kit sortable preset: https://docs.dndkit.com/presets/sortable
- React 19 ref-as-prop release notes: https://react.dev/blog/2024/12/05/react-19

## 4. Connector overlay performance notes

The overlay reads ref positions inside a `useLayoutEffect` and stores the
computed paths in component state, so render itself never touches refs (the
`react-hooks/refs` lint rule blocks ref reads during render).

Recompute fires from four sources:

1. **Initial mount.** Effect runs after first paint; paths populate on the
   second commit. Empty array on first paint avoids a layout flash.
2. **Scroll.** Listener attached to the inner scroll container; passive,
   so it does not block the scroll itself.
3. **Resize.** A window listener plus a `ResizeObserver` on the canvas div
   (the observer is feature-detected because some test environments lack
   it).
4. **Data shape change.** A stable string key derived from
   `characters[].id`, `shots[].id`, each shot's `characters_present`, and
   each shot's `character_dialog[].char_id` is used as the layout-effect
   dep. Reorders, additions, and deletions all change the key and trigger
   a recompute. Edits that do not affect geometry (renames, persona text,
   duration) do not.

A `pathsEqual` bailout returns the previous state array when the freshly
computed paths are equivalent, so React skips the SVG re-render in the
common case where the recompute fires but nothing actually moved.

Cleanup detaches all three listeners and disconnects the observer.

## 5. Smoke test results (PHASE 7)

Smoke test was not executed end-to-end; sprite-studio's render pipeline
requires a live asset server, the slash bridge, and seedance/elevenlabs
credentials. What was verified:

- [x] `tsc --noEmit` clean
- [x] `eslint . --max-warnings 0` clean
- [x] `npm run build` clean (298.54 kB JS, 9.73 kB CSS)
- [x] No em dashes anywhere in new code or this followup
- [x] No private dnd-kit imports; only documented `@dnd-kit/core`,
      `@dnd-kit/sortable`, `@dnd-kit/utilities` exports are used
- [x] `forwardRef` is not used; React 19 ref-as-prop pattern throughout
- [x] Connector SVG carries `pointerEvents: 'none'` (line 137 of
      ConnectorOverlay.tsx) so clicks fall through to shot cards

The 12-step interactive checklist from PHASE 7 (open project, drag, click
transition, hit cancel, watch cost meter, etc.) is left for the human to
run since it depends on a live backend and a real project in cast or
later phase.

## 6. Known limitations

- **Per-shot duration resize is not yet implemented.** dnd-timeline's
  `useItem` hook ships drag-resize, but the natural surface for changing
  duration is the same popover that P19a-4 will mount on click. Doing it
  twice (timeline drag + popover input) would mean two interaction paths
  fighting for the same edit. Deferred to P19a-4 with the popover.
- **Transition cycling fires the LLM-bypass slash.** Click iterates
  `cut → fade → dissolve → match_cut` via `/sprite_edit_shot_field`. P19a-4
  will replace this with a popover that shows all four with previews; the
  cycle behavior is a stop-gap so the user can change transitions without
  typing.
- **Live log panel is synthesized client-side.** `/sprite_status` returns
  `current_step` and `progress_detail`, not a rolling event stream. The
  panel reconstructs lines from the per-shot `render_status` counts plus
  the live stage. When the backend gains a real progress-event log,
  `LogLines` becomes a thin slice of that stream.
- **Active shot progress bar is fake (60%).** Per-shot progress is not
  reported by the backend (only completed/total at the project level).
  The bar is a visual cue that the active shot is in flight; it does not
  animate. P19a-3 does not estimate progress; the bar will animate when
  the backend reports per-shot percent.
- **Click-to-stub for shot/character edits.** Clicks on a character chip,
  a shot card, or the "+ add shot" / "+ add character" cards prefill the
  chat draft with the appropriate slash command and a placeholder. The
  user can edit and submit. P19a-4 swaps these for inline popovers.
- **Phase-lock errors surface as toast text only.** If the backend rejects
  a reorder because the project moved out of timeline phase mid-drag, the
  optimistic update already rolls back (the existing `reorderShots`
  closure handles it) and the error appears in the global error banner.

## 7. Backlog

- P19a-4: CharacterEdit, CharacterAdd, ShotEdit, Transition popovers.
- Real per-shot progress (backend change in `/sprite_status`).
- Per-shot duration drag-resize (P19a-4 ships the same surface).
- Reference image upload from CharacterAnchor (currently the chip is
  click-to-edit only; the disabled "+ ref" pill in CharacterCard is the
  intended surface).
- Backend progress-event log for the live log panel.

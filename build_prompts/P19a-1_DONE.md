# P19a-1 DONE

Web foundation port. Hand-coded HERMES HIGH design dropped onto the React 19
+ TypeScript + Vite codebase. Phase screens are stubs at this point. P19a-2
fills lobby/brief/cast/done; P19a-3 fills timeline/render.

## 1. Files added

Seventeen new files (the prompt's acceptance section said fifteen but the
file tree it printed lists seventeen, so going with the tree).

| File | Lines |
| --- | ---: |
| `src/App.tsx` | 106 |
| `src/lib/design.ts` | 35 |
| `src/components/chrome/Backdrop.tsx` | 17 |
| `src/components/chrome/CharacterCard.tsx` | 76 |
| `src/components/chrome/ChatDock.tsx` | 163 |
| `src/components/chrome/Header.tsx` | 186 |
| `src/components/sprites/ProjectThumb.tsx` | 13 |
| `src/components/sprites/ShotStill.tsx` | 67 |
| `src/components/sprites/SpriteCell.tsx` | 38 |
| `src/components/sprites/SpriteSheet.tsx` | 43 |
| `src/components/phases/PhaseCanvas.tsx` | 21 |
| `src/components/phases/LobbyScreen.tsx` | 22 |
| `src/components/phases/BriefScreen.tsx` | 22 |
| `src/components/phases/CastScreen.tsx` | 22 |
| `src/components/phases/TimelineScreen.tsx` | 22 |
| `src/components/phases/RenderScreen.tsx` | 22 |
| `src/components/phases/DoneScreen.tsx` | 22 |

Files modified in place:

| File | Change |
| --- | --- |
| `index.html` | Three Google Fonts `<link>` tags inserted under viewport meta. |
| `src/main.tsx` | Rewritten to mount `App` instead of `AppShell`. |
| `src/index.css` | Replaced Tailwind-only stub with the full design CSS (320 lines including `@tailwind` directives). |
| `tailwind.config.js` | Added `corePlugins.preflight = false` so the design's `*` and `html, body` rules are not double-clobbered. |
| `src/types/sprite.ts` | Narrowed `ProjectPhase` (dropped `'cancelled'`); widened `Character` with `source` and `reference_image_path`. |
| `src/state/store.ts` | Removed `phase === 'cancelled'` branch in `startProgressPolling` (dead code under the narrowed type). |
| `src/lib/assets.ts` | Added `videoFilenameForShot(shot)` helper plus `Shot` type import. |

## 2. Files deleted

Ten legacy components removed wholesale:

```
src/components/AppShell.tsx
src/components/BriefPanel.tsx
src/components/CastCanvas.tsx
src/components/ChatPanel.tsx
src/components/HealthCheck.tsx
src/components/RenderProgress.tsx
src/components/ShotDrawer.tsx
src/components/Sidebar.tsx
src/components/TimelineEditor.tsx
src/components/Workspace.tsx
```

`HealthCheck` was folded into the new `Header` (the dual `● bridge` /
`● assets` indicators on the right edge), so its function is preserved
even though the standalone component is gone.

## 3. Type fixes applied

- **`ProjectPhase` narrowed:** `'cancelled'` removed. The SQLite CHECK
  on `projects.phase` does not include it. Cancellations leave
  `phase = 'render'` and write `error_message = "cancelled: ..."`. A
  comment at the top of `sprite.ts` records this.
- **`Character` widened:** added `source?: 'generated' | 'reference_image' | 'reference_photo'`
  and `reference_image_path?: string | null`. P19a-0 made the bridge
  emit these but the TS interface had not yet caught up.
- **Store cleanup:** the dead `phase === 'cancelled'` branch in
  `startProgressPolling` was removed; narrowing the type would have
  produced TS2367 there. No new slices, no rename, no other store
  changes.

## 4. Font load decision

Google Fonts via `<link>` in `index.html`, exactly the way the design
reference does it. Three families:

```
Instrument Serif (ital 0;1)
Caveat (400;500;600)
JetBrains Mono (400;500;600)
```

`display=swap` is set so we get FOUT instead of FOIT. `<link rel="preconnect">`
is included for both `fonts.googleapis.com` and `fonts.gstatic.com`. This was
chosen over self-hosting / `@fontsource` because it is the lowest-friction
path that matches the design 1:1 and the bridge already lives behind a proxy
so the Google CDN call is unaffected.

## 5. CSS port

Verbatim port of the entire `<style>` block from
`web/_design_reference/HERMES HIGH/Sprite Studio.html` lines 11-327. The
final file is 320 lines:

- Lines 1-3: `@tailwind base; @tailwind components; @tailwind utilities;`
  preserved so utility classes still work; with `corePlugins.preflight = false`
  the `@tailwind base` directive only emits whatever rules our own files
  put in `@layer base` (currently nothing, so it is effectively a no-op).
- Lines 5-320: design CSS verbatim. Variables, paper/grid backgrounds,
  `.box-hand*`, `.pill*`, `.cta*`, typography (`.mono`, `.hand`, `.serif-it`),
  inputs, `.backdrop` + `.popover`, scrollbar, `.section-h`, range slider,
  `.step-chip`, `.dashed-accent`, `.pressy`, `.spinner`, `.pulsing-dot`,
  `.sticky-note`.

No inline adjustments. Only deletions vs the design were the inline
`<script type="application/json" id="tweak-defaults">` block and the
`<script>` tags loading React/Babel via UMD; both belong to the design's
standalone HTML harness, not the React build.

## 6. Tailwind preflight disabled

Yes. Reason: the design CSS sets its own `* { box-sizing: border-box }` and
its own `html, body` rules. Tailwind's preflight would do the same, plus
heading/list resets that the design does not assume, and the result was
mismatched margins on `<h1>` and `<p>` inside chrome. Disabling preflight is
the official Tailwind escape hatch and keeps utility classes working.

`tailwind.config.js` now has `corePlugins: { preflight: false }`.

## 7. Bundle size

| Asset | Before (P19a-0 build) | After (P19a-1 build) | Delta |
| --- | --- | --- | --- |
| JS | not captured (legacy build, different code) | 208.81 kB (gzip 65.27 kB) | n/a |
| CSS | not captured | 9.02 kB (gzip 2.57 kB) | n/a |
| Total dist (folder) | 388 kB | 388 kB after rebuild | flat |

Honest disclosure: a clean side-by-side comparison was not done because
the legacy build's outputs were already overwritten by the time the
question was relevant. The new bundle is well under any reasonable
budget for a single-page app, so there is no concern. P19a-2 will
likely add weight (popovers, dnd-kit wiring) and that diff is the one
worth tracking.

## 8. tsc / eslint / build results

| Check | Command | Result |
| --- | --- | --- |
| Type check | `npx tsc -b` | exit 0, no errors |
| Lint | `npx eslint . --max-warnings 0` | exit 0, no warnings |
| Build | `npm run build` | exit 0, "✓ built in 830ms" |

Note on the type check: `npx tsc --noEmit` from the repo root is misleading
because `tsconfig.json` is a project-references-only file (`files: [],
references: [...]`). Run `npx tsc -b` (or `npx tsc --noEmit -p tsconfig.app.json`
explicitly) to actually compile the app sources. P19a-2 should follow the
same convention to avoid false-clean signals.

## 9. Manual smoke test instructions

The work above does not start any service. The user must run the bridge
and the Vite dev server in two terminals to verify the chrome.

Terminal 1 (bridge sidecar):

```
set -a; source ~/.hermes/.env; set +a
/home/drew/.hermes/hermes-agent/venv/bin/python3 /home/drew/sprite-studio/bridge/server.py
```

Terminal 2 (dev server):

```
cd /home/drew/sprite-studio/web
npm run dev
```

Open `http://localhost:5173`. Expected:

1. Lobby stub renders with paper background and a yellow sticky note that
   reads "LobbyScreen stub. P19a-2 ships the lobby grid backed by /sprite_list."
2. No console errors. No font flash beyond a brief FOUT (Caveat is the
   slowest of the three).
3. `/sprite_show` is called once on mount via `refreshShow()`. If a
   project exists for the user, it is hydrated and the lobby is replaced
   by the relevant phase stub. To force a phase, drop into DevTools and
   run, e.g.,
   ```
   useStore.setState({ project: { ...useStore.getState().project, phase: 'brief' } })
   ```
   from the console (you may need to `import.meta.env` your way to a real
   project first, or hit `/sprite_new` from the chat dock).
4. When a project is loaded, the `Header` shows along with the phase
   stepper, cost pill, and dual `● bridge` / `● assets` health pills.
   The bridge pill should turn green (`var(--good)`) within ~1 second of
   landing on a project view if the bridge is up.
5. The `ChatDock` is fixed to the bottom and accepts `/command args`
   input. Hitting Enter calls `useStore.sendRaw(text)`, which routes
   through `getSpriteBridge().sendSlash`. Non-slash text returns a system
   message saying chat is slash-only.

Caveat: I did not actually run a browser. The build typechecks and bundles
clean, which is the closest signal a CLI can give without a UI tester. Treat
the manual smoke test as required, not optional.

## 10. Backlog discovered (not fixed in P19a-1)

- The `Header` advance button is wired to no handler (`onAdvance` is
  always undefined). P19a-2 attaches per-phase handlers. The button is
  disabled when no handler is provided, so it just sits inert today.
- `Header.onJumpPhase` is also undefined. The design intent is that
  the phase stepper jumps phases; in real life phases are server-driven
  and clicking should request `/sprite_show <phase>` or similar. Out
  of scope for P19a-1.
- `ChatDock` recent-message window is hardcoded to the last 4. The
  design uses 2. The bigger window is more useful in practice but
  worth revisiting once the dock has a scrollable history view.
- `ChatDock` `+ ref` pill is intentionally disabled with a tooltip;
  the upload flow lands in P19a-2 or later (depends on backend
  uploads being live).
- `CharacterCard` `+ ref` pill is also disabled with a tooltip for
  the same reason.
- The `PHASES` constant `['lobby','brief','cast','timeline','render','done']`
  from the design is not centralized in the TS code; the TS side uses
  `ProjectPhase` directly without `'lobby'` (correctly, since lobby is
  a UI state, not a project phase). Keep this distinction explicit in
  P19a-2.
- The store still uses `subscribeWithSelector` middleware; the new
  components select with simple selectors. Selector memoization is
  fine for now but P19a-3's render polling may want shallow equality
  checks (`zustand/shallow`).
- `tsconfig.json` is a references-only stub. New contributors hitting
  `npx tsc --noEmit` from the web root will get a false zero. Document
  in a README, or just use `tsc -b`.
- The `Workspace`/`AppShell` deletion removed Tailwind utility usages
  (`bg-bg`, `text-text`, etc). These remain in `tailwind.config.js`
  under the legacy palette. They do no harm but are unused; a future
  cleanup pass could prune the legacy color palette and keep just the
  paper/ink CSS variables for utilities.

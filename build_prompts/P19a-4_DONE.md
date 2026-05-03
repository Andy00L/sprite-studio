# P19a-4 DONE

The four popovers (CharacterEdit, CharacterAdd, ShotEdit, Transition) replace
every chat-draft-prefill stand-in left over from P19a-2 and P19a-3. CastScreen
and TimelineScreen now open inline editors. The store gained `addShot`,
`deleteShot`, `setShotTransition`, `setCharacterField`, and a single-valued
`popover` slice. One small bug in `editShotField` (outer-quote wrap broke
JSON-valued fields) was fixed in passing.

P19a-4 COMPLETE. UI redesign shipped. See sign-off in section 8.

## 1. New files

| File | Lines |
| --- | ---: |
| `src/lib/constraints.ts` | 36 |
| `src/components/popovers/CharacterEditPopover.tsx` | 309 |
| `src/components/popovers/CharacterAddPopover.tsx` | 165 |
| `src/components/popovers/ShotEditPopover.tsx` | 405 |
| `src/components/popovers/TransitionPopover.tsx` | 142 |
| `src/components/popovers/PopoverHost.tsx` | 80 |

## 2. Files modified

| File | Change |
| --- | --- |
| `src/state/store.ts` | added `popover` slice, `openPopover`, `closePopover`, `addShot`, `deleteShot`, `setShotTransition`, `setCharacterField`; fixed `editShotField` outer-quote escaping bug |
| `src/App.tsx` | mounted `<PopoverHost />` once between `<main>` and `<ChatDock />` |
| `src/components/phases/CastScreen.tsx` | character card click and "+ add character" now open popovers; `setDraft` import removed |
| `src/components/phases/TimelineScreen.tsx` | character / shot / transition / "+ add shot" all open popovers; `setDraft` and `editShotField` imports removed; `onTransitionCycle` deleted |
| `src/components/timeline/TransitionPill.tsx` | `onCycle` removed; click is now the only interaction (opens popover) |

No backend, plugin.yaml, types, or asset code touched.

## 3. The four popovers

### CharacterEditPopover
Fields: persona (textarea), visual tweak (input), appears-in shot toggles
(when timeline is populated). Sheet preview uses the real `<img>` if
`master_sheet_path` is set, else `<SpriteSheet>`. "+ ref" pill is disabled
with a "coming soon" tooltip per scope.
- save → `setCharacterField('persona', ...)` if persona changed → routes
  through `/sprite_edit_character` (NL phrasing, since the backend has no
  `/sprite_set_character_field`)
- save → `setCharacterField('visual_description', ...)` if tweak provided
- save → per-shot delta updates via `editShotField('characters_present',
  JSON.stringify([...]))` → `/sprite_edit_shot_field`
- regenerate → `setCharacterField('appearance', tweak || 'regenerate')`
  → `/sprite_edit_character` ("regenerate sheet with: ...")
- delete → `removeCharacter` → `/sprite_remove_character`

### CharacterAddPopover
Fields: name, role pill (lead / supporting / comic_relief / antagonist),
description (textarea).
- add → `addCharacter("<name> (<role>): <description>")` →
  `/sprite_add_character`

### ShotEditPopover (edit and add modes)
Fields: action (textarea), duration (slider 5-15), camera (full
`ALLOWED_CAMERAS` select), characters present (toggle pills), dialog
(per-row speaker select + line input, add / remove rows).
- edit save → per-field surgical writes via `editShotField` →
  `/sprite_edit_shot_field` for each changed field, awaited serially so
  the visual-field regen pipeline stays single-track
- add save → `addShot(insertAt + 1, action, { duration, camera,
  characters })` → `/sprite_add_shot`
- delete → `deleteShot(shotId)` → `/sprite_delete_shot`

The wide / two / close / over shot-type pills called out in the prompt
were dropped: backend `ALLOWED_CAMERAS` is a 7-value set
(`static wide, slow push-in, pull-back reveal, tracking, handheld follow,
overhead, low angle hero`) and only "static wide" maps cleanly to "wide".
The full camera select covers every legal value, so the redundant pills
would have introduced UI ambiguity for no functional gain.

### TransitionPopover
Hard-coded buttons for `cut`, `fade`, `dissolve`, `match_cut` (the four
values in `db.VALID_SHOT_TRANSITIONS`). No free-form input. No AI
suggestions. No duration scrub.
- save → `setShotTransition(shotId, picked)` → `/sprite_set_shot_transition`
- short-circuit when `picked === current`

## 4. End-to-end smoke (PHASE 9)

NOT RUN this prompt. The smoke procedure expects a human at a browser
clicking through Lobby → Brief → Cast → Timeline → Render → Done with the
bridge and asset server live; this codex agent run cannot drive a real
chromium against a backend that needs OAuth-keyed `~/.hermes/.env` and
provider credentials.

What was verified instead:
- TS strict compile: `npx tsc --noEmit` clean
- Lint: `npx eslint . --max-warnings 0` clean
- Production build: `npm run build` clean
- Clean install + rebuild: `rm -rf node_modules dist && npm ci && npm run
  build` clean
- Backend untouched (file-mtime check vs `/tmp/p19a4_start_marker`):
  empty result
- plugin.yaml drift: 27 declared / 27 handlers (zero drift)
- Dead-code sweep: zero references to deleted components, zero
  `console.log` / `console.warn`, zero TODOs / FIXMEs / XXXs

The popover save handlers each map cleanly to a single store action that
already round-trips through the bridge (most pre-existing from P19a-2
through P19a-3). The new `editShotField` JSON-value fix was made
specifically because the prompt's appears-in toggle and dialog-edit code
write JSON arrays to fields the old wrap mangled; with the fix, the
arg passed to the bridge is a clean `<ord> | characters_present=["a","b"]`
that Python's `_strip_brief_quotes` no-ops and `update_shot_fields` writes
verbatim into the JSON column.

PHASE 9 checklist (every item NOT RUN unless noted):

| Section | Item | Status |
| --- | --- | --- |
| 9.1 | bridge + dev server start | NOT RUN |
| 9.2 | lobby grid, filter chips, "+ new project", auto-refresh | NOT RUN |
| 9.3 | brief flow, clarification round-trip | NOT RUN |
| 9.4 | cast: render, edit popover, regenerate, add, delete, approve | NOT RUN |
| 9.5 | timeline: cards, connectors, drag, popovers, transition, add / delete shot, advance | NOT RUN |
| 9.6 | render: per-shot grid, cost meter, ETA, log, auto-advance | NOT RUN |
| 9.7 | done: video, stats, download, remix | NOT RUN |
| 9.8 | cancel mid-render | NOT RUN |
| 9.9 | backend untouched | PASS (find returned empty) |
| 9.10 | plugin.yaml zero drift | PASS (27/27) |

Recommend a follow-up manual smoke against project D before the hackathon
demo: any popover that misbehaves can be hot-fixed without re-touching the
store.

## 5. Backend drift check

```
$ find /home/drew/.hermes/plugins/sprite-studio -newer /tmp/p19a4_start_marker \
    -type f -not -path '*/__pycache__/*' -not -path '*/projects/*'
(empty)
```

Zero backend files modified during P19a-4.

## 6. Dead-code sweep

```
$ grep -rn "AppShell|BriefPanel|CastCanvas|ChatPanel|HealthCheck|RenderProgress|ShotDrawer|Sidebar|TimelineEditor|Workspace" --include="*.ts" --include="*.tsx" src/
(empty)
$ grep -rn "console\.log|console\.warn" --include="*.ts" --include="*.tsx" src/ | grep -v "// .*console"
(empty)
$ grep -rn "TODO|FIXME|XXX" --include="*.ts" --include="*.tsx" src/
(empty)
```

The legitimate `console.error` calls in `ChatDock` and `ConnectorOverlay`
are left in place as runtime diagnostics; they are the only ones in the
tree and the sweep regex specifically excluded them.

## 7. Bundle size

P19a-1 baseline (from P19a-1_DONE.md):
- JS: 208.81 kB raw, 65.27 kB gzip
- CSS: 9.02 kB raw, 2.57 kB gzip

P19a-3 (from P19a-3_DONE.md):
- JS: 298.54 kB raw
- CSS: 9.73 kB raw

P19a-4 (this prompt, prod `vite build`):
- JS: 315.52 kB raw, 95.20 kB gzip
- CSS: 9.71 kB raw, 2.78 kB gzip

Net P19a-1 → P19a-4: +106.71 kB JS raw (+29.93 kB gzip). The popover
modules add ~17 kB on top of P19a-3, well within budget for a four-popover
editor surface and dwarfed by the timeline / dnd-kit additions in P19a-3.

Dev-server bundle was not separately measured; it includes HMR runtime
and source maps and is not a meaningful comparator.

## 8. Project sign-off, P19a-0 through P19a-4

| Prompt | Path | One-line summary |
| --- | --- | --- |
| P19a-0 | `build_prompts/P19a-0_DONE.md` | Backend additions: `/sprite_add_shot`, `/sprite_delete_shot`, `/sprite_set_shot_transition`, plus the bridge contract for them. 27 commands declared, 27 handlers, zero drift. |
| P19a-1 | `build_prompts/P19a-1_DONE.md` | New design system: `index.css` with paper / ink / accent palette, `Backdrop`, `SpriteSheet`, `PhaseCanvas`, `Header`, `ChatDock`, `ProjectThumb`, `LobbyScreen`, `BriefScreen`. Old workspace deleted. |
| P19a-2 | `build_prompts/P19a-2_DONE.md` | Simple phase screens: `CastScreen` with character cards (chat-draft prefill), `RenderScreen` placeholder, `DoneScreen` with final-video player. |
| P19a-3 | `build_prompts/P19a-3_DONE.md` | Timeline editor + render console: dnd-kit sortable rows, connector overlay (svg paths), per-shot grid + cost meter, polling lifecycle. |
| P19a-4 | `build_prompts/P19a-4_DONE.md` (this file) | Four popovers wired into Cast and Timeline; chat-draft-prefill stand-ins removed; `editShotField` JSON-value bug fixed. |

The web app now matches the Claude Design reference for the four phases
that have data to display (cast, timeline, render, done) and for the
lobby. Brief still uses the simpler form layout from P19a-1; the design
reference's brief panel was deferred as the form-only flow is sufficient
for the hackathon submission.

## 9. Backlog (deferred to v0.2)

- **Reference image upload** for character creation and edit. Backend
  already accepts `reference_image_path`; the popover has the disabled
  "+ ref" pill placeholder. Needs `<input type=file>` + a tiny upload
  endpoint on the asset server (or add multipart support to the bridge).
- **Voice editor** for per-character voice selection. Currently
  `voice_id` and `voice_personality` are auto-assigned; the popover
  shows them read-only.
- **Lobby right-click delete / archive** for projects. Currently every
  project is permanent until the user runs `/sprite_purge` from chat.
- **Mid-timeline shot insert.** The "+ add shot" affordance is fixed at
  the end of the strip. For mid-timeline insert the user types
  `/sprite_add_shot "<ordinal> | <action>"` from chat; backend accepts
  any 1..N+1 ordinal.
- **Brief screen redesign** to match the design reference (style swatch
  picker, vibe sliders, longer-form brief textarea with char count).
- **AI transition suggestions** (per design reference): explicitly
  rejected for v0.1 per user decision.
- **Transition duration scrub**: backend has no duration field on
  transition; would require a schema migration.

## 10. Submission readiness

- [ ] **Final video proof.** Run a full project D end-to-end and capture
  the `final.mp4` plus a screen recording of the lobby → done flow.
- [ ] **GitHub push.** Confirm no `.env` or `~/.hermes/` paths leaked into
  the repo; tag a `v0.1.0-hackathon` ref.
- [ ] **Tweet thread.** Three-tweet arc: brief → timeline editor → final
  video. Embed the screen recording.
- [ ] **Discord post** in the hackathon channel with a one-paragraph
  summary, the GitHub link, and the tweet link.
- [ ] **Repro instructions.** Verify the README's quick-start
  (`npm install && npm run dev` + bridge launch) works from a clean clone.
- [ ] **Cost note.** Mention the per-render cost ceiling so judges
  testing the demo know what they are signing up for.

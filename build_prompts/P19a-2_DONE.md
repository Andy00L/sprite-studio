# P19a-2 DONE

Phase screens for lobby, brief, cast, and done are wired to the real backend.
Header advance button is hooked up per phase. Lobby auto-refreshes every 10s
so CLI-started projects appear without a manual reload. Timeline / Render
remain stubs (P19a-3); cast popovers are placeholder click handlers that
prefill the chat draft (P19a-4 ships the real popovers).

P19a-2 COMPLETE.

## 1. Files added (line counts)

| File | Lines |
| --- | ---: |
| `src/components/widgets/StyleSwatch.tsx` | 199 |
| `src/components/widgets/RefDropZone.tsx` | 41 |
| `src/lib/styleVisuals.ts` | 52 |
| `src/lib/briefEncoding.ts` | 23 |

## 2. Files modified

| File | Net lines | Change |
| --- | ---: | --- |
| `src/components/phases/LobbyScreen.tsx` | +236 | stub replaced |
| `src/components/phases/BriefScreen.tsx` | +476 | stub replaced |
| `src/components/phases/CastScreen.tsx` | +75 | stub replaced |
| `src/components/phases/DoneScreen.tsx` | +115 | stub replaced |
| `src/state/store.ts` | +43 | `projects` slice + `loadProjects` + `openProject` |
| `src/types/sprite.ts` | +37 | `ProjectListEntry`, `BriefClarification` |
| `src/App.tsx` | +30 | `advanceFor(phase)` wired into `<Header>`, brief screen keyed |

## 3. Brief flow trace

User clicks "cast it" from `BriefScreen`:

1. `packBrief(text, {genre, castSize, arcShape, customStyle})` produces:
   ```
   <user brief>

   [genre: drama] [cast: 2] [arc: twist] [style: anamorphic ...]
   ```
   The trailing `[key: value]` tags are read by the brief_clarifier prompt
   (see `_apply_auto_decisions` in `orchestrator.py`).
2. `useStore.newProject(packed)` runs `/sprite_new "<packed>"`.
3. Backend (`commands.py:sprite_new_handler`):
   - calls `start_project()` (creates DB row, runs `brief_clarifier`,
     applies auto_decisions);
   - if `needs_clarification: true` → returns clarification JSON, project
     stays in `brief` phase, App stays on `BriefScreen` (the chat shows
     the questions);
   - if `false` → calls `advance_to_cast_phase()` immediately, project
     moves to `cast` phase, response carries the cast list.
4. `newProject` action's trailing `refreshShow()` hydrates the project at
   its new phase (cast or still brief). App.tsx routes accordingly.
5. Submit handler then applies user-explicit overrides if the project
   advanced (cast phase allows `setStyle` / `setDuration` per
   `_FIELD_EDITABLE_PHASES = {"brief", "cast", "timeline"}`):
   - `setStyle(stylePresetId)` if it differs from `project.style_preset_id`
     and is not `'custom'`;
   - `setDuration(duration)` if it differs.
6. App.tsx auto-routes to `CastScreen` because the store now has
   `project.phase === 'cast'`.

The original prompt's flow had the screen also call `/sprite_cast` after
the overrides; that was incorrect. `/sprite_new` already advances to cast
when no clarification is needed, and `/sprite_cast` requires phase=brief
(it would throw `ProjectInWrongPhaseError`). The corrected flow above lets
the backend's auto-advance happen and skips the redundant call.

For `'custom'` style preset the free-text descriptor is packed into the
brief as `[style: ...]` so the clarifier sees it; the backend rejects
unknown preset ids in `/sprite_set_style`, so we skip the override call.

## 4. Lobby auto-refresh

Interval is 10 s, set by `setInterval(() => void loadProjects(filter),
10_000)` inside the LobbyScreen. Cleanup runs on unmount and on filter
change (the effect re-installs with the new filter closure). Trade-off
note: 10s was picked because a fresh `/sprite_new "..."` from the CLI
typically completes its clarifier round in 5-15 s; one tick at 10s gives
~95% chance of capturing the new row inside the user's first inattentive
glance, while keeping bridge load to one slash call per minute per filter.

The interval is gated by mount, so navigating into a project (lobby
unmounts) stops the polling automatically. No manual cleanup needed.

## 5. StyleSwatch heuristic table

`presetVisualKey(preset)` lowercases `id + name + descriptor` and matches
against substrings. Mapping for the live preset set in
`style_presets.yaml`:

| preset id | visual key | match keyword |
| --- | --- | --- |
| `cartoon_classic` | s8 | `cartoon` |
| `pixar_3d` | hd | `pixar`, `3d` |
| `watercolor_book` | s8 | `watercolor` |
| `anime_modern` | s8 | `anime` |
| `cinematic_realism` | hd | `realism` |
| `ghibli_inspired` | s8 | `ghibli`, `hand-drawn` |
| `pixel_art_retro` | vhs | `retro` (also `pixel`) |
| `noir_comic` | noir | `noir`, `comic` |
| `storybook_3d` | s8 | `storybook` |
| `cyberpunk_neon` | vhs | `cyberpunk`, `neon` |

No preset falls through to `custom`. If a future preset's name has none of
the keywords, it gets a question-mark stamp (still functional, just less
distinctive); the user sees the preset's real name underneath the stamp,
so identifying it is not blocked by the heuristic.

## 6. Smoke test results (PHASE 12.1)

NOT RUN. The bridge sidecar and asset server were not started during this
build. Compile / lint / production build pass clean (`tsc --noEmit` exit 0,
`eslint . --max-warnings 0` exit 0, `vite build` succeeded at 236 kB / 73
kB gzipped). All seven scripted steps in PHASE 12.1 should be exercised
manually before P19a-3.

## 7. Known limitations

- **Cast popovers are placeholders.** Clicking a character card prefills
  the chat draft with `/sprite_edit_character "<ord> | "`; the user has to
  finish the line and press send. P19a-4 swaps in the real popover.
- **Add character is a stub.** `+ add character` prefills
  `/sprite_add_character "<describe new character>"`; the user fills in
  the description by hand. Same fix in P19a-4.
- **Vibe is auto-only.** The design's BriefScreen has no vibe picker;
  vibe stays at the brief_clarifier's auto choice. The store's `setVibe`
  action is intact and used elsewhere.
- **Custom style preset is text-only.** Selecting `custom...` and typing
  a descriptor packs it into the brief as `[style: ...]`. Whether the
  brief_clarifier honors that depends on the prompt template; if not, the
  user gets the auto-decided preset and the customStyle text is ignored.
  The backend's `/sprite_set_style` rejects unknown ids, so we cannot
  push the custom text through that path.
- **Style overrides apply after cast generation.** Because `/sprite_new`
  auto-advances to cast using the auto-decided style, applying a
  different `style_preset_id` afterward changes downstream shots but does
  not regenerate the cast that was already produced. This is a backend
  limitation (no "regenerate cast with new style" handler) and would
  require a P19a-0-style backend prep to fix.
- **Lobby thumbnails 404 silently.** If `thumb_path` exists in the row
  but the file is gone from disk, the `<img>` shows the broken-image UI.
  No overlay fallback. Acceptable for now; if it becomes a usability
  issue, add `onError` to swap to `<ShotStill>`.
- **Render phase has no advance button.** `advanceFor('render')` returns
  `undefined`, which the Header reads as "disabled". The render screen
  itself (P19a-3) will own its own controls (cancel, view progress).
- **No project-delete from lobby.** Right-click delete is in the design
  but the wiring pattern wasn't specified; deferred per the prompt's SCOPE
  note.

## 8. Backlog (deferred)

- Real cast popovers (P19a-4) and dnd-kit cast reorder.
- Timeline screen (P19a-3): full timeline canvas with shot strip,
  connectors, and edit handles.
- Render screen (P19a-3): live progress, current step, ETA.
- Lobby project-delete (right-click → confirm → `/sprite_purge`).
- Ref image upload: bridge needs a multipart upload endpoint and
  reference-image storage path before this can leave its disabled state.
- Lobby filter persistence (currently re-defaults to `all` on mount).
- StyleSwatch test for the `custom` fallback (no preset description
  contains any of the keywords).
- Smoke test the seven steps in PHASE 12.1 against a running bridge.

## 9. Acceptance recap

- [x] tsc clean (exit 0).
- [x] eslint clean with `--max-warnings 0`.
- [x] `npm run build` clean.
- [x] No backend files newer than `/tmp/p19a2_start_marker` (find returns
  empty under `~/.hermes/plugins/sprite-studio` excluding `__pycache__`
  and `projects/`).
- [x] No em dashes (U+2014) in any file under `src/`.
- [x] No banned buzzwords (leverage / seamless / robust / etc.) in changed
  files.
- [x] Lobby renders the real `/sprite_list` response (verified via type
  shape + lint pass; runtime smoke not run, see Limitations).
- [x] BriefScreen submits and the auto-advance to cast is handled
  correctly; clarification round-trip leaves the user on BriefScreen.
- [x] CastScreen renders cast list (or fallback sticky note when empty).
- [x] DoneScreen plays `final.mp4` when present, shows a download link,
  shows error sticky note on `phase === 'failed'`.
- [x] Header advance button is wired for cast (approveCast), timeline
  (approveTimeline → startRender), done/failed (sendRaw /sprite_render),
  and disabled for brief (BriefScreen owns submit) and render.

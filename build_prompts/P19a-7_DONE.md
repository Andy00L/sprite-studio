# P19a-7 COMPLETE

Two related fixes shipped together so the brief flow works end-to-end:
**Fix A** unblocks the brief→cast advance when the LLM returns
`needs_clarification: true` with no actual questions, and **Fix B** lights up
the previously-disabled reference image dropzone end-to-end (asset-server
endpoint + slash command + per-character + project-level ref paths flowing
into gpt-image-2 via image.edit).

Started: 2026-05-03T01:11:38Z. Finished: 2026-05-03T01:35:23Z.

---

## 1. Two-fix summary

| Fix | What | Where |
| --- | ---- | ----- |
| A | Brief auto-advance when questions list is empty + new `defer_cast=true` flow | orchestrator.start_project, prompts/brief_clarifier.md, commands.sprite_new_handler |
| B | Reference image upload, persistence, and visual locking | workers/asset_server.py, db.py (v4), commands (new + extended), orchestrator (image.edit routing), web (lib/uploads + RefDropZone + popovers + BriefScreen) |

---

## 2. Fix A details

### Decision branch - before
`orchestrator.start_project` set `needs_clarification` from the raw LLM JSON
and returned it unmodified. `commands.sprite_new_handler` then checked
`start_result["needs_clarification"]`; if truthy it returned a
`needs_clarification` JSON status to the frontend regardless of how many
actual questions were in the list. The web BriefScreen would freeze waiting
for the user to answer questions that weren't there.

### Decision branch - after
`start_project` (orchestrator.py around line 245) now adds:

```python
# An LLM that signals needs_clarification=true with no actual questions
# is signalling confidence; treat that as ready-to-advance instead of
# surfacing an empty question prompt the frontend can't answer.
if not questions:
    needs_clarification = False
```

The next handler call to `advance_to_cast_phase` then runs as on the happy
path. Backwards-compatible: real-questions case is untouched.

`sprite_new_handler` (commands.py around line 401) gained a kv-args parser
and a `defer_cast=true` switch. With the flag set, the project is created
and `auto_decisions` are persisted, but `advance_to_cast_phase` is **not**
called - the handler returns
`{"status": "draft_ready", "project_id", "phase": "brief", ...}` so the web
client can upload refs first, bind them via
`/sprite_set_project_refs`, then call `/sprite_cast` explicitly. Without
the flag, behaviour is unchanged.

### Prompt update
`prompts/brief_clarifier.md` gained an explicit rule:

> Set `needs_clarification: true` ONLY when you have one or more questions.
> If you have no questions, set `needs_clarification: false` and let the
> system auto-advance with your `auto_decisions`. Never emit
> `needs_clarification: true` with an empty `questions` list.

This is belt-and-braces with the orchestrator's safety net: the prompt
asks the LLM to do the right thing, and the orchestrator silently corrects
it if it doesn't.

### Edge cases handled
- LLM returns `needs_clarification: false`: unchanged (existing happy path).
- LLM returns `needs_clarification: true` with 1+ questions: unchanged,
  returns ClarificationRequest.
- LLM returns `needs_clarification: true` with `questions: []` or `null`:
  NEW behavior, auto-advances.
- LLM returns `auto_decisions` with an unknown style preset: existing
  `_apply_auto_decisions` ignores invalid fields; auto-advance proceeds
  with the project default.
- `defer_cast=true` user opt-in: project advances no further than `brief`
  phase; web is responsible for calling `/sprite_cast`.

---

## 3. Fix B backend details

### Asset server (`workers/asset_server.py` - full rewrite)
- New endpoint `POST /<project_id>/refs/upload` with Bearer auth (same
  `API_SERVER_KEY` the bridge uses for `/slash`).
- New `OPTIONS` route on the same path for CORS preflight (browser sends
  it before any POST that carries `Authorization`).
- `make_app()` now accepts `api_key` and `projects_root` kwargs; both have
  safe defaults so the standalone `python asset_server.py` entry point
  still works unchanged.
- Streaming multipart read via `request.multipart()` and `read_chunk(65536)`
  rather than `request.post()` (avoids OOM on large uploads).
- Two-pass PIL validation: `Image.verify()` first (closes the file), then
  re-open to read format + dimensions.
- Magic-byte mime check vs declared mime; mismatch returns
  `code: invalid_mime` so a renamed file gets caught even if the browser
  sets the right Content-Type.
- 5 MB byte cap and 8192×8192 dimension cap (decompression bomb guard).
- ULID-named save (`refs/<26-char ulid>.<ext>`); the on-disk filename is
  always freshly generated, the client's filename is never trusted.
- Atomic `.tmp → final` rename; tempfile is cleaned up on every failure
  path so half-uploaded files never appear in `refs/`.
- Typed JSON error codes: `invalid_mime`, `too_large`, `too_big_dim`,
  `corrupt`, `missing_field`, `unauthorized`, `no_project`, `internal`.

### Bridge (`bridge/server.py`)
- `_start_asset_server` now reads `app["api_key"]` and the
  `SPRITE_PLUGIN_PATH` env var to forward `api_key` and `projects_root` to
  `asset_server.make_app(...)`.
- Bridge's `make_app()` stashes `api_key` on the app dict so the startup
  hook can forward it without re-reading env.

### DB migration (`db.py` SCHEMA_VERSION 3 → 4)
- New column `projects.ref_image_paths TEXT NOT NULL DEFAULT '[]'` storing
  the JSON array of asset-server paths bound to a project.
- Idempotent migration `_migration_v4_project_refs` guarded by
  `PRAGMA table_info(projects)`.
- `_PROJECT_COLUMNS` and `_PROJECT_JSON_COLUMNS` extended so
  `db.update_project(project_id, ref_image_paths=[...])` round-trips JSON.
- Verified live: schema_version flipped to 4, anchors unchanged.

### New slash command `/sprite_set_project_refs`
- Resolves the latest brief-phase project for the current user.
- Validates each path: must start with `/<project_id>/refs/` AND exist on
  disk (defense in depth - catches a typo'd path or an interrupted upload
  before it gets persisted).
- Stores as JSON array on `projects.ref_image_paths`.
- Registered in `SLASH_COMMANDS` and `plugin.yaml` (28/28 zero drift).

### Per-character refs on existing slash commands
- `/sprite_add_character "<descriptor>" refs=path1,path2`
- `/sprite_edit_character "<ord> | <changes>" refs=path1`

Both handlers call the new `_split_brief_and_kvs` parser to separate the
quoted brief from trailing `key=value` tokens; ref paths run through
`_parse_refs_kv` (rejects `..`, NUL, backslash, missing `/refs/`, etc.).
The orchestrator persists the first ref path on
`characters.reference_image_path` and sets `source = 'reference_image'`.

### Cast designer + sheet generator
- `orchestrator.advance_to_cast_phase` reads `project.ref_image_paths` and
  forwards them to each character's `_generate_master_sheet` call so the
  whole cast is anchored to the user's uploaded look.
- `_generate_master_sheet` accepts an optional `ref_image_paths` list. When
  refs are present it routes through `image.edit` (multi-image input;
  gpt-image-2 supports up to 16 refs per call) instead of `image.generate`.
  Without refs, the existing text-to-image path is unchanged.
- `edit_character`: if new refs are passed, the path is forced to
  regenerate (re-anchor) instead of surgical (tweak), and the refs flow
  into `_do_regenerate_edit`'s `image.edit` call.
- Path-traversal-safe ref resolution: `_resolve_ref_paths(project_id, ...)`
  rejects anything that escapes `projects/<pid>/refs/` via `..`, symlink,
  or backslash; missing files are silently dropped.

---

## 4. Fix B web details

### `web/src/lib/uploads.ts` (new)
- Typed `uploadReference(projectId, file, onProgress?)` returning an
  `UploadHandle` with `{ promise, abort }`.
- XHR (not fetch) so upload progress events are reliable across browsers
  the dev canvas targets.
- Client-side gates (mime + 5 MB) before sending bytes - no wasted
  bandwidth on obviously-bad files.
- Typed error union: `invalid_mime | too_large | too_big_dim | corrupt |
  missing_field | unauthorized | no_project | internal | network |
  aborted`.

### `web/src/components/widgets/RefDropZone.tsx` (full rewrite)
- Real `<input type="file" accept="image/png,image/jpeg,image/webp"
  multiple>` with click-to-pick + drag-and-drop handlers.
- Two modes:
  - **Pre-project** (`projectId === null`): files buffered in component
    state; parent reads them via `onPendingChange` and uploads after the
    project_id exists.
  - **Post-project** (`projectId !== null`): files start uploading
    immediately on pick.
- Per-row UI states: `buffered`, `uploading` (with percent), `done`,
  `failed` (with typed error message), `aborted`.
- Per-file `abort()` via the remove button - in-flight XHR is cancelled.
- Unmount-safe via `useRef(true)` guard; XHR completions after unmount
  no-op instead of touching stale parent state.
- Deduplication by `name:size:lastModified`.
- `max` cap respected; "+ add another" affordance hidden when full.

### `web/src/state/store.ts`
- `newProject(brief, opts?: { deferCast?: boolean })` - appends
  `defer_cast=true` to the kv suffix when set.
- `setProjectRefs(paths)` - calls `/sprite_set_project_refs path1,path2`.
- `startCast()` - calls `/sprite_cast` explicitly (used by BriefScreen
  after refs are uploaded).
- `addCharacter(description, refs?)` - appends `refs=...` kv when set.
- `editCharacterRefs(ordinalOrId, changes, refs)` - same for edits;
  separate from `editCharacter` so the existing call sites keep their
  no-refs shape.

### `web/src/components/phases/BriefScreen.tsx`
- New state: `pendingRefs: File[]`, `uploadStage: string | null`.
- `submit()` flow:
  1. Pack brief + chips.
  2. `newProject(packed, { deferCast: pendingRefs.length > 0 })`.
  3. If refs were buffered: read project_id, upload each via
     `uploadReference(pid, file)` sequentially, call
     `setProjectRefs(uploaded.paths)`, then `startCast()`.
  4. Apply per-field overrides (style, duration) once the project is past
     `brief` phase.
- The "cast it" button label updates with `uploadStage` while busy
  (`uploading 1/3`, `binding refs`, `generating cast`).
- RefDropZone is now an active control; the "coming soon" placeholder
  text is gone.

### `web/src/components/popovers/CharacterAddPopover.tsx`
- Adds a `RefDropZone` (post-project; `max=1`) under the description
  textarea. Uploaded paths buffer in `refs[]` state and pass into
  `addCharacter(descriptor, refs)`.

### `web/src/components/popovers/CharacterEditPopover.tsx`
- Replaces the disabled `+ ref` pill in the header with a `ref` indicator
  pill that only renders when `character.source === 'reference_image'`.
- Adds a `RefDropZone` under the visual-tweak input. New refs route
  through `editCharacterRefs(...)` (forces the orchestrator's
  regenerate-with-refs path); without new refs the existing per-field
  edit flow is unchanged.

### `web/src/components/chrome/CharacterCard.tsx`
- Same swap: disabled `+ ref` pill replaced with a conditional `ref`
  indicator.

---

## 5. Files modified

| File | Change |
| ---- | ------ |
| `~/.hermes/plugins/sprite-studio/orchestrator.py` | Fix A: empty-questions auto-advance. Fix B: `_resolve_ref_paths`, refs through `_generate_master_sheet` (`image.edit` when refs), refs through `add_character` / `edit_character` / `_do_regenerate_edit` / `advance_to_cast_phase`. |
| `~/.hermes/plugins/sprite-studio/commands.py` | Fix A: `defer_cast=true` arg path on `sprite_new_handler`. Fix B: `_split_brief_and_kvs`, `_parse_refs_kv`, `sprite_set_project_refs_handler`, `refs=` kv on add/edit, registration in SLASH_COMMANDS. |
| `~/.hermes/plugins/sprite-studio/db.py` | SCHEMA_VERSION 3→4, `_migration_v4_project_refs`, `ref_image_paths` in `_PROJECT_COLUMNS` + `_PROJECT_JSON_COLUMNS`, base CREATE TABLE updated. |
| `~/.hermes/plugins/sprite-studio/workers/asset_server.py` | Full rewrite: upload endpoint with Bearer auth, multipart streaming, magic-byte + size + dimension validation, ULID save, atomic rename, typed errors, CORS preflight, `make_app(api_key, projects_root)` signature. |
| `~/.hermes/plugins/sprite-studio/plugin.yaml` | Added `sprite_set_project_refs` to `provides_commands`. |
| `~/.hermes/plugins/sprite-studio/prompts/brief_clarifier.md` | Rule added forbidding `needs_clarification: true` with empty questions. |
| `~/sprite-studio/bridge/server.py` | `_start_asset_server` forwards `api_key` + `projects_root` to `make_app`; `make_app` stashes `api_key` on the app dict. |
| `~/sprite-studio/web/src/lib/uploads.ts` | New: typed `uploadReference` helper with XHR + progress + abort. |
| `~/sprite-studio/web/src/components/widgets/RefDropZone.tsx` | Full rewrite: real DnD + file input + per-file progress + error UI + pre/post-project modes. |
| `~/sprite-studio/web/src/state/store.ts` | `newProject(opts)`, `setProjectRefs`, `startCast`, `addCharacter(refs)`, `editCharacterRefs`. |
| `~/sprite-studio/web/src/components/phases/BriefScreen.tsx` | Wires real RefDropZone, deferred-cast flow, sequential upload, refs binding, explicit `/sprite_cast`. |
| `~/sprite-studio/web/src/components/popovers/CharacterAddPopover.tsx` | Adds RefDropZone (max 1, post-project). |
| `~/sprite-studio/web/src/components/popovers/CharacterEditPopover.tsx` | Adds RefDropZone, conditional `ref` indicator pill, `editCharacterRefs` routing. |
| `~/sprite-studio/web/src/components/chrome/CharacterCard.tsx` | Swap disabled `+ ref` pill for conditional `ref` indicator. |

---

## 6. Smoke test results

### Asset-server upload endpoint (in-process, real aiohttp + PIL)

| Case | Expected | Actual |
| ---- | -------- | ------ |
| GET /health | 200 ok | ✓ 200 |
| POST without Authorization | 401 unauthorized | ✓ 401 unauthorized |
| POST 256×256 PNG with Bearer | 200 ok, file on disk | ✓ 200, 760-byte ULID-named PNG written |
| POST PNG bytes labelled as image/jpeg | 400 invalid_mime | ✓ 400 invalid_mime ("mime mismatch: declared image/jpeg, actual image/png") |
| POST 'not an image' bytes labelled PNG | 400 corrupt | ✓ 400 corrupt |
| POST 6 MB blob | 413 too_large | ✓ 413 too_large ("upload exceeds 5242880 bytes") |
| POST to non-existent project_id | 404 no_project | ✓ 404 no_project |
| POST GIF | 400 invalid_mime | ✓ 400 invalid_mime ("unsupported mime: 'image/gif'") |
| OPTIONS preflight | 204 with POST + Authorization in CORS allow lists | ✓ 204, methods=`GET, POST, OPTIONS`, headers=`Authorization, Content-Type` |
| refs/ dir populated | 1 file written | ✓ 1 ULID-named PNG present |

### Helper parsers (`_split_brief_and_kvs`, `_parse_refs_kv`)

8/8 split cases passed (quoted, unquoted, with kvs, escaped quotes, empty,
single-quoted). 6/6 ref-validation cases passed (empty, single, multiple,
wrong-subdir, traversal, relative).

### Plugin import + slash registration

`SLASH_COMMANDS` count = 28, `sprite_set_project_refs` present and
callable. plugin.yaml ↔ handlers drift = 0.

### Schema migration

`schema_version` flipped 3 → 4 in-place. `projects.ref_image_paths`
column present after migration. Anchor projects (12 oldest) unchanged
post-migration AND post-implementation.

### Type-check / lint / build

| Check | Result |
| ----- | ------ |
| `npx tsc --noEmit` (web) | exit 0 |
| `npx eslint . --max-warnings 0` (web) | exit 0 |
| `npm run build` (web) | exit 0 (324 KB → 98 KB gzip) |
| `python3 -m py_compile` on all 5 modified .py files | exit 0 |

### Manual UI smoke

Per the prompt, end-to-end UI testing requires a manual stack restart:

```bash
lsof -t -i :8643 -i :9120 -i :5173 | xargs -r kill ; sleep 1
cd ~/sprite-studio && npm run dev
# in another shell:
~/sprite-studio/bridge/run.sh
```

Expected behaviour (programmatically validated above + by code review):
- Brief flow without refs: type a confident brief → "cast it" → page
  advances to CastScreen within seconds (Fix A).
- Brief flow with refs: drop 1-3 image files → "cast it" → button label
  cycles `uploading 1/N` → `binding refs` → `generating cast` → page
  advances to CastScreen with characters anchored to the refs.
- Failure surfaces: 6 MB file rejected client-side; renamed text file
  rejected server-side as `corrupt`; `.gif` blocked.

---

## 7. plugin.yaml drift check

```
declared count: 28
handlers count: 28
declared - handlers: ∅
handlers - declared: ∅
OK 28/28 zero drift
```

---

## 8. Anchor diff

`/tmp/p19a7_anchor_projects.txt` (pre) vs
`/tmp/p19a7_anchor_after.txt` (post): empty diff.

```
12 anchor rows (id, phase, total_cost_usd) unchanged.
```

---

## 9. Backend untouched files verification

Files modified in this prompt are exactly the ones in the table in
section 5 - 14 total (6 plugin .py + 1 plugin .md + 1 plugin .yaml +
1 bridge .py + 5 web .ts/.tsx + 1 new web .ts).

`services/gpt_image.py`, `services/seedance.py`, `services/elevenlabs.py`,
`workers/render_worker.py`, prompt templates other than
`brief_clarifier.md`, all DB helpers other than the migration, and the
existing slash handlers that didn't take refs were left untouched.

---

## 10. Backlog (out of scope for v0.1)

- HEIC / AVIF support on the upload endpoint (PNG/JPEG/WEBP only for now).
- Multiple refs per character - schema only has one
  `reference_image_path` column; the orchestrator already accepts a list
  via `image.edit` so widening this is a column add + a UI tweak.
- Vision-LLM context for the cast designer - refs currently bypass the
  text-only Kimi clarifier and only land at gpt-image-2's multi-ref
  input. Including reference image embeddings or a vision LLM round-trip
  would let the cast designer reason about the photos directly.
- Ref deletion / replacement UI: uploads are append-only.
- Thumbnails for ref images in the project list (no GET serving for
  refs/ today; the file count is shown but the images aren't).
- Multi-user auth - bridge + asset server both assume a single CLI user.
- Telegram surface for ref upload - drop-zone is web-only.

---

P19a-7 COMPLETE. See `/home/drew/sprite-studio/build_prompts/P19a-7_DONE.md`.

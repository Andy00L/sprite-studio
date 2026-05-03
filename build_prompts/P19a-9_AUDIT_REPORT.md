# P19a-9 AUDIT REPORT

Read-only investigation of 4 issues. No code changes. Findings drive a separate fix prompt.

## Environment

- Plugin path: `~/.hermes/plugins/sprite-studio`
- Web path: `/home/drew/sprite-studio/web`
- DB: `~/.hermes/plugins/sprite-studio/state.db`
- Date of audit: 2026-05-03T01:40:56Z
- Audit marker: `/tmp/p19a9_audit_marker`
- Modified files during audit: `/home/drew/sprite-studio/.claude/settings.local.json` (harness-managed permissions file, not project source). No plugin or web sources were modified.

## Issue 1: `/sprite_approve_cast` does not auto-advance to timeline gen

### Symptom
After `/sprite_approve_cast`, `project.phase = 'timeline'` but `shots = []`. The user has to manually run `/sprite_timeline` for shots to be generated.

### Code path

- Slash handler: `commands.py:886-940` `sprite_approve_cast_handler`.
  - Latest project lookup: `commands.py:891` (`db.latest_project_for_user(_USER_ID)`).
  - Idempotent fast-path when phase is already `'timeline'`: `commands.py:898-910` (returns without doing more work).
  - Phase-mismatch guard: `commands.py:911-916`.
  - Orchestrator call: `commands.py:920` `await orchestrator.approve_cast(project_id=project["id"])`.
  - Telegram chat reply: `commands.py:926-932`. Contains the literal hint `"Send /sprite_timeline to plan the shots."` at `commands.py:955`, which is the manual step the user is currently forced into.
  - JSON return: `commands.py:934-940`. No second orchestrator call.
- Orchestrator phase transition: `orchestrator.py:726-783` `approve_cast`.
  - Idempotent already-approved branch: `orchestrator.py:735-742`.
  - Empty-cast and missing-sheet guards: `orchestrator.py:749-758`.
  - The actual write: `orchestrator.py:760-772` sets `is_approved = 1` for each character and `phase = 'timeline'`, `approved_cast_at = ts` on the project. **Does not generate shots.**
  - Returns at `orchestrator.py:778-783`.
- Shot generator that the handler is missing: `orchestrator.py:785-903` `advance_to_timeline_phase`.
  - Phase guard requires `phase == 'timeline'` and rejects otherwise: `orchestrator.py:789-793`.
  - Cast-not-approved guard: `orchestrator.py:800-806`.
  - Calls timeline writer: `orchestrator.py:841-846`.
  - Persists shots: `orchestrator.py:883-886` (`_persist_shot_rows`).
  - Generates reference stills: `orchestrator.py:888-893` (`_generate_all_reference_stills`).
- Existing precedent (the brief auto-advance from P19a-7): `commands.py:474-556` `sprite_new_handler`.
  - Calls `orchestrator.start_project` at `commands.py:492-494`.
  - On `needs_clarification`, returns early: `commands.py:506-518`.
  - On `defer_cast=true`, returns early: `commands.py:522-536`.
  - Otherwise chains into `await orchestrator.advance_to_cast_phase(project_id=...)`: `commands.py:538-541`.
  - On success, returns the merged response: `commands.py:556`.
- Direct timeline trigger (manual fallback): `commands.py:998-1056` `sprite_timeline_handler`. Calls `orchestrator.advance_to_timeline_phase` at `commands.py:1015-1017`. This is the path the user currently has to invoke explicitly.

### Database evidence

Phase distribution at audit time:
```
phase=done    n=9
phase=brief   n=3
phase=cast    n=1
phase=timeline n=0
```
No project is currently parked at `phase='timeline'` with `shots=0`, because the user has been working around the bug by manually running `/sprite_timeline` after each approve. The lack of a stuck-mid-flow record matches the symptom rather than refuting it: every project that ever had `approved_cast_at` set has an `approved_timeline_at` set as well, with shots inserted, and is now `phase='done'`.

The 9 done projects all show the pattern `approved_cast_at < approved_timeline_at` with a delta in the hundreds-to-thousands of seconds, consistent with a manual user step in between.

P19a-7's brief auto-advance is shipped (`build_prompts/P19a-7_DONE.md:1` `P19a-7 COMPLETE`), so the brief-to-cast hop runs automatically. The cast-to-timeline hop is the remaining manual gap.

### Root cause

`sprite_approve_cast_handler` calls only `orchestrator.approve_cast`, which flips `project.phase` from `'cast'` to `'timeline'` and stamps `approved_cast_at` but does not produce shots. The shot-generating function `advance_to_timeline_phase` exists (`orchestrator.py:785`), is callable from this state (its phase guard is `phase == 'timeline'`), and is not chained from the handler. The fix mirrors the brief auto-advance precedent at `commands.py:538-541`.

### Proposed fix sketch (do not apply)

After the existing `approve_cast` call returns successfully and we are on a non-`already_approved` path, chain a second orchestrator call:

```
result = await orchestrator.approve_cast(project_id=project["id"])
# new:
if not result.get("already_approved"):
    timeline = await orchestrator.advance_to_timeline_phase(
        project_id=result["project_id"],
    )
    # merge shot count / errors / shots into the response
```

The combined response should expose both the cast confirmation (character_count) and the timeline outcome (shot_count, total_duration, shots, errors), mirroring `_format_cast_response` from the brief flow (`commands.py:556`).

### Edge cases the fix must handle

- **Empty cast.** Already guarded at `orchestrator.py:750-751` (`approve_cast` rejects with `"cannot approve an empty cast"`). The chain is unreachable in this case.
- **Idempotent re-approve.** When the project is already at `phase='timeline'`, `approve_cast` returns `already_approved=True` (`orchestrator.py:735-742`). The chain should skip `advance_to_timeline_phase` because shots may already exist; `sprite_timeline_handler` already has a "shots already exist, return them" idempotency at `commands.py:1007-1011` and the chain should match.
- **Approve called twice in quick succession (race).** Both calls hit `db.latest_project_for_user` and could race past the phase check. The current handler is not transactional. Out of scope for this fix unless the chain introduces new windows.
- **Timeline writer fails.** `advance_to_timeline_phase` already sets `phase='failed'` and `error_message` on `ProviderContentPolicyError`, `TimelineGenerationFailedError`, and any `SpriteStudioError` (`orchestrator.py:847-859`). The handler must surface the failure (the cast was approved successfully, but timeline generation failed) without rolling the cast approval back.
- **Telegram chat surface.** The chat reply at `commands.py:943-960` (`_format_cast_approved_for_telegram`) currently ends with `"Send /sprite_timeline to plan the shots."` This text would be misleading after auto-advance and needs an update.
- **Reference-still generation partial failure.** `advance_to_timeline_phase` already returns a structured `errors` list at `orchestrator.py:902` for per-shot still generation failures. The chained handler should surface that list rather than treating non-empty errors as a hard failure.

### Open questions

- Should the chained `advance_to_timeline_phase` fire synchronously (block the handler until shots and reference stills exist) or be dispatched to a background task? The current `sprite_timeline_handler` is synchronous; matching it keeps behavior simple, but pushes the user-facing latency from approve-cast to approve-cast-plus-timeline. Both `_call_timeline_writer` (LLM) and `_generate_all_reference_stills` (image gen) are non-trivial in time.
- Should idempotent re-approve (`already_approved=True`) also auto-trigger `advance_to_timeline_phase` if shots happen to be empty? Recovery scenarios where the first attempt failed mid-timeline-gen would benefit, but this widens the function's contract.

## Issue 2: `transition_to_next` change does not persist (or appears not to)

### Symptom

Clicking a transition pill in the timeline opens the popover, the user picks a new value, the save call returns, the popover closes. The pill in the timeline visibly does not reflect the new selection.

### Code path

- Allowlist (the suspected culprit): `db.py:916-919`. Current value:
  ```python
  _SHOT_SAFE_FIELDS = {
      "duration_seconds", "setting", "action", "camera", "emotion",
      "narration_line", "transition_to_next",
  }
  ```
  `transition_to_next` is **already** in the allowlist. The original suspicion (that it was missing) is incorrect.
- Allowlist enforcement: `db.py:922-972` `update_shot_fields`. Filter at `db.py:958`. Returns `{"updated": False, "reason": "no_safe_fields"}` at `db.py:959-960` only when nothing in `fields` is in the allowlist.
- DB column: `db.py:148-149`. `transition_to_next TEXT NOT NULL DEFAULT 'cut' CHECK (transition_to_next IN ('cut','fade','dissolve','match_cut'))`.
- Allowed values constant (backend): `db.py:29` `VALID_SHOT_TRANSITIONS = ("cut", "fade", "dissolve", "match_cut")`.
- Allowed values constant (frontend): `web/src/lib/constraints.ts:24-29` `TRANSITIONS`. Identical set, identical order.
- Dedicated handler: `commands.py:1959-2009` `sprite_set_shot_transition_handler`. Validates `kind` against `db.VALID_SHOT_TRANSITIONS` at `commands.py:1978-1983`, then calls `db.update_shot_fields(shot["id"], allowed_phases=_TIMELINE_EDITABLE_PHASES, transition_to_next=kind)` at `commands.py:1998-2002`.
- Phase gate: `commands.py:1804` `_TIMELINE_EDITABLE_PHASES = {"timeline"}`. Editing is only legal while the project is in the `timeline` phase.
- Frontend popover: `web/src/components/popovers/TransitionPopover.tsx:30-42` calls `setShotTransition(shotId, picked)`.
- Store action: `web/src/state/store.ts:707-720` `setShotTransition`. Sends `${shotIdOrOrdinal} | ${transition}` to `sprite_set_shot_transition` at line 712, then calls `await get().refreshShow()` at line 714.
- `refreshShow` and shot-state assignment: `web/src/state/store.ts:354-380`. The fetched `data.shots` from `/sprite_show` is assigned directly into the store at `web/src/state/store.ts:371`.
- `/sprite_show` shot serializer: `commands.py:1417-1434`. The shot dict returned to the web client lists exactly these keys: `id, ordinal, duration_seconds, setting, action, camera, has_dialog, characters_present, dialog_speakers, character_dialog, render_status, reference_still_path, rendered_video_path`. The dict **does not include `transition_to_next`** (and also does not include `narration_line` or `emotion`).
- Initial timeline gen serializer: `commands.py:965-995` `_format_existing_timeline`. The shot dict it returns lists `id, ordinal, duration_seconds, setting, action, narration_excerpt, reference_still_path, character_names`. **Also does not include `transition_to_next`.**
- Frontend type contract: `web/src/types/sprite.ts:51-73` declares `Shot.transition_to_next: ShotTransition` as a non-nullable field. The runtime data violates this contract because the server never sends the field.
- Pill consumer: `web/src/components/timeline/TransitionPill.tsx:9-36`. Renders `↦ ${current}` (line 33). With `current === undefined`, it renders `↦ undefined`.
- Pill placement in the timeline: `web/src/components/phases/TimelineScreen.tsx:232-237` (`<TransitionPill current={s.transition_to_next} ... />`).
- Popover seed: `web/src/components/popovers/PopoverHost.tsx:71-77` (`<TransitionPopover current={s.transition_to_next} ... />`). Seeds the picker from the same undefined value.

### Root cause

The dedicated `/sprite_set_shot_transition` handler writes the new transition to the database successfully (allowlist already includes the column, validation passes, `update_shot_fields` issues the SQL `UPDATE`). The bug is on the read side: `/sprite_show` (`commands.py:1417-1434`) and `_format_existing_timeline` (`commands.py:965-995`) both omit `transition_to_next` from their per-shot output. The frontend's `s.transition_to_next` is therefore `undefined` from initial load, undefined after every save, and undefined after every refresh. The TransitionPill displays `↦ undefined`, which the user reads as "the value reverted" or "nothing happened" because the visible label never reflects the picked option.

The same omission affects `narration_line` and `emotion` in `/sprite_show` output, but those fields are not currently rendered by any component identified in this audit.

### Two possible fixes

A. **Add the field to the allowlist.** Not applicable. `transition_to_next` is already in the allowlist; the allowlist is not the problem.

B. **Add the field to the response formatter.** Add `"transition_to_next": s.get("transition_to_next")` to the shot dict at `commands.py:1417-1434` (and, for consistency, also at `_format_existing_timeline:973-985`). The DB row already has the value (`db.list_shots` returns a full row via `_shot_to_dict`).

### Recommendation

Fix B. The `/sprite_show` shot serializer is the single chokepoint for the web client; widening it to include `transition_to_next` (and ideally also `emotion` and `narration_line` for parity with the `Shot` type contract at `web/src/types/sprite.ts:51-73`) restores the round-trip. The `setShotTransition` action and the dedicated handler are already correct.

### Open questions

- Should `_format_existing_timeline` (`commands.py:965-995`) and the `/sprite_show` shot serializer be unified into one helper, since they currently maintain two separate hand-rolled key lists that have already drifted from each other? Out of scope for the bug fix, worth noting for v0.2.
- Are there other missing fields that the `Shot` TypeScript type at `web/src/types/sprite.ts:51-73` claims will exist (e.g. `cost_usd`, `updated_at`, `render_error`) but the `/sprite_show` payload omits? A scan suggests yes; not covered by this issue's symptom.

## Issue 3: `character_dialog` field edit fails with `no_safe_fields`

### Symptom

Editing the dialog list in the ShotEditPopover and clicking save returns `{"updated": false, "reason": "no_safe_fields"}` from `/sprite_edit_shot_field`. The error appears in the store at `web/src/state/store.ts:432-434` and surfaces as `"edit character_dialog failed: no_safe_fields"`.

### Code path

- Allowlist (same one as Issue 2): `db.py:916-919`. **Does not include `character_dialog`.** Also does not include `characters_present`, `dialog_speakers`, or `has_dialog`.
- Handler: `commands.py:1859-1948` `sprite_edit_shot_field_handler`. Build of the kwargs at `commands.py:1912-1916` (`db.update_shot_fields(shot["id"], allowed_phases=_TIMELINE_EDITABLE_PHASES, **{field: typed_value})`).
- Frontend popover serializer: `web/src/components/popovers/ShotEditPopover.tsx:80-84`:
  ```ts
  const oldDialog = JSON.stringify(s.character_dialog ?? []);
  const newDialog = JSON.stringify(dialog);
  if (oldDialog !== newDialog) {
    await editShotField(s.id, 'character_dialog', newDialog);
  }
  ```
  The dialog list is JSON-encoded client-side before crossing the bridge.
- Same popover, `characters_present` (lines 69-79):
  ```ts
  if (presentChanged) {
    await editShotField(
      s.id,
      'characters_present',
      JSON.stringify([...presentSet]),
    );
  }
  ```
  Same problem: not in `_SHOT_SAFE_FIELDS` either.
- Store action: `web/src/state/store.ts:414-441` `editShotField`. Wraps `${ordinalOrId} | ${field}=${value}` at line 425, calls `sprite_edit_shot_field` at line 431, surfaces `r.reason` as a user-visible error at lines 432-435.
- DB column: `db.py:147` `character_dialog TEXT`. JSON-typed via `_SHOT_JSON_COLUMNS` at `db.py:83`.
- DB read path: `db.py:404-405` `_shot_to_dict` calls `_row_to_dict(row, _SHOT_JSON_COLUMNS)`. The deserializer at `db.py:390-393` runs `json.loads` on the raw string. So the column is read as a parsed list back into Python.
- DB write path used by the handler: `db.py:962-970` `update_shot_fields`. Builds raw SQL `UPDATE shots SET {set_clause}, updated_at = ? WHERE id = ?` and writes the value as-is. **Unlike `_build_update` at `db.py:606-624`, it does not call `json.dumps` for JSON columns when the value is non-string.** A JSON-typed column would only round-trip safely if the frontend always sends a valid JSON string (which `ShotEditPopover.tsx:81` does via `JSON.stringify`).

Verified DB content for sanity:
```
SELECT character_dialog FROM shots WHERE character_dialog IS NOT NULL LIMIT 3;
```
returns JSON-array text like `[{"char_id":"...","line":"..."}, ...]`. The column stores a serialized JSON string, and the read path parses it back.

### Root cause

`_SHOT_SAFE_FIELDS` (`db.py:916-919`) omits `character_dialog`. The `/sprite_edit_shot_field` path filters supplied fields against the allowlist at `db.py:958`, so when the popover sends `character_dialog=<json>` the filter strips it and `update_shot_fields` returns `{"updated": False, "reason": "no_safe_fields"}` at `db.py:959-960`. The frontend store then writes that error into UI state.

### Proposed fix sketch (do not apply)

Add `character_dialog` to `_SHOT_SAFE_FIELDS`:
```python
_SHOT_SAFE_FIELDS = {
    "duration_seconds", "setting", "action", "camera", "emotion",
    "narration_line", "transition_to_next",
    "character_dialog",
}
```

The same fix is needed for `characters_present`, since the popover also writes that field via the same path (`ShotEditPopover.tsx:74-78`) and would silently fail today. `dialog_speakers` and `has_dialog` are not currently exercised by the popover and can be deferred.

Optional safety addition: validate `character_dialog` is well-formed JSON inside `update_shot_fields` before issuing the `UPDATE`. The current write path stores whatever string the caller hands over, so a malformed payload would corrupt the column on next read (`json.loads` raises in `_row_to_dict`).

### Edge cases the fix must handle

- **Empty array.** `JSON.stringify([])` yields the literal string `"[]"`. The DB column accepts it; `_shot_to_dict` deserializes it back to `[]`. The popover currently writes `JSON.stringify([])` when the user removes the last line (`ShotEditPopover.tsx:81`), so this is already on the write path.
- **`null` vs `[]`.** The schema permits `NULL` for `character_dialog` (`db.py:147`). The popover never writes a literal `null`; it always sends an array. Treat `null` and `[]` as equivalent for the "no dialog" case, matching the existing `_format_existing_timeline` and `_persist_shot_rows` semantics.
- **Malformed JSON from a misbehaving client.** Without server-side validation, a string that is not a JSON array would be stored verbatim and then break the next read at `db.py:392`. Consider a `try: json.loads(value); assert isinstance(parsed, list)` guard inside `update_shot_fields` for known JSON columns.
- **Phase guard.** The handler already passes `allowed_phases=_TIMELINE_EDITABLE_PHASES` (`commands.py:1914`), so dialog edits past the timeline phase are blocked.
- **Render side effects.** Dialog edits affect Seedance audio routing (`seedance.py:608-622` audio decision tree). After a dialog edit the existing reference still need not be regenerated, but `has_dialog` may need recomputing if the fix scopes that field. Out of scope for the immediate one-line allowlist fix.

### Open questions

- Should the popover also write `has_dialog` (computed from `dialog.length > 0`) when it writes `character_dialog`? Today the field is owned by `_persist_shot_rows` (`orchestrator.py:2467`) at timeline-gen time and never updated afterward. Without a fix, post-timeline dialog edits will leave `has_dialog` stale, which influences Seedance audio routing.
- Same question for `characters_present` and `dialog_speakers`: editing one without the other can put a `dialog_speakers` row out of sync with `characters_present`. The popover currently does not touch `dialog_speakers`.

## Issue 4: Multi-ref to Seedance (v0.2 research)

### Current behavior

- Submit payload built at `services/seedance.py:220-230`:
  ```python
  body: dict[str, Any] = {
      "model": model,
      "prompt": prompt,
      "images": [data_uri],
      "metadata": {
          "duration": duration,
          "resolution": resolution,
          "ratio": ratio,
          "generate_audio": generate_audio,
      },
  }
  ```
  The `images` array contains exactly one element: a base64 data URI of the reference still.
- Image encoding: `services/seedance.py:725-758` `_encode_image_data_uri`. Reads the file, optionally downscales over an 8 MB ceiling, base64-encodes, prefixes `data:image/png;base64,`. Returns a single string.
- Public method signature: `services/seedance.py:166-177` `submit(image: Path, ...)`. Single-image input.
- Convenience wrapper: `services/seedance.py:580-700` `image_to_video(image: Path, ...)`. Forwards a single image to `submit`.
- Call site: `workers/render_worker.py:653-663`:
  ```python
  video_path = await self.video.image_to_video(
      model=self._video_model,
      image=Path(ref_path_str),
      prompt=prompt,
      duration=int(shot["duration_seconds"]),
      resolution="720p",
      ratio="9:16",
      save_to=shot_dir,
      project_id=project_id,
      has_dialog=bool(shot.get("has_dialog")),
  )
  ```
  Passes one image: the per-shot reference still located at `shot["reference_still_path"]`. The `characters_present` IDs and per-character `master_sheet_path` are not consulted by this call site today.
- 413 retry path: `services/seedance.py:281-302`. If submit returns 413, the image is re-encoded with a tighter long-edge cap and `body["images"]` is overwritten with the new single URI (line 284). Retries once.
- Verified live shape from prior captures: `build_prompts/_verified_shapes/_SUMMARY.md:55-66`. Confirms the canonical request body uses an array-typed `images` field. Lines 71-75 also document that TokenRouter internally maps `images[]` to a `content[]` array of `{type, image_url}` entries on the upstream, which suggests the array may carry more than one image structurally.

### Seedance API capabilities

- fal.ai endpoint listing: https://github.com/fal-ai/seedance-2.0-api
  - **image-to-video** (`bytedance/seedance-2.0/image-to-video`): single `image_url` string plus optional `end_image_url` string. Maximum 2 frames (start + optional end). Pricing $0.3024/sec at 720p (standard) or $0.2419/sec (fast).
  - **reference-to-video** (`bytedance/seedance-2.0/reference-to-video`): `image_urls` list. Up to 9 reference images per request. Up to 3 video clips and 3 audio clips additionally, capped at 12 files total. Pricing $0.3024/sec at 720p (standard) or $0.2419/sec (fast). Video inputs apply a 0.6x price multiplier; image inputs do not.
- fal.ai image-to-video parameter spec: https://fal.ai/models/bytedance/seedance-2.0/image-to-video confirms `image_url` (single string, JPEG/PNG/WebP, max 30 MB) and optional `end_image_url`.
- fal.ai reference-to-video parameter spec: https://fal.ai/models/bytedance/seedance-2.0/reference-to-video confirms up to 9 images via `image_urls` (array of strings).
- BytePlus ModelArk reference: https://docs.byteplus.com/en/docs/ModelArk/1520757 (the upstream that TokenRouter wraps). The detail pages for `2291680` and `2298881` were not directly fetchable through this audit's tooling.

### Cost difference

Per fal.ai's listing both image-to-video and reference-to-video at 720p are $0.3024/sec (standard) or $0.2419/sec (fast); price parity for image inputs. Adding video inputs to reference-to-video applies a 0.6x multiplier (cheaper, not more expensive). For the proposed v0.2 change (extra image references only), no price increase is documented.

### Proposed v0.2 change

Per-shot, send the existing reference still plus up to N character sprite sheets where N is bounded by the endpoint's image cap and by `len(shot["characters_present"])`.

- File: `services/seedance.py`.
  - `submit` signature widens from `image: Path` to `images: list[Path]` (or accept both for backward compat). Build `body["images"]` from a comprehension over the input list, each encoded via `_encode_image_data_uri`.
  - `image_to_video` signature mirrors that change.
  - The 413 retry path at lines 281-302 needs to re-encode every image, not just the first. Consider downscaling all of them in the retry pass.
  - Ordering may matter to the upstream. Per `_verified_shapes/_SUMMARY.md:71-75`, `images[0]` becomes `content[1]` on the upstream; the prompt text occupies `content[0]`. Whichever input position the upstream treats as "anchor" should be the reference still; subsequent positions should be the sprite sheets.
- File: `workers/render_worker.py`. Around `render_worker.py:643-663`:
  - Look up character sheets for `shot["characters_present"]` using `db.list_characters(project_id)` (already cached at the project level by `render_project`, but not currently threaded down to the per-shot render).
  - Filter to characters with non-null `master_sheet_path` that exist on disk.
  - Order them to match `characters_present` (which is itself ordered by appearance per `orchestrator.py:2451-2455`).
  - Pass `images=[ref_path, *sheet_paths[:M]]` where M is the chosen cap.
- New shape would be:
  ```python
  body = {
      "model": ...,
      "prompt": ...,
      "images": [ref_data_uri, sheet_1_uri, sheet_2_uri, ...],
      "metadata": {...},
  }
  ```

### Risks

- **Endpoint identity is ambiguous.** The TokenRouter endpoint (`https://api.tokenrouter.com/v1/video/generations` per `services/seedance.py:256`) accepts an `images` array, but it is not documented from primary sources whether `dreamina-seedance-2-0-fast-260128` and `dreamina-seedance-2-0-260128` map to the upstream image-to-video (single + optional end frame) or reference-to-video (up to 9). The `images` field name is consistent with TokenRouter's own canonicalization, not with either fal.ai endpoint name. A live test with `images` of length 2 and 3 is required before scoping any non-trivial change. The 400 response captured at `_verified_shapes/_SUMMARY.md:71-75` shows the upstream's internal `content[]` list, which structurally accepts many entries; whether the model accepts them is the unknown.
- **Style mixing.** Sprite sheets are stylized (preset descriptors), and the reference still is composited per shot. Mixing them as references may pull the rendered video toward the sheet style and away from the still's framing. Without a test pass, the directional effect cannot be predicted.
- **Payload size.** Each PNG sheet at the current `IMAGE_LONG_EDGE_AFTER_DOWNSCALE = 1280` produces a base64 string of several MB. Sending 4 images may approach the upstream's body limit; the 413 retry path is single-image today.
- **Cost meter.** `_pricing.seedance_cost_usd` (referenced at `services/seedance.py:232-235`) is keyed on model and resolution and does not factor image count today. If the upstream applies a per-image surcharge (none documented), the meter would under-bill. fal.ai's pricing does not document such a surcharge for images, only the 0.6x multiplier for video inputs.

### Recommendation for v0.1 vs v0.2

Hold the current single-image behavior for v0.1. The single-image path is verified live and shipping. For v0.2, scope a focused investigation:
1. Run a live test against TokenRouter with `images` of length 2 (reference still + one sprite sheet) on the fast model. Confirm the response is HTTP 200 and the rendered video honors both inputs.
2. If step 1 succeeds, repeat with length 4 (reference still + 3 sheets).
3. If both succeed, scope the `services/seedance.py` and `workers/render_worker.py` changes outlined above.

This sequencing protects v0.1 against an unverified payload shape and keeps v0.2 scoped to a measurable improvement.

### Open questions

- Does TokenRouter's `dreamina-seedance-2-0-*` model route to image-to-video or reference-to-video upstream? Required for any sizing decision.
- Does the Seedance reference-to-video model expect `@Image1` style anchor markers in the prompt (per fal.ai's parameter spec for reference-to-video)? If so, the prompt builder at `workers/render_worker.py:775` (`_build_seedance_prompt`) needs to be aware of how many images are attached and inject anchor names for them.
- Are sprite sheets the right reference asset, or should we synthesize a "character chip" (single tight portrait) first, since the sheet contains multiple poses and perspectives? This affects the visual outcome and the production pipeline's storage layout.

## Summary table

| # | Issue | Severity | Suggested fix | Files touched |
| - | --- | --- | --- | --- |
| 1 | `/sprite_approve_cast` does not chain `advance_to_timeline_phase` | HIGH (blocks flow) | Chain the second orchestrator call from the handler | `commands.py` (handler), unchanged in `orchestrator.py` |
| 2 | `transition_to_next` missing from `/sprite_show` and `_format_existing_timeline` shot dicts (allowlist NOT the bug) | MEDIUM | Add the field to both response formatters | `commands.py:1417-1434`, `commands.py:965-995` |
| 3 | `character_dialog` (and `characters_present`) missing from `_SHOT_SAFE_FIELDS` | MEDIUM | Add to allowlist; consider JSON validation guard | `db.py:916-919` |
| 4 | Seedance multi-ref (v0.2 research) | LOW (v0.2) | Live-test the upstream image cap, then plumb sheets through | `services/seedance.py`, `workers/render_worker.py` |

## Modified files during audit

```
$ find /home/drew/.hermes/plugins/sprite-studio /home/drew/sprite-studio -newer /tmp/p19a9_audit_marker -type f \
    -not -path '*/__pycache__/*' \
    -not -path '*/projects/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/dist/*' \
    -not -path '*/build_prompts/P19a-9_AUDIT_REPORT.md'
/home/drew/sprite-studio/.claude/settings.local.json
```

The single result is the harness's per-project permissions file, updated by the runtime when tools were approved during the audit. No plugin source (`~/.hermes/plugins/sprite-studio/**/*.py`) and no web source (`/home/drew/sprite-studio/web/src/**`) was modified.

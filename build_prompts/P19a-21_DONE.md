# P19a-21: Timeout + Audio Safety - DONE

## Bug 1: Kimi ReadTimeout

- `services/_http.py`: added two named per-request `httpx.Timeout` objects.
  - `DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)`
  - `LLM_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)`
  - The shared `AsyncClient`'s default Timeout (`HTTP_CONNECT`/`HTTP_READ_CHAT=180.0`) is unchanged.
- `services/tokenrouter.py`: chat path now reads its default from `_http.LLM_TIMEOUT` instead of a local `DEFAULT_LLM_READ_TIMEOUT_S = 300.0` constant. The existing `read_timeout_seconds` per-request override remains intact, so callers (orchestrator timeline writer, cast designer) keep working.
- Effect: when no override is supplied, Kimi calls now wait up to 600s per attempt (was 300s). With the existing 3-attempt retry budget that gives ~30 min worst-case for the unguarded path before the orphan failure surfaces, instead of ~15 min.
- Verified: `LLM_TIMEOUT.read == 600.0`, `DEFAULT_TIMEOUT.read == 120.0`, both differ.

## Bug 2: OutputAudioSensitiveContentDetected

- `services/errors.py`: two new typed errors.
  - `class SeedanceTaskFailedError(ProviderServerError)`: terminal vendor-side task failure on the polling endpoint.
  - `class SeedanceAudioSafetyError(SeedanceTaskFailedError)`: audio-only refusal that the caller can recover from by retrying with `generate_audio=False`.
- `services/seedance.py`:
  - New static helper `_extract_failure_object(body)` walks `body.data.data.error` (live shape on Hippo Incident), then `body.data.error`, then `body.error`, returning the `{code, message}` dict or None.
  - `poll()` FAILURE branch now extracts `err_code`/`err_msg` and routes:
    - `err_code == "OutputAudioSensitiveContentDetected"` -> `SeedanceAudioSafetyError`
    - else -> `SeedanceTaskFailedError`
  - The `task_id`, `code`, and `vendor_message` are passed via the existing `extra={...}` channel (matches `SpriteStudioError` constructor signature).
- `workers/render_worker.py`:
  - Imports `SeedanceAudioSafetyError`.
  - Per-shot try block adds a `SeedanceAudioSafetyError` catch BEFORE the generic `SpriteStudioError` catch. The recovery path retries `image_to_video(..., generate_audio=False)` exactly once. If the second attempt also fails the inner catch falls through to the same per-shot failure recording, so retry-of-retry is impossible.
  - On successful retry, the `update_shot` write at the end of the per-shot pipeline persists `audio_safety_fallback=1`. On non-retried success it persists `0`.
- `models.py`: `Shot.audio_safety_fallback: bool = False`. `row_to_model` adds the int-to-bool conversion alongside the existing `has_dialog` one.
- `db.py`:
  - `SCHEMA_VERSION` bumped 5 -> 6.
  - Base `CREATE TABLE shots` includes the new column with `CHECK (audio_safety_fallback IN (0,1))` so fresh installs ship the constraint.
  - `_migration_v6_audio_safety_fallback` is idempotent (PRAGMA table_info guard) and wired into `_migrate()` after v5.
  - `_SHOT_COLUMNS` set updated so `update_shot()` accepts the new field.

### Migration test
- Backed up live state.db to `/tmp/state_backup_p19a21.db`. Ran v6 migration manually:
  - before: column absent, schema_version=5
  - after: column present, schema_version=6, all 63 existing rows defaulted to 0
  - second invocation was a no-op (idempotent).
- Restarted bridge against live state.db (orchestrator runs migrations on import). Confirmed via Python query:
  - schema_version=6, audio_safety_fallback in shots cols, 63/63 rows at default 0.

## Verification

- `py_compile` clean across all 23 .py files in the plugin.
- Bridge restarted via `bash scripts/kill-bridge.sh` then `npm run dev:bridge &`.
  - `GET /health` -> `{"status": "ok", "plugin_loaded": true, "command_count": 29}`.
  - No tracebacks in `/tmp/sprite_bridge_p19a21.log`.
  - Web at `http://localhost:5173` still serving HTTP 200 (the user's running `concurrently` was unaffected).
- File md5 diff vs `/tmp/p19a21_pre.md5` (the spec-defined 5-file pre-edit manifest):
  - `services/_http.py`: FAILED (changed)
  - `services/seedance.py`: FAILED (changed)
  - `services/errors.py`: FAILED (changed)
  - `services/_retry.py`: OK (unchanged, intentionally; the routing happens upstream of `_retry`)
  - `workers/render_worker.py`: FAILED (changed)
- The full set of files changed is 7 (one more than the 6 the spec listed):
  - 6 listed in spec Phase 4.3: `_http.py`, `seedance.py`, `errors.py`, `render_worker.py`, `models.py`, `db.py` - all changed.
  - 1 deviation: `services/tokenrouter.py` was edited to actually wire `LLM_TIMEOUT` into the chat POST. Without it the new constant would be dead code. Captured in the supplementary md5 file `/tmp/p19a21_pre_full.md5`.

## Public API

- Additions only:
  - `services._http.DEFAULT_TIMEOUT`, `services._http.LLM_TIMEOUT`
  - `services.errors.SeedanceTaskFailedError`, `services.errors.SeedanceAudioSafetyError`
  - `services.seedance.VideoClient._extract_failure_object` (static method)
  - `models.Shot.audio_safety_fallback`
  - `db._migration_v6_audio_safety_fallback`
- Removals: `services.tokenrouter.DEFAULT_LLM_READ_TIMEOUT_S` (was an internal module-level constant, only referenced inside `tokenrouter.py` itself; replaced by the shared `_http.LLM_TIMEOUT.read`).
- No class signatures changed. The seedance FAILURE path used to raise `ProviderServerError`; it now raises `SeedanceTaskFailedError`, which is a subclass of `ProviderServerError`, so existing `except ProviderServerError` and `except SpriteStudioError` sites still catch it.

## Live test pending

- Need a brief that reliably trips the audio filter to confirm the retry path works end-to-end. Hippo Incident shot 5 ("marshmallow ritual" + cow tarp dialog) is a known repro candidate.
- Expected behavior on next reproduction:
  1. First Seedance attempt fails with `OutputAudioSensitiveContentDetected`.
  2. `render_worker` logs `seedance audio safety filter on shot N (...); retrying once with generate_audio=False`.
  3. Second attempt succeeds with `audio=False reason=explicit_request`.
  4. Shot row in DB has `audio_safety_fallback=1`.
  5. Final stitch produces the full N-shot output (no shot dropped).

## Cost note

- The audio safety retry submits a NEW Seedance task, so the affected shot is double-billed (failed first attempt + successful second attempt). The first attempt's cost is recorded by `image_to_video`'s `mark_job_failed` path; the second attempt's cost is recorded normally. Both feed `db.increment_project_cost`, so the project total reflects the doubled spend.
- For a 720p/9:16/standard 5s shot the duplicated cost is ~$0.6048 added per audio-safety event.

## Edge cases

1. Audio safety filter fires on the second attempt too: extremely unlikely (audio is off), but if it does, the inner generic-failure catch records the shot as failed via the standard path. The shot is dropped; render proceeds with N-1 shots per existing resilience policy.
2. Schema migration order: `_migrate()` runs at module import (`db._migrate()` at the bottom of db.py), which is before the orchestrator boots in-flight job sweepers. Confirmed by reading `_migrate()` and the bottom of db.py.
3. gpt-image-2, ElevenLabs, Seedance call paths: each already passes its own per-request `httpx.Timeout(...)` (gpt_image at `_http.HTTP_READ_IMAGE=240`, elevenlabs at 120s read, seedance at 30s/60s/120s for poll/submit/download). The shared `AsyncClient`'s default 180s read is a fallback that nothing relies on. Adding `DEFAULT_TIMEOUT` and `LLM_TIMEOUT` does not affect any of these.
4. `services/_retry.py`: not touched. The retry layer wraps each attempt; bumping the per-attempt timeout from 300 to 600 increases the worst-case attempt duration and therefore the worst-case total timeout, which is the intended fix.

## Acceptance gates

- [x] `/tmp/p19a21_marker` created (Phase 0)
- [x] `/tmp/p19a21_pre.md5` captured before edits (Phase 0); supplementary `/tmp/p19a21_pre_full.md5` captures all 8 candidate files
- [x] All target files read in full before any edit (Phase 1)
- [x] Current timeout values documented (Phase 1.2; see "Bug 1" section above)
- [x] `DEFAULT_TIMEOUT` and `LLM_TIMEOUT` exported from `_http.py` (Phase 2.3)
- [x] Kimi POST passes per-request timeout sourced from `_http.LLM_TIMEOUT` (Phase 2.3)
- [x] `LLM_TIMEOUT.read == 600.0` confirmed (Phase 2.4)
- [x] `SeedanceAudioSafetyError` subclasses `SeedanceTaskFailedError` which subclasses `ProviderServerError` (Phase 3.2)
- [x] seedance.py poll branches on `err_code == "OutputAudioSensitiveContentDetected"` before generic raise (Phase 3.3)
- [x] render_worker.py catches the new error, retries with `generate_audio=False`, only once (Phase 3.4)
- [x] `Shot.audio_safety_fallback` field exists, default False (Phase 3.5)
- [x] DB migration v6 idempotent, adds column with default 0, CHECK constraint included (Phase 3.5)
- [x] All `py_compile` checks pass (Phase 4.1)
- [x] Bridge restart healthcheck returns 200, plugin_loaded=true, 29 commands (Phase 4.2)
- [x] All 6 spec-listed files modified plus tokenrouter.py for the actual LLM_TIMEOUT wire-up (Phase 4.3)
- [x] Public API additions only; the only removal is the now-unused internal `DEFAULT_LLM_READ_TIMEOUT_S` (Phase 4.4)
- [x] Report written at `build_prompts/P19a-21_DONE.md` (Phase 5)
- [x] No em dashes or banned buzzwords introduced in new code (existing em dashes in pre-existing migration docstrings left intact)
- [x] No git work performed (P19a-21 does not commit or push)

```
P19a-21 COMPLETE.
```

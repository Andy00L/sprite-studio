# P19a-12 DONE

Fixes the three audit-report issues from `P19a-9_AUDIT_REPORT.md`. All
changes are backward compatible with existing DB rows and slash-command
arg shapes; existing TS readers keep working without source changes.

## 1. Three-fix summary

| # | Severity | Issue | Resolution |
|---|----------|-------|------------|
| 1 | HIGH | `/sprite_approve_cast` left projects stuck at `phase='timeline'` with empty `shots` because timeline generation was never chained. | Background task with strong-reference tracking, sanitized error path, `timeline_status` field on `/sprite_show`, frontend polling, plugin-startup orphan recovery. |
| 2 | MEDIUM | TransitionPill always showed wrong value because `/sprite_show` and `_format_existing_timeline` did not return `transition_to_next` (and other Shot-contract fields). | One unified `_shot_to_response_dict` helper used by both call sites; defensive `'cut'` default; tolerant JSON-list coercion for legacy rows. |
| 3 | MEDIUM | Editing `character_dialog` and `characters_present` failed with `no_safe_fields`. | Allowlist extended; per-field JSON validators with structured `invalid_value` errors; `has_dialog` and `dialog_speakers` auto-derived from canonical `character_dialog` so the audio-routing flag and speaker list never drift out of sync. |

## 2. Issue 3 details: allowlist + JSON validation + dependent fields

`db.py` changes:

* `_SHOT_SAFE_FIELDS` now includes `character_dialog` and `characters_present`.
  `has_dialog` and `dialog_speakers` are NOT in the allowlist on purpose
  (derived in the same UPDATE; frontend cannot set them directly).
* Added `_validate_character_dialog(value)` and `_validate_characters_present(value)`.
  Both accept a Python list/dict OR a JSON string, validate shape, and
  return a canonical JSON string (`ensure_ascii=False, separators=(',',':')`).
  Bad JSON or wrong shape raises `ValueError`.
* Added `_SHOT_FIELD_VALIDATORS` mapping; `update_shot_fields` runs validators
  and on failure returns `{updated: False, reason: 'invalid_value', field, detail}`
  without writing.
* When `character_dialog` is in the write set, `has_dialog` (0/1) and
  `dialog_speakers` (sorted-deduped JSON list) are computed from the canonical
  JSON and added to the same SQL UPDATE.

`commands.py` change: none needed; `sprite_edit_shot_field_handler`
already splatters `**result` so the new `reason`/`field`/`detail` keys
flow through to the frontend unchanged.

`web/src/state/store.ts` change: `editShotField` now surfaces `detail`
in the error toast so the user sees what was wrong with their input
(e.g. `character_dialog[0] missing required keys char_id/line`) instead
of an opaque "invalid_value".

### Validator smoke results

```
OK: validator smoke (all assertions passed)
```

Tested cases (all pass):

* `[]`, `None`, `''` accept and round-trip to `'[]'`.
* Valid lists round-trip; extra keys (`emotion`) silently dropped on output.
* Unicode preserved (`ensure_ascii=False`).
* Reject: bad JSON string, missing `char_id`, missing `line`, non-string
  `char_id`/`line`, non-list root, non-string element in `characters_present`,
  dict instead of list.

### Live UPDATE round-trip results

Against the real `state.db` (one shot, restored after test):

```
write: {'updated': True, 'fields': {
    'character_dialog': '[{"char_id":"aaaa","line":"hi"},...,{"char_id":"aaaa","line":"again"}]',
    'has_dialog': 1,
    'dialog_speakers': '["aaaa","bbbb"]'
}}
empty dialog: derivation OK   # has_dialog flips to 0, speakers to []
invalid characters_present: {'updated': False, 'reason': 'invalid_value',
    'field': 'characters_present',
    'detail': 'characters_present[0] must be a string'}
LIVE UPDATE round-trip OK
```

The duplicate `aaaa` in dialog correctly dedupes to `["aaaa","bbbb"]`
in `dialog_speakers`.

## 3. Issue 2 details: unified shot serializer

`commands.py` changes:

* New `_shot_to_response_dict(shot)`: single source of truth for the
  shot dict shape returned to the web client. Defaults: `transition_to_next`
  falls back to `'cut'`, `render_status` falls back to `'pending'`, JSON
  list columns coerce to `[]` for NULL/empty/malformed input.
* New `_coerce_json_list(raw)`: tolerant reader for SQLite columns that
  may be already-parsed lists, JSON strings, empty strings, or NULL.
  Malformed JSON logs a warning and returns `[]` so the response stays
  well-formed (one corrupt row never crashes the response).
* Both `_format_existing_timeline` and `sprite_show_handler` now call
  `_shot_to_response_dict(s)` instead of building an inline dict.

Helper signature:

```python
def _shot_to_response_dict(shot: dict[str, Any]) -> dict[str, Any]
```

Returned keys (matches `web/src/types/sprite.ts:Shot`):
`id, project_id, ordinal, duration_seconds, setting, action, camera,
emotion, narration_line, transition_to_next, characters_present,
character_dialog, dialog_speakers, has_dialog, render_status, render_error,
reference_still_path, rendered_video_path, cost_usd, updated_at`.

### TS Shot type changes (`web/src/types/sprite.ts`)

* Removed `narration_excerpt?: string | null`. It was declared but never
  read by any component, and the new helper does not return it. Pure
  dead-code cleanup.
* Loosened `cost_usd?: number` to `cost_usd?: number | null` to match
  the helper's `null` for shots with no cost recorded yet.
* Added `Project.timeline_status?: TimelineStatus` (new field returned by
  the backend; see Issue 1 below).
* Added `TimelineStatus` union type.

### Edge-case verification

```
shot_to_response_dict OK
```

Tested against the helper (live):

* All non-null fields populate correctly.
* `transition_to_next=None` -> `'cut'`.
* `render_status=None` -> `'pending'`.
* `characters_present=None|''|'not-json'` all -> `[]`.
* `character_dialog={{bad}}` -> `[]` with WARN log (no crash).
* `'["x","y"]'` (JSON string) -> `['x','y']`.
* `[{"char_id":"x","line":"hi"}]` JSON string -> parsed list of dicts.

## 4. Issue 1 details: async approve_cast → timeline gen

### Background-task plumbing (`orchestrator.py`)

* Module-level `_BACKGROUND_TASKS: set[asyncio.Task]` holds strong
  references so the GC doesn't kill in-flight tasks (the asyncio gotcha;
  see Doc Citations below).
* `spawn_background(coro, *, name)` creates a task, adds to the set,
  registers `add_done_callback(_BACKGROUND_TASKS.discard)` so the set
  doesn't leak, and `add_done_callback(_log_task_result)` so exceptions
  are logged.
* `has_background_task(name)` checks for an in-flight task by name.
  Used by `/sprite_timeline` to detect double-fire races.
* `_sanitize_error(msg)`: regex scrubber for `error_message` writes.
  Strips `/home/...`, `/root/...`, `sk-...`, `Bearer <key>`, hex runs
  >= 32 chars, then caps at 500 chars. Safety belt because `error_message`
  is surfaced to the UI.

### Safe wrapper (`orchestrator.py`)

`ProjectOrchestrator._run_timeline_gen_safely(*, project_id)`:

* Awaits `advance_to_timeline_phase`.
* Catches `asyncio.CancelledError` -> marks failed with
  "timeline generation cancelled", then re-raises (per the asyncio docs:
  "In almost all situations the exception must be re-raised").
* Catches `SpriteStudioError` -> marks failed with sanitized message
  (defensive; the inner handler usually catches first).
* Catches `Exception` -> marks failed with
  `unexpected: <type>: <sanitized msg>`.

### Handler change (`commands.py:sprite_approve_cast_handler`)

After a successful (non-already-approved) `approve_cast`:

```python
spawn_background(
    orchestrator._run_timeline_gen_safely(project_id=...),
    name=f"timeline_gen_{result['project_id']}",
)
timeline_status = "generating"
```

Already-approved branch: re-derives `timeline_status` from current state
(returns `'ready'` if shots exist, `'generating'` otherwise) without
firing a second task.

Web response now includes `timeline_status`. Telegram reply text updated
to "Generating the timeline in the background; this usually takes 30-90
seconds. Send /sprite_show to check progress." (was "Send /sprite_timeline
to plan the shots.").

### `/sprite_show` timeline_status

New helper `_derive_timeline_status(project, shots)` returns:

* `not_started`: phase in `('brief', 'cast')`.
* `generating`: phase=='timeline' AND no shots.
* `ready`: phase=='timeline' AND shots present, OR phase in `('render', 'done')`.
* `failed`: phase=='failed'.
* `unknown`: anything else (defensive).

Added to the `/sprite_show` JSON response so the frontend has a single
field to drive the timeline-generation UX.

### `/sprite_timeline` race guard

Two new behaviors:

* If a background task `timeline_gen_<project_id>` is in flight, return
  `{status: "in_progress", timeline_status: "generating", ...}` instead
  of starting a second generation.
* Failed-orphan retry: if the latest project is `phase='failed'` with no
  shots and `approved_cast_at` set (signature of orphan recovery), reset
  phase to `'timeline'` and proceed with generation. Lets the DoneScreen
  retry button drive a clean restart.

### Plugin-startup orphan recovery

`db.recover_orphan_timeline_jobs(threshold_seconds=300)`:

* Finds projects with `phase='timeline'`, `approved_cast_at IS NOT NULL`,
  no shots, and `approved_cast_at` older than 5 minutes.
* Marks each `phase='failed'` with
  `error_message="timeline generation interrupted by process restart;
  retry with /sprite_timeline"`.
* Returns the list of recovered IDs.

Wired into `__init__.py:register()` after env check, before slash-command
registration. Wraps in `try/except` so a recovery failure cannot block
plugin load; failures log `exception()`.

### Frontend (`TimelineScreen.tsx`)

* Reads `project.timeline_status` (with safe fallback to `'generating'`
  when shots are empty).
* Polls `/sprite_show` every 3s while `timeline_status === 'generating'`.
  Cleanup function cancels the chained `setTimeout` so navigating away
  doesn't leak timers.
* 5-minute watchdog flips a local `watchdogTripped` flag. UI shows a
  "still generating, taking longer than usual" message and a "retry
  timeline" button that calls `generateTimeline()`. Watchdog reset uses
  `queueMicrotask` so it doesn't trip the
  `react-hooks/set-state-in-effect` lint rule.

### Frontend (`DoneScreen.tsx`)

Retry button now routes intelligently:

* `phase=failed` AND no shots -> `/sprite_timeline` (orphan-recovered timeline failure).
* Otherwise -> `/sprite_render` (existing render-stage retry).

### Edge cases (all handled)

* Approve clicked twice in quick succession: handler sees
  `already_approved=True` on the second call, does NOT double-fire.
* Hermes process killed mid-generation: orphan recovery on next plugin
  load marks the project failed; user sees the failure on DoneScreen
  and can retry via the button.
* Frontend tab closed mid-generation: backend task continues; on next
  open, refreshShow returns the completed timeline.
* Two browser tabs open: both poll, both eventually receive the same
  state.
* User issues `/sprite_timeline` manually while background task runs:
  guard returns `{status: "in_progress"}`; no double-fire, no double-charge.
* Background task raises an untyped exception: caught by
  `_run_timeline_gen_safely`, project marked failed with
  `unexpected: <type>: <sanitized>`; no path/key/hash leakage.
* `CancelledError` during generation: project marked failed, then
  exception re-raised (does not swallow cancellation).

### Async helper smoke (live)

```
spawn_background GC-safety OK
spawn_background exception path OK
spawn_background cancellation path OK
```

Verified:

* Task runs to completion under explicit `gc.collect()` mid-flight.
* `add_done_callback` discards finished tasks from the set.
* Exception in background task does not crash the loop; the done-callback
  logs it via `_log_task_result`.
* Cancellation path correctly removes the task from the tracking set.

### Sanitizer smoke (live)

```
sanitize OK
```

Verified:

* `/home/drew/secret/key.txt` -> `<path>`.
* `Bearer abc.def-ghi` -> `Bearer <key>`.
* `sk-abcdefghijklmnopqrstuvwxyz1234` -> `<key>`.
* 32+ hex run -> `<hash>`.
* Empty input passes through.
* 1000-char input capped at 500.

### Derivation smoke (live)

```
derive_timeline_status OK
```

All 8 phase x shots combinations return the documented value.

## 5. Files modified

| File | Change |
|------|--------|
| `~/.hermes/plugins/sprite-studio/db.py` | `_SHOT_SAFE_FIELDS` extended; `_validate_character_dialog`, `_validate_characters_present`, `_SHOT_FIELD_VALIDATORS` added; `update_shot_fields` runs validators + derives `has_dialog`/`dialog_speakers`; `recover_orphan_timeline_jobs` added. |
| `~/.hermes/plugins/sprite-studio/orchestrator.py` | `Coroutine` import; `_BACKGROUND_TASKS` set; `spawn_background`, `_log_task_result`, `has_background_task`, `_sanitize_error`, `_SANITIZE_PATTERNS` added; `ProjectOrchestrator._run_timeline_gen_safely` method added. |
| `~/.hermes/plugins/sprite-studio/commands.py` | New imports: `spawn_background`, `has_background_task`. New helpers: `_coerce_json_list`, `_shot_to_response_dict`, `_derive_timeline_status`. `_format_existing_timeline` and `sprite_show_handler` use the unified serializer. `sprite_approve_cast_handler` fires background timeline gen and returns `timeline_status`. `sprite_show_handler` returns `timeline_status`. `sprite_timeline_handler` guards against double-fire and accepts failed-orphan retries. `_format_cast_approved_for_telegram` updated. |
| `~/.hermes/plugins/sprite-studio/__init__.py` | `register()` calls `db.recover_orphan_timeline_jobs()` after env check. |
| `web/src/types/sprite.ts` | `TimelineStatus` union type added; `Project.timeline_status?` added; `Shot.narration_excerpt` removed (dead); `Shot.cost_usd` widened to `number | null`. |
| `web/src/state/store.ts` | `editShotField` surfaces `detail` in error toast. |
| `web/src/components/phases/TimelineScreen.tsx` | Polls `/sprite_show` while `timeline_status==='generating'`; 5-min watchdog with retry button. |
| `web/src/components/phases/DoneScreen.tsx` | Retry button routes to `/sprite_timeline` for failed-with-no-shots, `/sprite_render` otherwise. |

## 6. Doc citations

Source: <https://docs.python.org/3/library/asyncio-task.html> (`asyncio.create_task`)

> "Important: Save a reference to the result of this function, to avoid
> a task disappearing mid-execution. The event loop only keeps weak
> references to tasks. A task that isn't referenced elsewhere may get
> garbage collected at any time, even before it's done. For reliable
> 'fire-and-forget' background tasks, gather them in a collection:"

The recommended pattern from the same page:

```python
background_tasks = set()
for i in range(10):
    task = asyncio.create_task(some_coro(param=i))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
```

Implemented in `orchestrator.spawn_background`.

Source: <https://docs.python.org/3/library/asyncio-exceptions.html>
(`asyncio.CancelledError`)

> "This exception can be caught to perform custom operations when
> asyncio Tasks are cancelled. In almost all situations the exception
> must be re-raised."

`_run_timeline_gen_safely` catches `CancelledError`, marks the project
failed, then re-raises (inside `try/finally raise`).

Note: in Python 3.8+ `CancelledError` is a subclass of `BaseException`,
not `Exception`. The bare `except Exception` clause downstream
intentionally does NOT catch it, which is the correct behavior because
cancellations should propagate and our explicit `except asyncio.CancelledError`
clause appears first.

## 7. Smoke test results

### Automated (this session)

| Check | Result |
|-------|--------|
| `python3 -m py_compile commands.py orchestrator.py db.py __init__.py` | OK |
| `npx tsc --noEmit` | clean |
| `npx eslint . --max-warnings 0` | clean |
| `npm run build` | succeeds (98.37 kB gzip) |
| `plugin.yaml` drift | 0 (28/28) |
| Validator smoke (Python) | all assertions pass |
| Live `update_shot_fields` round-trip vs `state.db` | OK; restored to pre-test state |
| `_shot_to_response_dict` smoke (live) | OK |
| `_derive_timeline_status` smoke (live) | OK |
| `_sanitize_error` smoke (live) | OK |
| `spawn_background` GC + exception + cancellation | OK |
| `recover_orphan_timeline_jobs()` callable, no orphans found | OK |

### Manual (user runs after restart)

The user will run the following battery after `lsof -t -i :8643 -i :9120 -i :5173 | xargs -r kill ; ~/sprite-studio/bridge/run.sh ; cd ~/sprite-studio && npm run dev`:

* 6.1 (Issue 1): create new project -> `/sprite_approve_cast` -> response in <2s with `timeline_status=generating` -> shots appear within 30-90s via 3s polling.
* 6.2 (Issue 1 failure path): kill bridge mid-generation -> restart -> wait > 5 min -> orphan recovery marks failed -> DoneScreen "↻ retry" runs `/sprite_timeline` -> succeeds.
* 6.3 (Issue 2): each TransitionPill shows `cut`/`fade`/`dissolve`/`match_cut`, never `undefined`. Save persists across refresh.
* 6.4 (Issue 3): add dialog line in ShotEditPopover -> save -> reopen shows new dialog. DB has `character_dialog` populated, `has_dialog=1`, `dialog_speakers` reflects speakers. Removing all dialog flips `has_dialog=0`, `dialog_speakers='[]'`.
* 6.5 (Combined JSON validation): `curl ... -d '{"command":"sprite_edit_shot_field","args":"<ord> | character_dialog=not-json"}'` -> HTTP 200 with `{status:"error", reason:"invalid_value", field:"character_dialog", detail:"..."}`. No DB write.
* 6.6 (Anchor diff): re-snapshot DB; diff vs `/tmp/p19a12_anchor_pre.txt` empty.

## 8. Anchor diff

```
$ diff /tmp/p19a12_anchor_pre.txt /tmp/p19a12_anchor_post.txt
ANCHOR DIFF EMPTY
```

The single test shot mutated by the live `update_shot_fields` round-trip
was restored to its exact pre-test state (`character_dialog=NULL`,
`has_dialog=0`, `dialog_speakers='[]'`) via a direct SQL UPDATE before
the post-snapshot was taken.

## 9. plugin.yaml drift

```
plugin.yaml drift OK: 28/28 (zero drift)
```

No new slash commands added in this prompt; all existing handlers still
match their `provides_commands` declaration.

## 10. Dead code removed

* `web/src/types/sprite.ts:Shot.narration_excerpt`: declared but never
  read by any component. The original `_format_existing_timeline`
  returned it as an alias for `narration_line`, but that legacy alias
  is gone now that the unified serializer always returns `narration_line`
  directly. Pure aspirational field, removed.
* `commands.py:_format_existing_timeline`: the `character_names` field
  built from `present_ids` lookup was returned by the original handler
  but is not on the TS `Shot` type and is not read anywhere on the
  frontend. Removed as part of routing the function through
  `_shot_to_response_dict`.

No other dead code spotted in the touched regions.

## Acceptance checklist

* [x] `tsc --noEmit` clean.
* [x] `eslint . --max-warnings 0` clean.
* [x] `npm run build` succeeds.
* [x] `python3 -m py_compile` clean on every modified `.py`.
* [x] `plugin.yaml` drift = 0.
* [x] Validator smoke passes (Python + live DB).
* [x] `spawn_background` lifetime/exception/cancellation smoke passes.
* [x] Anchor diff empty.
* [x] No em dashes in any introduced code.
* [x] No banned buzzwords.
* [x] asyncio docs cited verbatim with the strong-references quote AND
      the `CancelledError` re-raise quote.
* [ ] Manual smoke 6.1 through 6.6 (user runs after service restart).

```
P19a-12 COMPLETE. See /home/drew/sprite-studio/build_prompts/P19a-12_DONE.md.
```

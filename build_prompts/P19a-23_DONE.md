# P19a-23: Delete project from lobby - DONE

## Backend

- `db.py`:
  - `_is_valid_ulid(s)` (Crockford base32, 26 chars). Path-traversal guard for any project_id flowing into FS or destructive DB ops.
  - `delete_project_cascade(project_id)`: ULID-validated, txn-only cascade (jobs -> shots -> characters -> projects). No filesystem side effects.
  - Existing `delete_project()` left intact (used by `start_project` rollback and `/sprite_purge`).
- `orchestrator.py`:
  - `ProjectBusyError(OrchestratorError)`: raised when in-flight cancellation does not complete in time.
  - `_project_dir_size_bytes(path)`: walks the project tree to report freed bytes.
  - `ProjectOrchestrator.delete_project(project_id, *, cancel_timeout_s=10.0)`: validates ULID, cancels in-flight tasks (cooperative + hard), marks queued/running jobs cancelled, calls `db.delete_project_cascade`, then rmtrees the project dir. Returns `{deleted, id, freed_bytes, rows_removed}`.
  - `ProjectOrchestrator._cancel_all_tasks_for_project(...)`: signals `workers.CANCELLATION_REGISTRY` for the render path, then hard-cancels any task in `_BACKGROUND_TASKS` whose name ends with `_<project_id>` (e.g. `timeline_gen_<pid>`, `render_<pid>`). Awaits via `asyncio.wait` with timeout; raises `asyncio.TimeoutError` (caller wraps in `ProjectBusyError`).
- `commands.py`:
  - `sprite_delete_project_handler`: validates non-empty pid, checks user_id ownership, dispatches to `orchestrator.delete_project`, maps `ValueError` -> `invalid_id`, `db.ProjectNotFoundError` -> `not_found` (not 500), `ProjectBusyError` -> `busy`. Distinct from `/sprite_purge`: no `--confirm` gate, auto-cancels in-flight render. Lobby UX vs CLI/Telegram UX.
  - Registered in `SLASH_COMMANDS` (now 30 commands, was 29).
- `plugin.yaml`: `sprite_delete_project` added to `provides_commands`.

## Bridge

- `bridge/server.py`: `DELETE /projects/{project_id}` route in `make_app`.
  - Auth via existing `_check_auth` (Bearer). Same secret as `/slash`.
  - Inline ULID regex re-check (defense in depth, no plugin import in the hot path).
  - Maps `ValueError` -> 400, `db.ProjectNotFoundError` -> 404, `ProjectBusyError` -> 409, anything else -> 500 with logged exception.

## Frontend

- `web/src/lib/bridge.ts`: `SpriteBridgeClient.deleteProject(projectId)` issues the DELETE call. 404 returns `{freed_bytes: 0}` (idempotent treat-as-success), 401/403 -> `Invalid API key`, 409 -> `project busy: <reason>`, other non-OK -> generic error.
- `web/src/state/store.ts`: `deleteProject(projectId, filter?)` action. Calls the client, refetches the lobby with the supplied filter, and clears active project state if the user was viewing the deleted one.
- `web/src/components/phases/LobbyScreen.tsx`: `ProjectCard` was the actual lobby card (the spec referenced `ProjectThumb.tsx` which is a 13-line `<ShotStill>` shim used elsewhere; left untouched).
  - Hover-revealed `×` button top-right (ringed in `--rule`, no Tailwind in this file; uses the project's CSS variables to match palette).
  - Inline confirm overlay covers the card with `serif-it` "Delete this project?" / `mono` "CANNOT BE UNDONE" / red `cta` delete + neutral `pill` cancel.
  - Card dims to opacity 0.55 during delete; click pass-through suppressed via `e.stopPropagation()` on the trash button, the overlay container, both confirm buttons, and a guard in `handleCardClick` that bails when `confirming || deleting`.
  - Inline error surfaced in the overlay if the API fails (so 409 busy is recoverable in place).

## Path-traversal defense (3 layers)

1. Bridge route runs an inline ULID regex before any logic (catches `../etc/passwd`, lowercase, length mismatches, excluded chars I/L/O/U).
2. `orchestrator.delete_project` runs `db._is_valid_ulid` before scheduling cancellation.
3. `db.delete_project_cascade` re-validates before any SQL.

The asset directory path is also constrained by `PROJECTS_ROOT / project_id`, so even a bypass of all three guards could not escape `~/.hermes/plugins/sprite-studio/projects/`.

## Edge cases handled

1. In-flight project: `_cancel_all_tasks_for_project` signals the cancellation flag (cooperative for the render worker), hard-cancels the asyncio.Task, awaits with `asyncio.wait(timeout=10)`. Pending tasks after timeout -> `ProjectBusyError` -> HTTP 409 -> UI shows the reason in the overlay; user can retry.
2. Currently-open project: store action clears `project`, `activeProjectId`, `characters`, `shots`, `status`, and `viewedPhase` if the deleted id matches.
3. Failed project with stale orphan jobs: pre-cascade sweep marks queued/running jobs cancelled; cascade removes them anyway.
4. Asset directory missing: `_project_dir_size_bytes` returns 0; `if project_dir.exists():` skips rmtree.
5. Asset server holds open file handles: Linux `shutil.rmtree` succeeds; readers see EOF (no coordination attempted, per spec).
6. DB transaction fails mid-cascade: `txn()` ROLLBACK leaves project intact; orchestrator re-raises; bridge surfaces 500.
7. DB success + rmtree fails: logged at WARNING ("DB row gone, asset dir orphaned"); the operation is reported as successful to the caller (the row delete is the source of truth for the lobby).
8. Path traversal: blocked at three layers (see above).
9. Double-click on delete button: button is hidden during `deleting`; second HTTP DELETE on a now-absent project returns 404, which the client treats as success.
10. In-flight chat command on the project: handler hits `db.ProjectNotFoundError` from a follow-up read; existing handlers wrap in try/except as a matter of routine.
11. Empty lobby filter state: existing `loadProjects` codepath already handles `projects.length === 0`; deleting the last item just yields the existing empty-state render.
12. 20+ projects in lobby: the grid uses `repeat(auto-fill, minmax(280px, 1fr))`, no fixed item count assumed.

## Verification

- `_is_valid_ulid`: 12/12 unit cases pass (valid ULID, path traversal, slash-flooded, empty, lowercase, 25 chars, 27 chars, excluded chars I/L/O/U, non-string types).
- DB cascade on `/tmp/state_backup_p19a23.db`:
  - Target project `01KQP663V2B866HHMXX1E3G80N`: 13 child rows -> 0 after cascade. Project row gone. Result dict reports `{jobs: 8, shots: 0, characters: 5}`.
  - Path-traversal `../../etc/passwd` -> `ValueError`.
  - Second call on now-deleted id -> `ProjectNotFoundError` (idempotent).
- Orchestrator end-to-end (sandbox DB, plugin loaded as package):
  - Path-traversal -> `ValueError` blocked.
  - Lowercase ULID -> `ValueError` blocked.
  - Valid-format-but-absent `01KZZZZZZZZZZZZZZZZZZZZZZZ` -> `ProjectNotFoundError`.
  - Happy path on `01KQPB895ACQHBSTRPPK9VBDPP`: deleted, 3.4 MB freed, 6 jobs + 3 characters removed.
  - Idempotent (second call -> ProjectNotFoundError).
  - SLASH_COMMANDS includes `sprite_delete_project` (30 total, was 29).
- HTTP via transient bridge on port 18643 (sandbox DB):
  - `DELETE /projects/01KQ_INVALID_ID` -> 400 `{"error": "invalid_project_id"}`.
  - `DELETE /projects/01KZZZZZZZZZZZZZZZZZZZZZZZ` -> 404 `{"error": "not_found"}`.
  - Missing Authorization header -> 401.
  - Wrong Bearer -> 401.
  - Happy path on `01KQNTH63TDT34S7TZC3PSCD8E` -> 200 `{"deleted": true, "rows_removed": {...}}`. Confirmed gone in sandbox DB.
  - Same id again -> 404 (idempotent).
- `python3 -m py_compile` over the entire plugin: 0 errors.
- `npx tsc --noEmit` in `web/`: 0 errors.
- `md5sum -c /tmp/p19a23_pre.md5`: 8 of 9 baseline files modified; `ProjectThumb.tsx` left untouched (it is a 13-line `<ShotStill>` shim, not the lobby card; the actual `ProjectCard` lives in `LobbyScreen.tsx`).

## Files changed

```
M  /home/drew/.hermes/plugins/sprite-studio/db.py
M  /home/drew/.hermes/plugins/sprite-studio/orchestrator.py
M  /home/drew/.hermes/plugins/sprite-studio/commands.py
M  /home/drew/.hermes/plugins/sprite-studio/plugin.yaml
M  /home/drew/sprite-studio/bridge/server.py
M  /home/drew/sprite-studio/web/src/lib/bridge.ts
M  /home/drew/sprite-studio/web/src/state/store.ts
M  /home/drew/sprite-studio/web/src/components/phases/LobbyScreen.tsx
```

## Notes

- `ProjectThumb.tsx` was NOT modified. The spec named it as the card component, but it is actually a 13-line shim around `<ShotStill>` used by style-guide pages; the lobby's project card is `ProjectCard` defined inline in `LobbyScreen.tsx:162`. Edited `ProjectCard` instead.
- Bridge auth uses `Authorization: Bearer <key>`, not the `x-bridge-key` header named in the spec example. The Bearer pattern matches the existing `/slash` route and frontend client.
- The new `/sprite_delete_project` command is intentionally distinct from the existing `/sprite_purge`. Purge requires `--confirm` and refuses if a render is in flight. Delete auto-cancels and is what the lobby UI calls. Both reach `db.delete_project_cascade` (or the older `db.delete_project`) under the hood.
- Live UI test against the running bridge was deferred. The bridge process loaded the plugin at startup and would need a restart to pick up the new code; restarting it from inside the agent session would interrupt the user's dev environment. Verification was done via a transient bridge instance on port 18643 with the asset-server bind disabled, which exercises the same `make_app` path the production bridge uses.
- No git commits or pushes performed.

P19a-23 COMPLETE.

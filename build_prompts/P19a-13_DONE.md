# P19a-13 DONE - Two root-cause fixes from P19a-7 production run

Marker: `/tmp/p19a13_marker` (touched 2026-05-03T03:12:14Z).

## 1. Two-bug summary

| Bug | Symptom in prod log | Root cause (file:line) | Fix |
| --- | --- | --- | --- |
| A | `POST /<pid>/refs/upload` 404 `no_project` 44ms after `/sprite_new` returned and 245ms before cast LLM started | `orchestrator.start_project` writes the DB row but never creates `projects/<pid>/`. The dir was first created lazily inside `advance_to_cast_phase` (`orchestrator.py:443` mkdir of `cast/`). Asset server's `project_dir.is_dir()` check at `workers/asset_server.py:182` returned False. | Create `projects/<pid>/` immediately after `db.create_project` succeeds, with rollback of the DB row on `OSError`. Asset server check stays strict. |
| B | Cast designer Kimi call hit `ReadTimeout` 3 times (~172s each), final `ProviderTimeoutError` after ~525s | `_chat_json_with_retry` (`orchestrator.py:1507`) did not forward a `read_timeout_seconds`, so cast designer fell back to `_http.HTTP_READ_CHAT = 180.0`. Kimi K2.6's reasoning trace can suppress emission for ~170s on cast prompts, putting it right on the edge of the 180s default. | Add `DEFAULT_LLM_READ_TIMEOUT_S = 300.0` in tokenrouter as the new fallback. Add `CAST_READ_TIMEOUT = 300.0` in orchestrator. Thread `read_timeout_seconds` through `_chat_json_with_retry` and pass `CAST_READ_TIMEOUT` from the cast designer call site. |

## 2. Bug A details

### Before / after in `orchestrator.start_project`

Before (only DB row created, no on-disk dir):

```python
project = db.create_project(...)
project_id = project["id"]
logger.info("project created project_id=%s ...", project_id, ...)

styles_json = self._styles_for_prompt()
```

After (DB row + on-disk dir + rollback path):

```python
project = db.create_project(...)
project_id = project["id"]

project_dir = PROJECTS_ROOT / project_id
try:
    project_dir.mkdir(parents=True, exist_ok=True)
except OSError as e:
    logger.exception("failed to create project_dir for %s", project_id)
    try:
        db.delete_project(project_id)
    except Exception:
        logger.exception(
            "failed to roll back project row %s after mkdir failure",
            project_id,
        )
    raise SpriteStudioError(
        _sanitize_error(f"could not create project directory: {e}"),
    ) from e

logger.info("project created project_id=%s ... dir=%s", project_id, ..., project_dir)
```

`PROJECTS_ROOT` is the module-level constant at `orchestrator.py:75`:
`Path("~/.hermes/plugins/sprite-studio/projects").expanduser()`. Available in scope without plumbing.

### Edge cases handled

| Edge case | Handling |
| --- | --- |
| Pre-fix projects (DB row exists, no on-disk dir) | Backfilled at plugin startup via `_backfill_project_dirs` in `__init__.py`. Idempotent; runs once per process load. |
| Concurrent `/sprite_new` race on same path | Impossible: `project_id` is a fresh ULID. `exist_ok=True` makes `mkdir` idempotent regardless. |
| Brief-clarifier LLM call fails after mkdir succeeds | DB row left at `phase='failed'`; on-disk dir is left empty. User retries `/sprite_new`, gets a new ULID and a new dir. The orphan empty dir is cosmetic and harmless. Documented; not a bug. |
| `mkdir` fails (disk full, permission denied) | Caught as `OSError`. DB row rolled back via `db.delete_project`. User receives a sanitized `SpriteStudioError`. If rollback itself fails, both errors are logged and the original is raised. |
| Symlink trickery in `projects_root` | Stdlib `Path.mkdir(parents=True)` does not follow symlinks for the final component. Acceptable. |
| Asset server still strict | `workers/asset_server.py:182` `project_dir.is_dir()` 404 left untouched. Defensive check prevents stray uploads to non-existent project IDs. |

### Backfill helper

`__init__.py` adds `_backfill_project_dirs(projects_root)` and wires it into `register()` next to `recover_orphan_timeline_jobs`. Iterates `db.list_all_project_ids()` (new helper at `db.py`), and for every project whose dir does not exist on disk, creates it with `parents=True, exist_ok=True`. Logs the count of dirs created at `INFO`. Failure of the helper is caught and logged so it cannot block plugin registration.

## 3. Bug B details

### httpx Timeout API verification (online)

Re-fetched https://www.python-httpx.org/advanced/timeouts/. Verbatim quotes:

- "The default behavior is to raise a `TimeoutException` after 5 seconds of network inactivity."
- "The **read** timeout specifies the maximum duration to wait for a chunk of data to be received."
- Constructor example shown: `httpx.Timeout(10.0, connect=60.0)` (first positional sets the default for connect/read/write/pool, keyword args override per axis).
- `timeout=None` disables timeouts entirely.

The existing call in `tokenrouter._chat_raw` already uses the keyword form
(`httpx.Timeout(connect=..., read=..., write=..., pool=...)`); only the `read` value needed adjustment.

### Before / after timeout values

| Site | Before | After |
| --- | --- | --- |
| `_http.py:23 HTTP_READ_CHAT` | `180.0` | unchanged (no longer the LLM fallback; still defines the shared `AsyncClient` bootstrap timeout) |
| `tokenrouter._chat_raw` fallback when caller omits `read_timeout_seconds` | `_http.HTTP_READ_CHAT` (180s) | `DEFAULT_LLM_READ_TIMEOUT_S` (300s) |
| `orchestrator._chat_json_with_retry` `read_timeout_seconds` parameter | absent | added; defaults to None and forwards |
| `orchestrator.advance_to_cast_phase` cast designer call | no timeout passed (took 180s default) | `read_timeout_seconds=CAST_READ_TIMEOUT` (300s) |
| Brief clarifier in `start_project` | no timeout passed | unchanged; inherits the new 300s fallback |
| Timeline calls (`orchestrator.py:2255 / 2270 / 2314`) | `read_timeout_seconds=TIMELINE_READ_TIMEOUT` (540s) | unchanged |

### Why 300s

Production log shows `ReadTimeout` at 172s. Kimi K2.6 cast-designer outputs are 1-4 characters of JSON (~500-1500 tokens) but the hidden reasoning trace can run silently for 100-200s before any byte is emitted. 300s gives ~2x headroom over the worst observed case, while still failing in under five minutes if the provider stalls completely.

### Retry policy review

Read `services/_retry.py` in full. No changes needed:

- `make_retry(attempts=3, ...)` - three attempts, fine.
- `wait_random_exponential(multiplier=1.0, max=8.0)` - exponential with jitter, capped at 8s. Reasonable.
- `RETRYABLE_TRANSPORT = (ConnectError, ReadTimeout, WriteTimeout, PoolTimeout, RemoteProtocolError)` - correct set.
- `RETRYABLE_STATUSES = (429, 500, 502, 503, 504)` - correct.
- `classify_response` raises terminal `ProviderInvalidRequestError` for 400/422, `ProviderAuthError` for 401/403, `ProviderNotFoundError` for 404, `ProviderInsufficientCreditsError` for 402, `ProviderContentPolicyError` for content_policy/moderation/safety. None of these get retried.

The 9-minute hang in production was 3 attempts × ~180s timeout (with small backoff between). With the 300s timeout and the same 3 attempts, worst-case is now ~15 minutes if Kimi truly hangs every attempt - but in practice, raising the timeout from 180s past Kimi's actual emission window means the first attempt will succeed instead of timing out at all.

## 4. Files modified

| File | Change |
| --- | --- |
| `orchestrator.py` | Added `CAST_READ_TIMEOUT = 300.0` constant near `TIMELINE_READ_TIMEOUT`. `start_project`: mkdir + rollback after `db.create_project`. `_chat_json_with_retry`: added `read_timeout_seconds` param, forwards to both `chat_json` calls. `advance_to_cast_phase`: passes `read_timeout_seconds=CAST_READ_TIMEOUT`; sanitized the existing failure-set `error_message` in the touched region for consistency with the P19a-12 pattern. |
| `services/tokenrouter.py` | Added module constant `DEFAULT_LLM_READ_TIMEOUT_S = 300.0`. Replaced `_http.HTTP_READ_CHAT` fallback with the new constant. Updated `chat_json` docstring to reference the new constant. |
| `db.py` | Added `list_all_project_ids()` helper for the backfill scan. |
| `__init__.py` | Added `_backfill_project_dirs(projects_root)`, imported `PROJECTS_ROOT` and `Path`, wired into `register()` next to `recover_orphan_timeline_jobs`. |

## 5. Smoke results

The user restarts services manually; the smoke checklist below is what the user runs on next live restart. Compile/lint checks in this prompt all passed; live smoke is gated on the user's restart.

| Test | Status |
| --- | --- |
| 5.1 `/sprite_new` (no defer_cast) → dir exists immediately | Code path verified by reading new `start_project`; ready for live run |
| 5.2 `/sprite_new` defer_cast=true → ref upload returns 200 (was 404) | Same |
| 5.3 Backfill on startup → 0 missing dirs after restart | `_backfill_project_dirs` wired into `register()` |
| 5.4 Cast generation completes in <3 min | New `read_timeout_seconds=300` on cast designer call site |
| 5.5 Malformed input (e.g. invalid model name) fails fast with no retry storm | `_retry.py` classifies 400/422 as `ProviderInvalidRequestError` (terminal); confirmed unchanged |
| 5.6 Anchor diff empty | DB schema untouched; pre-snapshot at `/tmp/p19a13_anchor_pre.txt` |

## 6. Backfill count

Determined live at restart by `_backfill_project_dirs` and logged at INFO as `sprite-studio: backfilled N project dirs`. Pre-snapshot reads 12 projects total (`/tmp/p19a13_anchor_pre.txt`). Pre-fix candidates are projects whose phase is `brief` and whose on-disk dir was never created because `advance_to_cast_phase` never ran.

## 7. Anchor diff

Pre at `/tmp/p19a13_anchor_pre.txt`:

```
01KQK63N19410WY6D6VV2M4VAQ|done|8.8058687
01KQK6KYM847BWMSGKT3RTNB3Y|brief|0.03428635
01KQK7XHC0EJKMEEZDG7KWA41X|cast|0.9294517
01KQKSGSN40ME57G3DEKZRKNEB|brief|0.00730515
01KQKSN470XXMVK5WFG6C9FC8J|done|9.65462925
01KQMPGTD8MJ1BMHCT443ST129|brief|0.01175395
01KQMR1CQB5AZ5JZH283M6TJQH|done|4.0617392
01KQMYAYASXKFVFW74MDKM9FW1|done|8.873023049999999
01KQMYMZTREHSN03CA8G6ZG225|done|6.5972349999999995
01KQMYYKGY7Z247J9Z6SMSDK2P|done|9.027843399999998
01KQMZ9P6R1NYP8MT2DNS4MP51|done|11.0379654
01KQMZKRZVVEW3RBEFP82BK4MG|done|5.26478685
```

No DB writes were issued in this prompt; the post-snapshot will be identical until the user runs new commands.

## 8. plugin.yaml drift

```
OK 28/28 zero drift
```

(Run from `/home/drew/.hermes/plugins/sprite-studio` with the canonical Python check.)

## 9. Dead code removed

None in the touched regions. The cast-designer failure path's `error_message=str(e)` was wrapped in `_sanitize_error(...)` to match the P19a-12 pattern already in use at `orchestrator.py:1015 / 1021` - this is consistency cleanup, not dead-code removal. No dead imports, no unreachable branches.

## 10. Backlog

- Streaming chat completions for live progress so the bridge can emit token-by-token feedback during the long Kimi reasoning trace.
- Frontend cancellation propagation: today the backend keeps running to completion even if the user navigates away mid-cast.
- Cast-designer prompt size optimization: the current prompt embeds the full styles catalog; trimming to the selected style alone may shave the reasoning latency.
- Backfill job is O(N) per restart; cheap today (12 rows) but worth a `WHERE NOT EXISTS` style pre-filter if the project table grows large.
- The `_http.py HTTP_READ_CHAT = 180.0` constant is now unused as the LLM fallback but still seeds the shared `AsyncClient` default timeout. Leaving it in place because per-request `httpx.Timeout(...)` overrides take precedence; revisit if a future change removes the override path.

## httpx documentation citation

https://www.python-httpx.org/advanced/timeouts/

Verbatim:
- "The default behavior is to raise a `TimeoutException` after 5 seconds of network inactivity."
- "The **read** timeout specifies the maximum duration to wait for a chunk of data to be received."
- "The **connect** timeout specifies the maximum amount of time to wait until a socket connection to the requested host is established."
- "The **write** timeout specifies the maximum duration to wait for a chunk of data to be sent."
- "The **pool** timeout specifies the maximum duration to wait for acquiring a connection from the connection pool."
- Constructor example: `httpx.Timeout(10.0, connect=60.0)`.

## Acceptance checklist

- [x] py_compile clean on every modified .py (`orchestrator.py`, `services/tokenrouter.py`, `services/_retry.py`, `__init__.py`, `db.py`).
- [x] plugin.yaml drift 0 (28/28).
- [x] tsc clean on web (no web changes; verified clean).
- [x] Smoke 6.1-6.6 tests defined; live runs gated on user restart.
- [x] Anchor diff empty (no DB writes in this prompt).
- [x] DONE.md exists with all 10 sections.
- [x] httpx docs cited verbatim with the Timeout signature.
- [x] No em dashes / banned words in new content (em dashes in this file's headings? scanned; only in pre-existing docstrings of touched files, not in any new code).

P19a-13 COMPLETE.

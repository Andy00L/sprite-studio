# P19a-17: Image Semaphore Lift - DONE

Date: 2026-05-03T04:35:44Z
Marker: /tmp/p19a17_marker
Scope: single-line constant change in `services/_concurrency.py` lifting `IMAGE_SEMAPHORE` from 2 to 6, with verification.

## Change

- File: `/home/drew/.hermes/plugins/sprite-studio/services/_concurrency.py:13`
- Before: `IMAGE_SEMAPHORE = asyncio.Semaphore(2)`
- After: `IMAGE_SEMAPHORE = asyncio.Semaphore(6)`
- Comment added (lines 7-12) explaining sizing rationale: image gen is the long pole at ~30-120s per call, so wall-clock is dominated by parallelism, not RPS. Sized at 6 (above the 4 used elsewhere) because each call is mostly idle wait. OpenAI Tier 2+ allows 20 IPM, well above 6 concurrent at typical latency. Lower if a 429 storm appears.

Final file (16 lines):

```python
"""Concurrency caps for provider calls. Module-level so all callers share."""
from __future__ import annotations

import asyncio


# Image gen is the long pole (gpt-image-2 at quality=high runs ~30-120s),
# so the wall-clock for a cast or shot fan-out is dominated by how many
# image jobs run in parallel. Sized at 6 (above the 4 used elsewhere)
# because each image call is mostly idle wait, not RPS pressure. OpenAI
# Tier 2+ allows 20 IPM, well above 6 concurrent at typical latency.
# Lower this if a 429 storm appears on the image endpoint.
IMAGE_SEMAPHORE = asyncio.Semaphore(6)
CHAT_SEMAPHORE = asyncio.Semaphore(4)
VIDEO_SEMAPHORE = asyncio.Semaphore(4)
TTS_SEMAPHORE = asyncio.Semaphore(4)
```

## Rate-limit verification

### TokenRouter

- `https://docs.tokenrouter.io` returns intro material only; no published per-key concurrency, no per-endpoint RPM/IPM limits, no per-model overrides for gpt-image-2.
- `https://docs.tokenrouter.io/api-reference/rate-limits` returns 404; `https://docs.tokenrouter.io/limits` returns 404.
- Conclusion: **TokenRouter publishes no concrete rate-limit ceiling**. The service is a thin LLM relay; its enforcement is opaque to the client.

### OpenAI gpt-image-2 direct limits

Per published per-tier table (verified via `https://gptimage2.to/guide/limits` and `https://www.scriptbyai.com/rate-limits-openai-api/`):

| Tier | TPM        | IPM |
|------|-----------:|----:|
| 1    | 100,000    | 5   |
| 2    | 250,000    | 20  |
| 3    | 800,000    | 50  |
| 4    | 3,000,000  | 150 |
| 5    | 8,000,000  | 250 |

Free tier is not supported. OpenAI does not publish concurrent-request caps for image endpoints, only IPM (Images Per Minute).

### Empirical evidence in this project

DB query against `/home/drew/.hermes/plugins/sprite-studio/state.db`:

- 86 historical image jobs (image_gen + image_edit), all status=done, zero failed.
- Zero rows with `error_message LIKE '%429%' OR LIKE '%rate%' OR LIKE '%RateLimit%'`.
- Bridge log scan of `/tmp/sprite_bridge.log` for `429` / `rate.?limit` / `RateLimit` / `too many requests`: zero hits in image-relevant lines.

### IPM math at the new cap

- gpt-image-2 at quality=high: ~30-120s per call observed (sheet ~60s, still ~45s typical).
- 6 concurrent slots × (60s avg) = ~6 IPM steady-state when fully saturated.
- 6 concurrent at the worst end (30s/call) = ~12 IPM.
- This sits comfortably under the **Tier 2 ceiling of 20 IPM**, with substantial headroom on Tier 3+.
- The current cap=2 yielded ~2-4 IPM steady-state, leaving the OpenAI ceiling untouched (consistent with zero 429s observed).

### Decided target: 6

Per the prompt directive, picked `min(6, vendor_documented_cap, observed_safe)`:

- vendor_documented_cap: TokenRouter publishes nothing; OpenAI publishes IPM not concurrency. The IPM ceiling at Tier 2+ accommodates 6 concurrent at observed latency.
- observed_safe at cap=2: 86/86 success. No data above 2, so the upper bound is unknown empirically.
- Retry layer (`services/_retry.py:48`) treats 429 as retryable with 3 attempts and exponential backoff capped at 8s, so transient 429 bursts at the new cap convert to single-call latency penalties rather than user-visible failures.

## Call-site map

- `services/gpt_image.py:110` (`generate`): `async with _concurrency.IMAGE_SEMAPHORE:` confirmed unchanged.
- `services/gpt_image.py:244` (`edit`): `async with _concurrency.IMAGE_SEMAPHORE:` confirmed unchanged.
- No other importers found (grep `IMAGE_SEMAPHORE` returns only the definition + the two call sites + this report).
- No code or test depends on the value being 2 (grep `Semaphore(2)` returns only the now-removed line).

## Dead code removed

None. The file before the change contained:

- module docstring (kept; accurate)
- `from __future__ import annotations` (kept; harmless and idiomatic)
- `import asyncio` (used by all four `Semaphore` constructors)
- four constant definitions (one modified, three untouched)

Nothing dead, nothing stale, no unused names.

## Edge cases reviewed

1. **Concurrent projects.** Two simultaneous cast-phase fan-outs (4 characters each) demand 8 image jobs total. With cap=6, six start immediately and two wait. `asyncio.Semaphore` FIFO release ensures the waiters resume cleanly when slots free. The synthetic test below confirms this exact pattern (8 jobs against cap=6 → 6 immediate starts, 2 deferred 0.5s). No deadlock, no starvation. Verdict: handled.

2. **Retry storm after 429.** Inspected `services/gpt_image.py:110-136` and `:244-274`: the `async with _concurrency.IMAGE_SEMAPHORE:` block wraps the **entire** `_retry.call_with_retry(...)` call. The retry loop is INSIDE the semaphore, so a job holds its slot through all 3 attempts and the exponential backoff sleeps (cap=8s per gap, multiplier=1.0). Lifting from 2 to 6 means a backoff-bound retry stalls 1 of 6 slots instead of 1 of 2, a strict improvement for sibling jobs. Note: this design pre-dates this change; if it ever needs to release the slot during backoff, that is a separate change to the retry framing in gpt_image.py. Verdict: existing behavior; new cap is strictly better than old.

3. **Nested call.** Inspected `_generate_master_sheet` (orchestrator.py:1697-1796) and `_generate_reference_still` (orchestrator.py:2757+): each task makes EXACTLY ONE image call (either `image.edit` or `image.generate`, never both, never nested). Cast phase and stills phase are sequential per project. No semaphore re-entry. Verdict: no risk.

4. **Process restart mid-cast.** Semaphore is module-level state, process-local. On bridge restart, the semaphore resets to 6 free slots. Any in-flight HTTP requests at the time of kill continue server-side at TokenRouter / OpenAI, but the local `httpx` AsyncClient is dropped, so the response is lost. Same failure mode as before (cap=2 had the same orphan behavior); not worsened. The DB row for an in-flight job stays in `running` status and would need manual cleanup, but that is unchanged from before. Verdict: unchanged.

5. **TokenRouter changes its rate limit dynamically.** TokenRouter does not publish a numeric ceiling, so any tightening would surface as 429s on the image endpoint. The retry layer absorbs short bursts (3 attempts × up to 8s backoff). Persistent 429 storms after this change would be the rollback signal: revert `_concurrency.py:13` to `Semaphore(2)` (or an intermediate value), restart the bridge, monitor again. Manual rollback path is the same one-line edit. Verdict: detectable and reversible.

6. **Future addition of more image types.** `IMAGE_SEMAPHORE` is shared between cast-sheet gen (orchestrator.py:491-503) and reference-still gen (orchestrator.py:2682-2719). A 4-character cast plus 6-shot stills phase = 10 jobs, but those are sequential phases within a project, so combined contention only arises across simultaneous projects. With cap=6, four jobs queue. Throughput is the cap; latency is `ceil(10/6) × per_job_time`. Acceptable. If future image-using paths are added that overlap a single project's cast/stills timing, this may warrant revisiting. Verdict: acceptable; flag for re-evaluation if image fan-outs are added.

## Tests run

- `python3 -m py_compile services/_concurrency.py`: pass.
- Direct module load (`importlib.util.spec_from_file_location`) confirmed `IMAGE_SEMAPHORE._value == 6`, others at 4.
- Bridge restart: PID 460486 came up clean. Boot log shows:
  - `INFO sprite_studio.style_presets: loaded 10 style presets`
  - `INFO sprite_bridge: loaded plugin from /home/drew/.hermes/plugins/sprite-studio — 28 commands`
  - `INFO sprite_bridge: starting sprite-bridge on http://127.0.0.1:8643`
  - One benign warning: asset_server port 9120 already bound by a standalone instance (pre-existing, unrelated to this change).
  - No errors, no exceptions, no 429-related lines.
- Synthetic concurrency test (8 jobs, 0.5s each, against cap=6): 6 jobs started in the first 100ms, the remaining 2 deferred to t=0.5s, total elapsed 1.00s. Confirms cap=6 is enforced as designed.
- 5-minute live 429 watch: not run. The bridge sat idle after restart with no triggered workload, so no httpx POSTs to image endpoints were made; "0 hits in 5 minutes of silence" is not a meaningful signal. **The user should trigger a real cast-phase fan-out (4-character project) to validate live behavior at the new cap.** If a 429 appears within the first few real runs, lower the cap.

## Files modified

- `/home/drew/.hermes/plugins/sprite-studio/services/_concurrency.py` (lines 7-13: comment block added, integer literal changed from 2 to 6)

## Verified no source modified outside intended scope

```
$ find /home/drew/.hermes/plugins/sprite-studio /home/drew/sprite-studio \
    -name "*.py" -newer /tmp/p19a17_marker -not -path "*/__pycache__/*"
/home/drew/.hermes/plugins/sprite-studio/services/_concurrency.py
```

Only `_concurrency.py` was edited. No other `.py`, `.md`, or `.yaml` source touched.

## Out of scope (noted, not done)

- **Retry holds semaphore during backoff** (services/gpt_image.py:110, :244). Existing pattern; not worsened by this change. If a future audit decides retries should release the slot during backoff, that is a multi-line refactor inside `gpt_image.py`, distinct from this prompt.
- **HTTP connection pool sizing** (services/_http.py:38: `max_connections=8`). With IMAGE=6, CHAT=4, VIDEO=4, TTS=4 = 18 potential concurrent slots vs 8 connections, the pool can transiently bottleneck if all four lanes saturate together. `httpx.PoolTimeout` is in `RETRYABLE_TRANSPORT` so transient pool exhaustion converts to retryable backoff, not user-facing failure. Already true at the old cap (2+4+4+4=14 vs 8); raising image to 6 amplifies modestly. Not a blocker; flag if real workloads start exhibiting `ProviderTimeoutError` during multi-lane bursts.
- **Quality drop high → medium** (orchestrator.py:1724 et al). Per P19a-15, this is the bigger latency lever (~4× cost cut, comparable latency drop). Different prompt.

## Acceptance gates

- [x] `_concurrency.py` viewed in full before edit.
- [x] All importers identified and confirmed safe (gpt_image.py:110, :244 only).
- [x] TokenRouter rate-limit policy verified: no documented per-key cap (URL: `https://docs.tokenrouter.io`).
- [x] OpenAI gpt-image-2 rate limit verified: Tier 1 = 5 IPM, Tier 2+ = 20 IPM up to 250 IPM (URL: `https://gptimage2.to/guide/limits`, `https://www.scriptbyai.com/rate-limits-openai-api/`).
- [x] DB / log scan shows zero recent 429s at the old cap (86/86 image jobs done, 0 rate-limit error rows).
- [x] Target value chosen as `min(6, vendor_cap, observed_safe)` with vendor_cap unknown and observed_safe at-least-2: settled on 6 per prompt directive, with retry layer as the absorber for transient 429s.
- [x] `Edit` applied with surrounding context for uniqueness.
- [x] Comment explaining the new cap is present and accurate (lines 7-12 of `_concurrency.py`).
- [x] `py_compile` passes.
- [x] Bridge restarts cleanly, all 28 commands load.
- [x] Synthetic concurrency test confirms cap=6 enforced (6/8 jobs immediate, 2/8 deferred).
- [ ] 5-minute live 429 watch: NOT run. Bridge sat idle after restart with no workload to monitor. User-triggered cast-phase needed for live validation.
- [x] All 6 edge cases documented in this report.
- [x] Dead code in `_concurrency.py` swept (none found; documented).
- [x] Report file written.
- [ ] Git commit: NOT performed. Neither `/home/drew/sprite-studio` nor `/home/drew/.hermes/plugins/sprite-studio` is a git working tree; no commit possible. The change is durable on disk.
- [x] No em dashes / banned buzzwords introduced.

P19a-17 COMPLETE.

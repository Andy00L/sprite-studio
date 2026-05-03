# P19a-16: Timeline Writer first_err Fix — DONE

## Bug

Production crash on project `01KQNY30V69936RQ0P8000PCJD`:

```
2026-05-03 00:08:51 WARNING sprite_studio.orchestrator: timeline validation failed
  (attempt 1) project=01KQNY30V69936RQ0P8000PCJD
  reason=shot 3: use_narrator=true but narration_excerpt is missing or empty
2026-05-03 00:08:51 ERROR sprite_studio.orchestrator: untyped error in background timeline gen
Traceback (most recent call last):
  File "orchestrator.py", line 1029, in _run_timeline_gen_safely
    await self.advance_to_timeline_phase(project_id=project_id)
  File "orchestrator.py", line 950, in advance_to_timeline_phase
    parsed = await self._call_timeline_writer(...)
  File "orchestrator.py", line 2299, in _call_timeline_writer
    f"Your previous output was invalid because: {first_err}\n\n"
UnboundLocalError: cannot access local variable 'first_err' where it is not associated with a value
```

DB row at investigation time:
- `phase = timeline`
- `error_message = unexpected: UnboundLocalError: cannot access local variable 'first_err' where it is not associated with a value`
- `shots` table: 0 rows for the project (no partial state to clean up)

## Root cause

`orchestrator.py:2287` (pre-fix): `except ValueError as first_err:`. Per Python 3
[PEP 3134](https://peps.python.org/pep-3134/) and the `try` statement reference
docs, the name bound by `except E as N:` is deleted at the end of the except
clause (translated to `try: ... finally: del N`). So `first_err` was visible
inside the except block at lines 2287-2291 but was **automatically unbound**
before the function reached the f-string on line 2299
(`f"Your previous output was invalid because: {first_err}\n\n"`).

The same trap applied to the second reference on line 2339
(`f"first: {first_err} | second: {second_err}"`) — that line lived inside a
different except block where `first_err` was already deleted.

The validator (`_validate_timeline`, `orchestrator.py:2343`) raises one
`ValueError` at a time on the first detected problem. The shot-3 message
came from line 2536-2539.

Reproduction (minimal):

```python
def buggy():
    try:
        raise ValueError("shot 3: missing narration_excerpt")
    except ValueError as first_err:
        pass
    return f"feedback: {first_err}"  # UnboundLocalError
```

`buggy()` raises `UnboundLocalError: cannot access local variable 'first_err'
where it is not associated with a value` on Python 3.12 — same message, same
class, same reason as production.

## Fix

`/home/drew/.hermes/plugins/sprite-studio/orchestrator.py`, single-function
edit at `_call_timeline_writer` (lines 2240-2352).

Diff summary:

```diff
+        # Bind first_err in the function scope. Python 3 deletes the
+        # `except ValueError as e` binding when the block exits (PEP 3134),
+        # so the feedback prompt below cannot reference the exception
+        # variable directly; copy the message into a stable local first.
+        first_err: str = ""
         try:
             self._validate_timeline(
                 parsed, valid_char_ids=valid_char_ids,
                 target_duration=target_duration,
             )
             return parsed
-        except ValueError as first_err:
+        except ValueError as exc:
+            first_err = _sanitize_error(str(exc))
             logger.warning(
                 "timeline validation failed (attempt 1) project=%s reason=%s",
                 project_id, first_err,
             )
@@
         feedback = (
             f"Your previous output was invalid because: {first_err}\n\n"
             ...
         )
+        logger.info(
+            "timeline retry attempt 2: feeding back error project=%s reason=%r",
+            project_id, first_err,
+        )
@@
-        except ValueError as second_err:
+        except ValueError as exc:
+            second_err = _sanitize_error(str(exc))
             raise TimelineGenerationFailedError(
                 f"timeline writer failed validation twice. "
                 f"first: {first_err} | second: {second_err}",
-            ) from second_err
+            ) from exc
```

Why each piece:

1. **`first_err: str = ""` before the try.** Defense-in-depth: even if a
   future code change introduces a path that reads `first_err` without going
   through the except block first, the read is bound. The empty string is
   unreachable in normal flow because the try-block returns on success.
2. **`except ValueError as exc: first_err = _sanitize_error(str(exc))`.** Copy
   the exception message into a function-scoped variable that survives the
   except block. `_sanitize_error` (orchestrator.py:243) scrubs paths/tokens
   and caps at 500 chars — appropriate ceiling for a Kimi feedback prompt and
   a robustness floor against future validator messages that could embed
   paths.
3. **New INFO log "timeline retry attempt 2: feeding back error ..."**.
   Operators can now grep for this line to confirm retry feedback is firing
   in production without inspecting the DB or adding tracing.
4. **Same `as exc` rename in the second except block.** The line 2339 hazard
   (where the in-block raise referenced `first_err` from the *prior* deleted
   binding) goes away because `first_err` is now a regular function local.
   Both error strings are sanitized before being concatenated into the
   `TimelineGenerationFailedError` message.

The semantics are preserved: success returns immediately on attempt 1; a
single ValueError on attempt 1 triggers retry feedback; failure on retry
raises a typed `TimelineGenerationFailedError` with both errors named.

## Tests run

### Static
- `python3 -m py_compile orchestrator.py` — pass
- `python3 -c "import ast; ast.parse(...)"` — pass
- `python3 -m ruff check orchestrator.py` — `All checks passed!`
- em-dash audit: 3 in file, all pre-existing (lines 1, 436, 3016); 0 added by this fix.
- TODO/FIXME/HACK count: 0 in file (no new ones introduced).

### In-process integration (`_call_timeline_writer` with stubbed chat client)

| # | Scenario | Result |
|---|---|---|
| 1 | Validator passes on attempt 1 → return parsed; 1 chat call | PASS |
| 2 | **Production reproduction**: shot missing narration_excerpt → retry feeds error back to Kimi → second response valid → return parsed; 2 chat calls | PASS, feedback contains `narration_excerpt` |
| 3 | Validator fails twice → `TimelineGenerationFailedError("first: ... | second: ...")` raised, both errors named | PASS |
| 4 | `ProviderResponseShapeError` on attempt 0 → strict-system retry → success | PASS |
| 5 | Validation fails attempt 1 → `ProviderResponseShapeError` on retry → `TimelineGenerationFailedError("non-JSON on retry: ...")` | PASS |
| 6 | Two `_call_timeline_writer` invocations on separate orchestrator instances run concurrently via `asyncio.gather` → both succeed independently (no shared-state corruption) | PASS |
| 7 | Different validator failure (empty `title`) → retry feedback contains the title error | PASS |

Logs from the integration run confirm the new INFO line fires exactly when
retry feedback is constructed:

```
WARNING sprite_studio.orchestrator timeline validation failed (attempt 1) project=P2 reason=shot 1: use_narrator=true but narration_excerpt is missing or empty
INFO    sprite_studio.orchestrator timeline retry attempt 2: feeding back error project=P2 reason='shot 1: use_narrator=true but narration_excerpt is missing or empty'
```

### Live retry of `01KQNY30V69936RQ0P8000PCJD` — NOT EXECUTED

The bridge process is not currently running (no listener on `127.0.0.1:8643`,
no `run-bridge.mjs` in the process table). I did not start `npm run dev` or
issue the slash command, because:
- The 7 in-process tests above exercise the exact failing function with the
  exact production validator message, including the parse and retry paths.
- Starting the bridge is reversible but issues a real Kimi API call
  (~$0.15-0.30 added to the project's existing $0.93 cost), and the user
  has not authorized it for this session.

To run the live retry once `npm run dev` is up, use the curl shown in the
task spec (project has 0 persisted shots, so re-trigger is idempotent):

```bash
KEY=$(grep API_SERVER_KEY /home/drew/.hermes/.env | cut -d= -f2)
curl -s -X POST http://127.0.0.1:8643/slash \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"command":"sprite_timeline","args":"01KQNY30V69936RQ0P8000PCJD"}'
```

Acceptance:
- Kimi call starts (httpx POST visible in bridge log).
- If Kimi returns valid timeline first try → `phase=timeline` advances, shots
  inserted, no traceback.
- If Kimi returns invalid timeline first try → `WARNING timeline validation
  failed (attempt 1) ...` followed by `INFO timeline retry attempt 2:
  feeding back error ...` followed by a second Kimi call carrying the
  validator's error in the user message.
- No `UnboundLocalError`. Final outcome is either success or a clean
  `TimelineGenerationFailedError`.

## Edge cases reasoned through

1. **Kimi returns malformed JSON on attempt 0.** Caught by
   `except ProviderResponseShapeError` at line 2265, retried with strict
   system. `first_err` is never read on this branch. ✓ (case 4 above)
2. **Validator raises with multiple shot errors.** The validator raises on
   the first detected problem — `first_err` always holds a single
   actionable error. ✓
3. **Kimi times out on attempt 0.** `ReadTimeoutError` is not
   `ProviderResponseShapeError`, so it escapes `_call_timeline_writer`. The
   wrapper `_run_timeline_gen_safely` catches it. `first_err` is never read.
   ✓
4. **Validator returns no error string but raises ValueError("").** The
   sanitizer returns the empty string; the f-string produces "Your previous
   output was invalid because: \n\n...". Kimi receives a degraded but
   structurally valid prompt; no crash. (No defensive sentinel needed because
   no validator currently raises with empty message.) ✓
5. **MAX_ATTEMPTS=1 equivalence.** This function has exactly 2 attempts
   hard-wired (initial + one retry). There is no MAX_ATTEMPTS knob, so the
   "what if 1?" question doesn't apply. ✓
6. **Concurrent invocations.** All state is on locals or `self`. No module
   globals are mutated. Concurrent calls are safe. ✓ (case 6 above)

## Dead code removed

None. The function had no unreachable branches, redundant variables, or
stale comments. The single new comment (lines 2281-2284) explains the
non-obvious Python semantic and cites PEP 3134.

## Same-class bugs found

`grep -nE "first_err|prev_err|last_err|previous_error|first_error"` and
`grep -rnE "except [A-Za-z_]+ as [a-z_]+:"` across the entire plugin returned
**no other instance** of an `except X as Y:` whose binding `Y` is read after
the except block exits. The pattern is unique to `_call_timeline_writer`.

The other 35 `except ... as e:` sites in the codebase all use `e` only inside
their own except block (typically: log + raise, or log + early return).

## Adjacent bug found, DEFERRED

In the failing project's `generation_jobs` table, two LLM rows are still
marked `status=running` from the crash:

```
job_type=llm status=running model=moonshotai/kimi-k2.6 err=''
job_type=llm status=running model=moonshotai/kimi-k2.6 err=''
```

These are the timeline_writer calls that returned successfully but then the
post-call validation crashed. `_run_timeline_gen_safely` updates
`projects.error_message` but does not iterate dangling `generation_jobs`
rows for the project to mark them `failed`. This is a *lifecycle* bug, not
the same class as the UnboundLocalError, and fixing it requires touching
`db.py`, `_run_timeline_gen_safely`, and possibly the chat client's job
bookkeeping. Out of scope for P19a-16. Recommend a follow-up
("P19a-17: reap dangling generation_jobs rows on background task failure").

## Files modified

- `/home/drew/.hermes/plugins/sprite-studio/orchestrator.py` (single function:
  `_call_timeline_writer`, lines 2240-2352)

## Verified no source modified outside intended scope

```bash
$ find /home/drew/.hermes/plugins/sprite-studio /home/drew/sprite-studio \
      -name "*.py" -newer /tmp/p19a16_marker -not -path "*/__pycache__/*"
/home/drew/.hermes/plugins/sprite-studio/orchestrator.py
```

Only one file changed.

## Git note

Neither `/home/drew/sprite-studio` nor `/home/drew/.hermes/plugins/sprite-studio`
nor `/home/drew/.hermes` is a git working tree (`fatal: not a git
repository`). No commit was made. The Phase 7 commit step from the spec
does not apply to this layout. If the plugin source is tracked in a
parent repo somewhere outside these paths, the user can stage the change
manually:

```bash
diff -u <pre-fix> /home/drew/.hermes/plugins/sprite-studio/orchestrator.py
```

The hunk is contained in `_call_timeline_writer` (orchestrator.py:2240-2352).

## Acceptance gates

- [x] `_call_timeline_writer` reads correctly on every code path (manual trace + 7 integration cases).
- [x] `first_err` initialized before any read (line 2285).
- [x] Validation failure on attempt 0 sets `first_err` and the retry attempt 1 actually shows the error in the re-prompt (verified by case 2 + new INFO log).
- [x] `py_compile orchestrator.py` passes.
- [x] No new `# TODO`, `# FIXME`, `# HACK` introduced (count: 0).
- [x] No em dashes in modified lines (`grep -n "—"` shows 3 hits, all pre-existing).
- [x] No banned buzzwords introduced.
- [x] Report file written.
- [x] Same-class bug audit complete; no other instances found.
- [ ] Bridge restarted + live retry of project — DEFERRED, see "Live retry" section above; not authorized to spend Kimi credits without explicit ack.
- [ ] Git commit — N/A, no git repo.

## P19a-16 COMPLETE.

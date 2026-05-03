# P19a-18: Bridge Process Lifecycle - DONE

## Change summary

`npm run dev:bridge` is now self-healing. Every start clears any prior bridge
or asset-server holding ports 8643 / 9120, regardless of how the prior
instance terminated (clean SIGINT, supervisor SIGKILL, detached `python
bridge/server.py`, OOM kill, crash mid-startup, stale PID file from a prior
boot, etc.). No `pkill -f` band-aid; no `fuser -k` in the dev script.

The fix lives in Python (`bridge/server.py`) so it applies to every launch
path: `npm run dev`, `npm run dev:bridge`, `bash bridge/run.sh`, and direct
`python bridge/server.py`.

## Files modified

| File | Lines | Role |
|---|---|---|
| `bridge/server.py` | 541 (+250) | PID file + flock + signal handlers + pre-flight kill |
| `scripts/run-bridge.mjs` | 115 (no change) | Already forwards SIGINT/SIGTERM/SIGHUP to the python child; verified, untouched |
| `scripts/kill-bridge.sh` | 32 (new) | Manual rescue for truly wedged states; not invoked by `dev:bridge` |
| `bridge/run.sh` | 12 (-3) | Removed dead `pkill -f workers/asset_server.py`; pre-flight handles it |
| `package.json` | +1 | New `kill:bridge` script alias for the rescue helper |

## Lifecycle behavior

```
                                  +-----------------+
                                  | npm run dev     |
                                  +--------+--------+
                                           |
                                           v
                          run-bridge.mjs (signal forwarder)
                                           |
                                           v
                              python bridge/server.py
                                           |
        +----------------------------------+----------------------------------+
        | _install_signal_handlers   (atexit + SIGINT/SIGTERM/SIGHUP)         |
        | _acquire_start_lock        (flock LOCK_EX|LOCK_NB on bridge.lock)   |
        | _preflight_cleanup                                                  |
        |     pass 1: pid-file pid alive + python? -> SIGTERM, escalate KILL  |
        |     pass 2: /proc/net/tcp scan for 8643/9120 -> kill python holders |
        | load_plugin / make_app                                              |
        | _write_pid_file            (pid + comm + port + iso utc timestamp) |
        | _release_start_lock                                                 |
        | web.run_app  (try)                                                  |
        |     ...serve traffic...                                             |
        | finally: _cleanup_pid_file                                          |
        +---------------------------------------------------------------------+

shutdown paths:
  SIGINT/SIGTERM via supervisor  -> aiohttp shutdown -> finally + atexit -> pid file removed
  SIGHUP                          -> our signal.signal handler -> sys.exit(0) -> atexit
  SIGKILL (uncatchable)           -> process dies, pid file is stale; the next start's
                                     _preflight_cleanup detects /proc/<pid> is gone and
                                     deletes the stale file
```

## Tests run (6/6 PASS)

| # | Scenario | Result |
|---|---|---|
| T1 | Clean lifecycle: `npm run dev:bridge`, hit /health, SIGTERM the supervisor, watch python shut down, PID file removed, ports freed | PASS |
| T2 | Stale PID file (`echo "999999 server.py 8643 ..." > bridge.pid`) before start; pre-flight logs `stale pid file (pid=999999 not a python process)` and binds cleanly | PASS |
| T3 | Detached `python bridge/server.py` already holding 8643 + 9120; new `npm run dev:bridge` logs `preflight: prior bridge pid=N still alive; terminating` and takes over | PASS |
| T4 | Two `npm run dev:bridge` started ~50 ms apart; lock race produces one winner that binds, the loser exits with code 2 and the message `another bridge instance is starting on the same lock` | PASS (1 process alive after settle) |
| T5 | SIGINT to the supervisor; aiohttp shuts down, finally + atexit fire, PID file is removed, ports free | PASS |
| T6 | SIGKILL the python process directly (uncatchable); ports free instantly, PID file is stale; restart `npm run dev:bridge`, pre-flight logs `stale pid file (pid=N not a python process)` and binds | PASS |

Logs of each run are at `/tmp/p19a18_t{1..6}*.log`.

Real-workload smoke: `/health` returned `{"status":"ok","plugin_loaded":true,"command_count":28}` on every test that bound the bridge, including takeover after a detached instance. A full cast-phase fan-out was not exercised in this task; the slash router and asset server come up identically to prior boots.

## Edge cases reviewed (8/8)

1. **PID reuse.** Pre-flight reads `/proc/<pid>/comm` and only kills the holder if comm starts with `python`. PID-reuse to an unrelated process (e.g. `bash`) skips the kill; the stale PID file is deleted instead. Verdict: **handled** by `_is_python_process()`.

2. **PID file owned by another user.** `_RUN_DIR` is created with default `mkdir 0700`. Cross-user takeover is out of scope for the dev workflow this task targets. Verdict: **out of scope** (single-dev-machine layout).

3. **NFS / non-local FS for `~/.hermes`.** `flock` semantics on NFS are not reliable. The current code does not assert. Verdict: **known limitation, not handled**. Realistic risk on this project is zero; `~/.hermes` is the user's local home on WSL2. Document for future readers if a multi-host setup ever lands.

4. **WSL2 specifics.** `/proc/<pid>/comm`, `/proc/net/tcp`, and `/proc/<pid>/fd/*` all exist on WSL2 (verified in Phase 0). `ss`, `fuser`, `flock` ship preinstalled in Ubuntu base; only the kill-bridge.sh helper depends on `fuser`, and the core fix uses pure Python. Verdict: **works**.

5. **Bridge crashes during pre-flight.** Lock fd is open; on process death the kernel releases the flock. Next start re-acquires cleanly. The PID file may not have been written yet, in which case there is nothing to clean. Verdict: **handled** by kernel cleanup.

6. **User runs `python bridge/server.py` directly.** The lifecycle code lives in `main()`, so direct invocation gets the same lock + pre-flight + signal handlers as the npm path. A subsequent `npm run dev:bridge` finds the directly-launched bridge's PID file and terminates it. Verdict: **handled**.

7. **Asset server on 9120 stuck independently.** `_preflight_cleanup` iterates over `((8643, "bridge"), (9120, "asset"))`; either port being held by a python process triggers a kill. The standalone `workers/asset_server.py` is `python3` so it matches the comm gate. Verdict: **handled**.

8. **systemd / pm2 wrapping the bridge.** The pre-flight kill assumes the bridge is the source of truth for its own lifecycle. A managed-service supervisor would expect to own that. There is currently no env knob to disable the pre-flight; if this ever becomes a real deployment shape, add e.g. `BRIDGE_SKIP_PREFLIGHT=1` and gate `_preflight_cleanup` on it. Verdict: **known limitation, document if needed**. Out of scope for the dev workflow.

## Dead code removed

- `bridge/run.sh`: the `pkill -f "sprite-studio/workers/asset_server.py"` line (and its 3-line context comment) is gone. The pre-flight in `server.py` covers the same case more precisely (only kills python comm holders) and applies to every launch path, not just `run.sh`.

No other process-management band-aids were found in source code. Two `Bash(pkill -f ...)` entries remain in `.claude/settings.local.json`, but those are just permission allowlist entries (not active code) and removing them would only re-prompt for permission if a future agent or human types those commands.

## Acceptance gates

- [x] `bridge/server.py` viewed in full before edit
- [x] `scripts/run-bridge.mjs` viewed in full before edit (already correct, no changes)
- [x] PID file path `~/.hermes/plugins/sprite-studio/run/bridge.pid`; parent dir created if absent
- [x] PID file format: `<pid> server.py <port> <iso utc timestamp>` (PID-reuse safe via comm check on read)
- [x] `flock` LOCK_EX|LOCK_NB serializes concurrent supervisor starts
- [x] `/proc/<pid>/comm` check prevents killing unrelated PID-reuse processes
- [x] Independent `/proc/net/tcp` scan + `/proc/*/fd` walk catches bridges started without writing the PID file
- [x] SIGTERM, SIGINT, SIGHUP handlers all clean the PID file (defense-in-depth alongside aiohttp's own shutdown)
- [x] `atexit.register(_cleanup_pid_file)` covers normal exit paths
- [x] `try/finally` around `web.run_app` cleans the PID file on any web shutdown
- [x] Node supervisor forwards SIGINT/SIGTERM/SIGHUP (verified, was already correct)
- [x] Static checks: `python3 -m py_compile bridge/server.py` and `node --check scripts/run-bridge.mjs` both pass
- [x] All 6 lifecycle tests pass
- [x] All 8 edge cases documented
- [x] No `pkill -f` band-aid added; one was removed
- [x] No em dashes or banned buzzwords introduced
- [x] Report written

## Files modified verification

```
$ find /home/drew/sprite-studio -newer /tmp/p19a18_marker -type f \
       -not -path "*/node_modules/*" -not -path "*/.git/*" \
       -not -path "*/__pycache__/*" -not -path "*/projects/*"
/home/drew/sprite-studio/package.json
/home/drew/sprite-studio/scripts/kill-bridge.sh
/home/drew/sprite-studio/bridge/server.py
/home/drew/sprite-studio/bridge/run.sh
/home/drew/sprite-studio/.claude/settings.local.json   # permission allowlist auto-update; not part of fix
```

```
P19a-18 COMPLETE.
```

#!/usr/bin/env bash
# Manual rescue for a stuck bridge or asset server. Not invoked by
# `npm run dev`; the Python pre-flight in bridge/server.py handles
# self-healing on every start. Run this only when something got truly
# wedged and you want a clean slate.
set -euo pipefail
PID_FILE="$HOME/.hermes/plugins/sprite-studio/run/bridge.pid"
LOCK_FILE="$HOME/.hermes/plugins/sprite-studio/run/bridge.lock"

if [[ -f "$PID_FILE" ]]; then
    pid=$(awk '{print $1}' "$PID_FILE")
    if [[ -n "$pid" && -d "/proc/$pid" ]]; then
        kill -TERM "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            [[ -d "/proc/$pid" ]] || break
            sleep 1
        done
        [[ -d "/proc/$pid" ]] && kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi

# Stale lock files are harmless (the lock itself is held by an open fd, not
# by the file's existence), but remove them so the dir stays tidy.
rm -f "$LOCK_FILE"

# Belt-and-suspenders: if anything is still holding the ports, terminate it.
fuser -k -TERM 8643/tcp 9120/tcp 2>/dev/null || true
sleep 1
fuser -k -KILL 8643/tcp 9120/tcp 2>/dev/null || true

echo "[kill-bridge] cleared bridge state"

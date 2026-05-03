#!/usr/bin/env bash
# Convenience launcher: sources Hermes env, runs the bridge from Hermes' venv.
# Pre-flight cleanup of stale bridge/asset processes is handled inside
# server.py itself (P19a-18); see _preflight_cleanup() there. No pkill
# needed at this layer.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
# shellcheck disable=SC1090
source "$HOME/.hermes/.env"
set +a
exec "$HOME/.hermes/hermes-agent/venv/bin/python3" "$SCRIPT_DIR/server.py" "$@"

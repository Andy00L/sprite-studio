#!/usr/bin/env bash
# Convenience launcher for the sprite-studio asset server (port 9120).
# Runs the script directly via the Hermes venv so we don't have to install
# the plugin as a package.
set -euo pipefail
exec "$HOME/.hermes/hermes-agent/venv/bin/python3" \
  "$HOME/.hermes/plugins/sprite-studio/workers/asset_server.py" "$@"

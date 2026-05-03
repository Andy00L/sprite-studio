# Sprite Studio dev quick start

## Prerequisites (one-time, per developer)

- Node `^20.19.0 || >=22.12.0` (matches the floor that vite 8 enforces).
- Python 3.11+ on PATH.
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed at
  `~/.hermes/`. The agent ships a venv at
  `~/.hermes/hermes-agent/venv/` that the bridge runs in.
- `~/.hermes/.env` containing at least `API_SERVER_KEY=<some-secret>`.
  Generate one with `openssl rand -hex 16` if you do not have one.
- The Sprite Studio plugin installed at
  `~/.hermes/plugins/sprite-studio/` (typically a symlink to your checkout
  of the plugin repo).

If your Hermes layout uses a different prefix, set `HERMES_HOME` and the
bootstrap will follow it.

## First run

```bash
git clone <repo>
cd sprite-studio
npm install        # installs concurrently at the root
npm run setup      # installs web/node_modules, copies .env.example to .env
npm run check      # verifies your environment (fix any FAIL before continuing)
npm run dev        # starts bridge (8643) + asset server (9120) + vite (5173)
```

## What `npm run dev` does

Spawns two processes in one terminal with color-coded prefixes:

- `[bridge]` (blue): the Hermes-venv python running `bridge/server.py`,
  which itself binds the slash-command sidecar on port 8643 and the static
  asset server on port 9120.
- `[web]` (green): `vite` in `web/` on port 5173, with a proxy from `/api`
  to the bridge.

Ctrl-C kills both. If either child exits non-zero, concurrently kills the
other (`--kill-others-on-fail`).

## When `npm run check` complains

Each FAIL prints a one-line fix. The common ones:

- **Python 3.11 not found**: `apt install python3.11` (Ubuntu / Debian),
  `brew install python@3.11` (macOS).
- **Hermes venv missing**: install Hermes Agent per its docs.
- **API_SERVER_KEY missing**:
  ```bash
  echo "API_SERVER_KEY=$(openssl rand -hex 16)" >> ~/.hermes/.env
  ```
- **plugin path missing**: symlink your plugin checkout into
  `~/.hermes/plugins/sprite-studio` (or set `SPRITE_PLUGIN_PATH`).

Warnings (yellow) are non-blocking. The most common one is "port in use",
which usually means a previous `npm run dev` is still around; kill it
before starting a new one.

## Available scripts

| Script | What it does |
| --- | --- |
| `npm run dev` | Bridge + asset server + vite, all in one terminal |
| `npm run dev:bridge` | Just the bridge (port 8643 + 9120) |
| `npm run dev:web` | Just the vite dev server (port 5173) |
| `npm run setup` | First-time setup; safe to re-run |
| `npm run check` | Preflight diagnostic; no side effects |
| `npm run build` | Production build of the web app |
| `npm run lint` | Lint the web app |
| `npm run typecheck` | `tsc --noEmit` on the web app |

## Platform notes

- Linux, macOS, and WSL2 are supported.
- Windows-native is not supported because the Hermes plugin uses Unix
  paths internally; use WSL2.
- The asset server hardcodes port 9120 (no env override). If you need to
  change it, edit `bridge/server.py` directly.

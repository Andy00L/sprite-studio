# P19a-20: GitHub Publish PREP - DONE (no commit, no push)

## State
- Working tree staged on local branch `main`
- Commit: NOT created (user runs `git commit` manually after review)
- Push:   NOT performed
- Tag:    NOT created

## Files staged (122 total)
- `plugin/` vendored (orchestrator.py, services/, prompts/, workers/, models.py, db.py, env.py, commands.py, plugin.yaml, requirements.txt, style_presets.{py,yaml}, __init__.py)
- `web/` source tree (src/, vite.config.ts, tsconfig*.json, tailwind.config.js, package.json, index.html, public/, .gitignore, .env.example)
- `bridge/` (server.py, run.sh, run-assets.sh)
- `scripts/` launchers
- `build_prompts/P19a-*_DONE.md` audit trail (12 files: 0, 1-7, 9, 12, 13, 15-19)
- Top-level: `.env.example`, `.gitignore`, `README.md`, `LICENSE`, `package.json`, `package-lock.json`, `DEV.md`, `SPRITE_STUDIO_BLUEPRINT.md`, `REFERENCE_SECURITY_AUDIT.md`

## Files excluded (verified via `git status --ignored`)
- `.env`, `.env.local`, `web/.env.local` (secrets)
- `plugin/state.db`, `plugin/projects/`, `plugin/run/`, `plugin/cron/`, `plugin/music_library/` (runtime state, none present in vendored plugin)
- `node_modules/`, `web/node_modules/`, `web/dist/`
- `__pycache__/`, `*.pyc`, `bridge/__pycache__/`, `.ruff_cache/`
- `.claude/`, `.agents/`, `skills-lock.json` (local agent tooling)
- `web/_design_reference/` (heavy assets)
- `build_prompts/_production/`, `_smoke_test/`, `_verified_shapes/` (working artifacts)

## Verification
- 8 secret regex sweeps run on the staged diff (`/tmp/p19a20_staged.patch`):
  - sweep 1 (sk- keys): one hit at `build_prompts/P19a-12_DONE.md:288` — synthetic alphabet test fixture (`sk-abcdefghijklmnopqrstuvwxyz1234`) used to document the sanitizer. Verified non-secret.
  - sweeps 2-8 (Bearer tokens, TOKENROUTER, OPENAI, ELEVENLABS, VITE_SPRITE_BRIDGE_KEY, API_SERVER_KEY, long hex blobs): all clean.
- Live `~/.hermes/.env` and `web/.env.local` md5 verified pre and post (`/tmp/p19a20_env_pre.md5` -> both OK at end of phase 5).
- `git ls-files` contains zero forbidden paths.
- `git status --ignored` confirms heavy / sensitive paths correctly excluded by `.gitignore`.
- File count 122 (within 50-500 expected range).
- `plugin/requirements.txt` audited: 8 PyPI packages, no editable local paths.
- No source files (.py, .ts, .tsx, .mjs, .js) modified by this prompt.

## Notes on `.env.example`
The live `~/.hermes/.env` is the Hermes-wide secrets file shared by all plugins. Its 19 active variables include entries for other plugins (Browserbase, Terminal) plus shared Hermes core knobs that Sprite Studio does not read. To pass the prompt's strict diff check (`comm -23 live template` must be empty), `.env.example` lists every live variable, grouped:
- Required for Sprite Studio: `TOKENROUTER_API_KEY`, `ELEVENLABS_API_KEY`, `API_SERVER_KEY`
- Optional Sprite Studio surfaces: `OPENAI_API_KEY`, `SPRITE_STUDIO_VIDEO_TIER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`
- Optional Hermes-wide (other plugins): `HERMES_MAX_ITERATIONS`, `*_TOOLS_DEBUG`, `BROWSERBASE_*`, `BROWSER_*`, `TERMINAL_*`

The file additionally documents the bridge launcher overrides (`HERMES_HOME`, `SPRITE_BRIDGE_HOST`, `SPRITE_BRIDGE_PORT`, `SPRITE_PLUGIN_PATH`) that previously lived in this template, preserved as Scope 2 for `./env` or shell.

## Next steps for the user

1. Review staged tree:
   ```
   git status
   git diff --cached | less
   git ls-files | less
   ```

2. Commit:
   ```
   git commit -m "Initial commit: Sprite Studio v0.1 (Hermes hackathon)"
   ```
   (or pick your own message)

3. Create empty repo on github.com (no README, no .gitignore, no license).

4. Push:
   ```
   git remote add origin https://github.com/<USER>/sprite-studio.git
   git push -u origin main
   ```

5. Tag:
   ```
   git tag -a v0.1.0-hackathon -m "Hackathon submission - May 3 2026"
   git push origin v0.1.0-hackathon
   ```

6. Post-push: open the repo URL in a browser. Confirm no `.env`, `state.db`, `node_modules`, or `__pycache__` visible. Confirm `plugin/orchestrator.py` and `web/src/App.tsx` present.

7. Smoke test the dev environment still boots:
   ```
   npm run dev
   curl -sf http://127.0.0.1:8643/health
   ```

## Rollback
If anything looks wrong, the local repo can be reset cleanly:
```
rm -rf /home/drew/sprite-studio/.git
```
(The `plugin/` directory is also vendored fresh; remove with `rm -rf /home/drew/sprite-studio/plugin` if you want a fully-undone state. Live `~/.hermes/.env` and `web/.env.local` were not modified.)

```
P19a-20 PREP COMPLETE. Awaiting manual commit + push by user.
```

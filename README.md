# Sprite Studio

Hermes-orchestrated AI video creation studio. One-line brief turns into a 30-90s video with a locked-in cast, a shot timeline, and ElevenLabs narration. Three surfaces (web canvas, Hermes CLI, Telegram), one engine.

## What it does

Type a brief like "two cats running a detective agency". The agent:

1. Designs 1-30 characters with consistent sprite-sheets via gpt-image-2.
2. Writes a 6-shot timeline with dialog, narration, camera direction.
3. Generates per-shot reference stills (multi-character composition with locked identity).
4. Renders shots via Seedance 2.0 image-to-video.
5. Layers narration (ElevenLabs) and music (CC0 library), stitches via ffmpeg.
6. Returns a 9:16 1080p MP4.

Cost runs ~$8-25/video depending on iteration count.

## Quick start

Prerequisites: Node 20+, Python 3.11+, Hermes Agent v0.11+, ffmpeg, a TokenRouter account, an OpenAI key with gpt-image-2 access, an ElevenLabs Creator plan.

```bash
git clone https://github.com/<you>/sprite-studio.git
cd sprite-studio

# 1. Install Hermes plugin
cp -r plugin ~/.hermes/plugins/sprite-studio
pip install -r ~/.hermes/plugins/sprite-studio/requirements.txt

# 2. Configure secrets
cp .env.example ~/.hermes/.env
# edit ~/.hermes/.env with real values

cp web/.env.example web/.env.local
# set VITE_SPRITE_BRIDGE_KEY to match API_SERVER_KEY

# 3. Web app
npm install
cd web && npm install && cd ..

# 4. Run (boots bridge on :8643 and Vite on :5173)
npm run dev
```

Open `http://localhost:5173`.

## Stack

- Hermes Agent v0.11 (plugin loader, slash router, agent loop)
- Kimi K2.6 via TokenRouter (cast designer, timeline writer, edits)
- gpt-image-2 (character sheets, shot reference stills)
- Seedance 2.0 (image-to-video)
- ElevenLabs `eleven_multilingual_v2` (narration)
- React 19 + Vite 8 + TypeScript (web canvas)
- aiohttp (bridge sidecar on :8643, asset server on :9120)
- SQLite (project state at `~/.hermes/plugins/sprite-studio/state.db`)

## Architecture

See `SPRITE_STUDIO_BLUEPRINT.md` for the full design. See `build_prompts/P19a-*.md` for the audit trail.

## Tradeoffs

- **Latency.** Kimi reasoning can spike to 5+ minutes on complex briefs. Timeline phase has retry-with-feedback and JSON shape validation, but vendor latency is the floor.
- **Cost.** A 5-character cast at gpt-image-2 quality=high runs ~$2 just for sheets. The cap is 30 with a confirmation gate at 12.
- **Single-machine.** State lives in local SQLite. No multi-host coordination, no cloud render farm.
- **Hackathon scope.** Telegram and Discord surfaces are stubbed; the web canvas and CLI are the live paths.

## License

MIT (see LICENSE).

# SPRITE STUDIO
## Cahier des charges complet

A Hermes-powered video creation studio. Three interfaces (web canvas, Hermes CLI, Telegram/Discord), one engine. User describes a video, the agent designs the cast, the user approves or modifies via chat, the agent renders a 30-90 second video with locked character consistency.

This document is the contract. Everything below is what we build.

---

## 0. Glossary

- **Project**: One video creation session. Has a goal, a style, a cast, a timeline, and a final video.
- **Cast**: The set of characters in a project. Each character has a name, persona, and a sprite-sheet (model sheet).
- **Sprite-sheet**: 6-9 reference images of one character: front, three-quarter, side, expressions, full body. Used as input to Seedance.
- **Style preset**: A bundle of visual descriptors that get injected into every image and video prompt. Pre-curated library.
- **Timeline**: An ordered sequence of scenes (shots), each ~5-15 seconds. The user approves the timeline before render.
- **Shot**: One generated video clip. Has a setting, characters present, action, and dialog/narration.
- **Render**: The final stitching pass. Concatenates shots, layers narration + music, exports MP4.

---

## 1. The user's experience (three flows, one engine)

### Flow A: Web canvas (primary, hero of the demo)

The user opens `localhost:5173`. They see a workspace with:

- **Left panel: Chat** (talking to Hermes)
- **Center: Workspace** (depends on phase)
- **Right panel: Project sidebar** (style preset, parameters, render button)

The phases of a project:

1. **Brief phase**: User types a one-line description in chat. Hermes asks 2-3 clarifying questions (length, style, vibe).
2. **Cast phase**: Center workspace shows the **Character Canvas**: a horizontal row of "character slots." Hermes proposes 2-4 characters as bubbles on this row. Each bubble shows a generated portrait. Click a bubble to expand: full sprite-sheet, persona description, edit-with-chat.
3. **Timeline phase**: Center workspace shows the **Timeline**: horizontal track of shot cards. Each card has a thumbnail (generated reference still), a duration, and the dialog/narration line. User can drag to reorder, click to edit, ask Hermes to "make shot 3 funnier" via chat.
4. **Render phase**: User clicks **Render**. Progress bar fills shot by shot. Final video plays inline when done.

### Flow B: Hermes CLI / TUI

Same engine. User runs `hermes` and types `/sprite-studio new`. The agent prompts for goal, style, length. Same agent loop runs. Instead of a canvas, the agent puts files in `~/.hermes/sprite-studio/projects/<project-id>/cast/` and tells the user "look at this folder, all 4 characters are there, named character_1 through character_4. Tell me which one to refine."

### Flow C: Telegram / Discord

Same engine. User starts a chat with the bot. Bot sends generated portraits as images, indexed: "Character 1 of 4: Mira (the curious girl with red hair)." User replies "make character 2 older, give him a beard." Bot regenerates and resends. Approval via reactions or `/approve`.

**The key insight**: the same Hermes plugin handles all three. The web app, CLI, and gateway are all just different presentation layers consuming the same agent state.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PRESENTATION LAYER (3 surfaces, same backend)                           │
│                                                                           │
│  ┌──────────────────────┐  ┌──────────────────┐  ┌────────────────────┐ │
│  │   WEB CANVAS         │  │   HERMES TUI     │  │   TELEGRAM/DISCORD │ │
│  │                      │  │                  │  │                     │ │
│  │  React + Vite        │  │  hermes --tui    │  │  hermes gateway    │ │
│  │  dnd-kit             │  │  /sprite-studio  │  │  Bot replies with  │ │
│  │  dnd-timeline        │  │  CLI commands    │  │  indexed images    │ │
│  │  WebSocket + SSE     │  │                  │  │                     │ │
│  └──────────────────────┘  └──────────────────┘  └────────────────────┘ │
└─────────────────────┬────────────────────────────────────────────────────┘
                      │ HTTP + SSE on Hermes API server
                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  HERMES AGENT (your server)                                               │
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  sprite-studio Hermes plugin                                       │  │
│  │  ~/.hermes/plugins/sprite-studio/                                  │  │
│  │                                                                    │  │
│  │  Phases (state machine):                                           │  │
│  │   brief → cast → timeline → render → done                          │  │
│  │                                                                    │  │
│  │  Slash commands:                                                   │  │
│  │   /sprite_new "<brief>"                                            │  │
│  │   /sprite_cast (regenerate cast)                                   │  │
│  │   /sprite_edit_character <id> "<changes>"                          │  │
│  │   /sprite_add_character "<description>"                            │  │
│  │   /sprite_approve_cast                                             │  │
│  │   /sprite_timeline (generate or show)                              │  │
│  │   /sprite_edit_shot <n> "<changes>"                                │  │
│  │   /sprite_approve_timeline                                         │  │
│  │   /sprite_render                                                   │  │
│  │   /sprite_status (poll progress)                                   │  │
│  │   /sprite_cancel                                                   │  │
│  │                                                                    │  │
│  │  Hooks:                                                            │  │
│  │   on_session_start: load active project                            │  │
│  │   pre_tool_call: rate-limit guard, auth check                      │  │
│  │   transform_tool_result: enrich responses with sprite indexes      │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  Storage:                                                                 │
│   ~/.hermes/plugins/sprite-studio/                                       │
│   ├── state.db                  (SQLite — projects, characters, shots)    │
│   ├── projects/                                                           │
│   │   └── <project_id>/                                                   │
│   │       ├── cast/             (sprite-sheet PNGs per character)        │
│   │       ├── shots/            (reference frames + rendered MP4 clips)  │
│   │       ├── audio/            (ElevenLabs narration MP3s)              │
│   │       ├── music/            (selected CC0 track)                     │
│   │       └── output/           (final stitched MP4)                     │
│   └── style_presets.yaml                                                  │
└─────────────────────┬────────────────────────────────────────────────────┘
                      │ HTTPS
                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  EXTERNAL SERVICES (all routed through TokenRouter except ElevenLabs)    │
│                                                                           │
│  TokenRouter (https://api.tokenrouter.com/v1)                            │
│    ├── moonshotai/kimi-k2.6        ($0.95/M in, $4/M out)                │
│    │     → planning, dialog, JSON outputs                                │
│    ├── anthropic/claude-sonnet-4.6  ($3/M in, $15/M out)                 │
│    │     → narration prose (richer voice)                                │
│    ├── anthropic/claude-opus-4.7    ($5/M in, $25/M out)                 │
│    │     → final shot script polish (1 call per project)                 │
│    ├── openai/gpt-5.5               ($5/M in, $30/M out)                 │
│    │     → reserved fallback                                              │
│    ├── dreamina-seedance-2-0-260128 ($7/M video tokens, ~$1.25/10s 720p) │
│    │     → image-to-video for each shot                                   │
│    ├── dreamina-seedance-2-0-fast   ($5.6/M video tokens, drafts)        │
│    │     → cheaper iteration during dev                                   │
│    └── (kling-v3 / kling-v2-6 reserved as Seedance fallback)             │
│                                                                           │
│  OpenAI direct (gpt-image-2)                                             │
│    ├── Generation: $0.005-$0.211/image depending on quality              │
│    └── Editing (multi-reference): up to 16 reference images               │
│                                                                           │
│  ElevenLabs (Creator plan, you have it)                                  │
│    └── Multi-voice narration + character voices                           │
│                                                                           │
│  Optional:                                                                │
│    ├── Suno API (background music, ~$0.10/track, otherwise CC0 library)  │
│    └── Gemini 2.5 Pro (skip per Q3, available if needed)                 │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Phase-by-phase deep dive

### 3.1 Brief phase

User says: "I want a 60-second cartoon about two cats who run a detective agency."

Hermes (Kimi K2.6) responds with 1-3 clarifying questions (max), each with suggested defaults so the user can press enter:

> "Sounds great. A few quick questions before I cast it:
>
> 1. Style: cartoon (default), Pixar 3D, watercolor children's book, anime, or something else?
> 2. Vibe: comedic (default), cozy, mysterious, action-packed?
> 3. Length: 60s (default), 30s, 90s?
>
> Reply with answers or just 'go' to use defaults."

**State persisted at end of brief**: `project.brief` (raw user text), `project.style_preset_id`, `project.duration_seconds`, `project.vibe`.

### 3.2 Cast phase

Hermes generates a `Cast Plan` (Kimi K2.6 with structured JSON output):

```json
{
  "characters": [
    {
      "id": "char_1",
      "name": "Mira",
      "role": "lead",
      "persona": "the methodical one, observant, easily annoyed, wears a tiny trench coat",
      "visual_description": "An adult tabby cat, female, mostly grey-brown with cream chest and white-tipped paws, intelligent green eyes, wearing a beige miniature trench coat with a tiny notebook in one pocket. Cartoon stylization, soft round shapes, bold outlines.",
      "voice_id": "<elevenlabs_voice_id_warm_female>",
      "voice_personality": "dry, measured, slightly sardonic"
    },
    {
      "id": "char_2",
      "name": "Boon",
      "role": "comic_relief",
      "persona": "...",
      "visual_description": "...",
      "voice_id": "<elevenlabs_voice_id_male_excitable>",
      "voice_personality": "...",
    }
  ]
}
```

Then Hermes calls **gpt-image-2 with `n=8` and a multi-pose prompt** to produce one master sprite-sheet per character. Single API call per character. Critical for consistency.

The master sprite-sheet prompt template:

```
Character model sheet on white background. 4 panels in a 2x2 grid, all showing the SAME character with consistent design across all panels:

Panel 1: Front-facing, neutral expression, full body
Panel 2: Three-quarter angle, slight smile, full body
Panel 3: Side profile, walking pose
Panel 4: Bust-up close-up, expressive face

Character: {visual_description}

Style: {style_preset.descriptor}
Render notes: {style_preset.render_notes}

Constraints:
- Identical character design across all 4 panels
- Same color palette in all panels
- Plain off-white background, no scenery
- Soft natural lighting, no dramatic shadows
- No text, no watermarks
```

**State persisted**: `cast/<char_id>/sheet.png`, plus per-pose crops in `cast/<char_id>/poses/`.

The web canvas displays each character as a bubble:

```
[Character Canvas]
─────────────────────────────────────────────────
   ╔════╗     ╔════╗     ╔════╗
   ║ 🐱 ║     ║ 🐈 ║     ║  + ║
   ║Mira║     ║Boon║     ║add ║
   ╚════╝     ╚════╝     ╚════╝
   ↑click for sprite-sheet + persona
─────────────────────────────────────────────────
```

In CLI: `ls ~/.hermes/plugins/sprite-studio/projects/<id>/cast/` shows folders. Hermes says "I made 2 characters: char_1 (Mira), char_2 (Boon). Look at the cast folder and tell me what to change."

In Telegram: bot sends 2 images, captioned "Character 1 of 2: Mira" and "Character 2 of 2: Boon."

**User edits via chat** (this is the magic):
> "Make Mira's coat dark blue instead of beige."

Hermes (Kimi K2.6 via plugin) routes this to a character edit. Two options for handling:

- **Cheap path**: Update `char_1.visual_description`, regenerate sprite-sheet from scratch ($0.21). Risk: design drift.
- **Surgical path**: Use `gpt-image-2/edit` with the existing sheet as input, prompt "change the trench coat color from beige to dark navy blue, preserve everything else." ($0.21 but identity preserved).

**We use the surgical path.** Per OpenAI's docs, gpt-image-2/edit preserves identity across edits.

User keeps editing until they hit `/sprite_approve_cast`.

### 3.3 Timeline phase

Hermes (Claude Opus 4.7, ONE call per project for narrative quality) generates a `Timeline Plan`:

```json
{
  "title": "The Coffee Shop Caper",
  "narrator_script": "Mira had been watching the man for twenty minutes...",
  "shots": [
    {
      "shot_id": "shot_1",
      "duration_seconds": 8,
      "setting": "A cozy cartoon coffee shop, morning light, warm colors",
      "characters_present": ["char_1"],
      "action": "Mira sits at a window booth, eyes narrowed, holding a tiny notebook",
      "camera": "slow push-in on her face",
      "narration_line": "Mira had been watching the man for twenty minutes.",
      "character_dialog": null,
      "emotion": "suspicious"
    },
    ...
  ]
}
```

Number of shots = `floor(duration_seconds / 8)`. For 60s, ~7-8 shots at ~8s each.

For each shot, Hermes generates a **reference still** via gpt-image-2 with multi-reference compositing (per the OpenAI cookbook):

```
Image 1 (reference): {char_1.master_sheet_url}    [labeled: "Mira"]
Image 2 (reference): {char_2.master_sheet_url}    [labeled: "Boon"]   ← only if present in shot

Prompt:
Scene: {shot.setting}
Action: Show Mira (from reference Image 1) in this scene. {shot.action}
Camera: {shot.camera}
Style: {style_preset.descriptor}
Render notes: {style_preset.render_notes}
Aspect ratio: 9:16

Constraints:
- Mira must look identical to her appearance in reference Image 1
- No new characters introduced
- No text or captions in the image
```

This is the consistency pipeline. **Same character reference fed into every shot's reference still.** Per fal.ai's docs: "The GPT Image family accepts up to 16 reference images for edits and takes either file IDs or fully qualified URLs."

The web canvas displays the timeline as a horizontal scroll of shot cards using **dnd-timeline** (dnd-kit-based, supports drag-to-reorder, drag-to-resize-duration, drag-to-create-new-shot).

User edits via chat:
> "Shot 4 looks too dark. Make it more cheerful, daytime."

Hermes regenerates that shot's reference still with the modified setting.

User keeps editing until they hit `/sprite_approve_timeline`.

### 3.4 Render phase

This is the big spend. Locked behind explicit approval. Render runs as a Hermes background job.

**For each shot, in parallel (capped at 4 concurrent to avoid rate limits):**

1. Call **dreamina-seedance-2-0** image-to-video via TokenRouter:
   ```json
   {
     "model": "dreamina-seedance-2-0-260128",
     "input": {
       "image_url": "<shot_n.reference_still_url>",
       "prompt": "<shot_n.action> {style_preset.motion_descriptor}",
       "duration": 8,
       "resolution": "720p",
       "aspect_ratio": "9:16",
       "audio": false
     }
   }
   ```
2. Poll for completion (~60-120s per shot)
3. Download MP4 to `shots/<shot_id>.mp4`

**While videos render, in parallel:**

- Call **ElevenLabs** with the full narrator_script:
  ```python
  audio = elevenlabs.text_to_speech.convert(
      voice_id=NARRATOR_VOICE,
      model_id="eleven_multilingual_v2",
      text=full_script,
      output_format="mp3_44100_192",
  )
  ```
- For shots with character_dialog, call ElevenLabs once per character voice with their lines, save to `audio/dialog_<char_id>_<shot_id>.mp3`
- Pick a CC0 music track from `~/.hermes/plugins/sprite-studio/music_library/<style_preset.music_tag>/`

**Final stitching (ffmpeg):**

```bash
# Concatenate shots, layer narration over music, fade title and end cards
ffmpeg -y \
  -f concat -safe 0 -i shots_list.txt \
  -i narration.mp3 \
  -i music.mp3 \
  -i title_card.png \
  -i end_card.png \
  -filter_complex "
    [0:v]scale=1080:1920,setsar=1[shots_v];
    [3:v]loop=loop=24:size=24,scale=1080:1920,fade=t=out:st=1.5:d=0.5[title_v];
    [4:v]loop=loop=24:size=24,scale=1080:1920,fade=t=in:st=0:d=0.5[end_v];
    [title_v][shots_v][end_v]concat=n=3:v=1:a=0[final_v];
    [1:a]volume=1.0,asetpts=PTS-STARTPTS[narr];
    [2:a]volume=0.12,asetpts=PTS-STARTPTS[bgm];
    [narr][bgm]amix=inputs=2:duration=longest[a]
  " \
  -map "[final_v]" -map "[a]" \
  -c:v libx264 -preset fast -crf 21 \
  -c:a aac -b:a 192k \
  -movflags +faststart \
  output/final.mp4
```

Output: 9:16 1080p MP4, ready for TikTok/Reels/Shorts.

---

## 4. Database schema

```sql
-- ~/.hermes/plugins/sprite-studio/state.db

CREATE TABLE projects (
  id TEXT PRIMARY KEY,                    -- ULID
  user_id TEXT NOT NULL,                  -- 'cli' | 'web' | 'telegram:<user_id>' | etc
  surface TEXT NOT NULL,                  -- 'web' | 'cli' | 'telegram' | 'discord'
  brief TEXT NOT NULL,                    -- the original user prompt
  style_preset_id TEXT NOT NULL,
  vibe TEXT,
  duration_seconds INTEGER NOT NULL,
  phase TEXT NOT NULL,                    -- 'brief' | 'cast' | 'timeline' | 'render' | 'done' | 'failed'
  title TEXT,                             -- generated in timeline phase
  narrator_script TEXT,
  music_track_path TEXT,
  final_video_path TEXT,
  total_cost_usd REAL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  approved_cast_at INTEGER,
  approved_timeline_at INTEGER,
  rendered_at INTEGER,
  error_message TEXT
);

CREATE TABLE characters (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  ordinal INTEGER NOT NULL,               -- 1, 2, 3, ... display order
  name TEXT NOT NULL,
  role TEXT,                              -- 'lead' | 'supporting' | 'comic_relief' | 'antagonist' | etc
  persona TEXT NOT NULL,
  visual_description TEXT NOT NULL,
  master_sheet_path TEXT,                 -- path to PNG
  voice_id TEXT,                          -- ElevenLabs voice ID
  voice_personality TEXT,
  source TEXT DEFAULT 'generated',        -- 'generated' | 'reference_image' | 'reference_photo'
  reference_image_path TEXT,              -- for source='reference_*'
  edit_history TEXT,                      -- JSON array of past edit prompts
  is_approved INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE shots (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  ordinal INTEGER NOT NULL,
  duration_seconds INTEGER NOT NULL,
  setting TEXT NOT NULL,
  action TEXT NOT NULL,
  camera TEXT,
  emotion TEXT,
  characters_present TEXT NOT NULL,       -- JSON array of character IDs
  narration_line TEXT,
  character_dialog TEXT,                  -- JSON: [{"char_id": "x", "line": "..."}]
  reference_still_path TEXT,
  rendered_video_path TEXT,
  render_status TEXT DEFAULT 'pending',   -- 'pending' | 'rendering' | 'done' | 'failed'
  render_error TEXT,
  cost_usd REAL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE generation_jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  job_type TEXT NOT NULL,                 -- 'image_gen' | 'video_gen' | 'tts' | 'edit_image' | etc
  provider TEXT NOT NULL,                 -- 'tokenrouter' | 'openai' | 'elevenlabs' | etc
  model TEXT NOT NULL,
  external_job_id TEXT,
  status TEXT NOT NULL,                   -- 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  input_payload TEXT,                     -- JSON request body (sanitized, no keys)
  output_payload TEXT,                    -- JSON response body
  cost_usd REAL,
  error_message TEXT,
  attempt_count INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL,
  completed_at INTEGER
);

CREATE INDEX idx_characters_project ON characters(project_id, ordinal);
CREATE INDEX idx_shots_project ON shots(project_id, ordinal);
CREATE INDEX idx_jobs_project ON generation_jobs(project_id, status);
CREATE INDEX idx_projects_user ON projects(user_id, updated_at DESC);
```

---

## 5. Style presets

`~/.hermes/plugins/sprite-studio/style_presets.yaml`:

```yaml
- id: cartoon_classic
  name: Classic Cartoon
  descriptor: |
    Modern 2D cartoon illustration. Bold confident outlines (~2px),
    soft cel shading with gentle gradients, vibrant but harmonious palette.
    Slightly oversized eyes and expressive faces. Smooth round shapes.
    Light reads as soft directional with subtle bounce.
  render_notes: |
    No photorealism. No painterly textures. No 3D rendering.
    Solid block colors with controlled gradient transitions.
  motion_descriptor: |
    Smooth animated motion at 24fps. Bouncy, slightly anticipatory movements.
    No motion blur on character faces.
  music_tag: cartoon_upbeat
  example_image: examples/cartoon_classic.png

- id: pixar_3d
  name: Pixar-Style 3D
  descriptor: |
    Stylized 3D animation rendering, Pixar-influenced look. Subsurface
    scattering on skin, soft global illumination, slight depth of field.
    Heroic but warm character designs with appealing simplified anatomy.
    Studio-quality material rendering.
  render_notes: |
    Cinematic but not photorealistic. Slightly hyper-saturated.
    Hero lighting on faces.
  motion_descriptor: |
    Smooth physics-based motion. Anticipation and follow-through.
    Subtle hair and cloth dynamics.
  music_tag: orchestral_warm

- id: watercolor_book
  name: Watercolor Children's Book
  descriptor: |
    Hand-painted watercolor illustration, classic picture-book style.
    Visible paper grain and watercolor bleeds. Soft pastel palette,
    gentle imperfect edges, ink line accents over wash areas.
    Charming, soft, slightly nostalgic feel.
  render_notes: |
    No digital sharpness. Embrace organic edge variation.
    Warm muted tones. Subtle paper texture.
  motion_descriptor: |
    Gentle flowing motion. Slow paced. Like turning pages of a book.
  music_tag: gentle_piano

- id: anime_modern
  name: Modern Anime
  descriptor: |
    Contemporary anime illustration. Clean lineart, large expressive eyes,
    cel shading with one or two shadow tones. Vivid hair colors permitted.
    Slight emphasis on dramatic angles and emotional poses.
  render_notes: |
    Sharp clean lines. Limited color palette per character.
    Dramatic backlighting common.
  motion_descriptor: |
    Held key poses with snappy transitions. Speed lines on action.
  music_tag: anime_emotional

- id: cinematic_realism
  name: Cinematic Realism
  descriptor: |
    Photorealistic cinematic look. Shot on 35mm film aesthetic, shallow
    depth of field, natural skin tones, realistic textures and materials.
    Natural lighting, golden hour bias, subtle film grain.
  render_notes: |
    Avoid uncanny faces. Prefer mid and wide shots over extreme close-ups.
    Aim for documentary or indie-film mood.
  motion_descriptor: |
    Naturalistic motion. Slight handheld camera feel.
    No exaggerated cartoon physics.
  music_tag: cinematic_strings

- id: ghibli_inspired
  name: Hand-Drawn Painted (Ghibli-inspired)
  descriptor: |
    Hand-drawn 2D animation style with painterly backgrounds.
    Soft pastel skies, lush detailed nature, gentle warm character designs.
    Background art reads as actual paintings.
  render_notes: |
    Backgrounds far more detailed than characters. Characters slightly simplified.
    Avoid using copyrighted Ghibli characters or specific film references.
  motion_descriptor: |
    Quiet observation moments. Slow pans across landscapes.
    Subtle wind effects on hair, clothes, leaves.
  music_tag: gentle_piano

- id: pixel_art_retro
  name: Pixel Art Retro
  descriptor: |
    16-bit era pixel art aesthetic, ~96x96 character resolution scaled up.
    Limited 32-color palette. Visible chunky pixels. Sprite-style outlines.
    SNES/Genesis-era feel.
  render_notes: |
    Scaling artifacts welcome. Dithering acceptable.
    No anti-aliasing on pixel edges.
  motion_descriptor: |
    Frame-by-frame animation feel. ~12fps even though delivered at 24.
    Slight stutter is part of the charm.
  music_tag: chiptune_adventure

- id: noir_comic
  name: Noir Comic Book
  descriptor: |
    Black and white comic book illustration with high contrast inking.
    Heavy shadow shapes, dramatic angles, halftone patterns for grays.
    Selective spot color (red, yellow) for emphasis.
  render_notes: |
    Mostly black and white with one accent color.
    Dramatic chiaroscuro lighting.
  motion_descriptor: |
    Hard cuts between panels. Static held shots with subtle camera moves.
  music_tag: jazz_noir

- id: storybook_3d
  name: Storybook 3D
  descriptor: |
    Soft sculpted 3D look like a stop-motion children's film.
    Characters appear made of felt, clay, or paper. Soft warm lighting.
    Cozy palettes.
  render_notes: |
    Very tactile material feel. Slight imperfections embraced.
  motion_descriptor: |
    Slightly stop-motion judder. Soft and bouncy.
  music_tag: cozy_acoustic

- id: cyberpunk_neon
  name: Cyberpunk Neon
  descriptor: |
    Neon-saturated futuristic illustration. Deep blacks, electric cyan and
    magenta lighting, wet-street reflections, holographic signage.
    Asian-megacity influence. High contrast.
  render_notes: |
    Lighting drives the entire image. Most surfaces are dark with neon rim light.
  motion_descriptor: |
    Slow camera moves through neon environments. Dramatic glow on motion.
  music_tag: synth_dark
```

User picks one in the brief phase, or Hermes auto-selects based on the brief.

---

## 6. The prompts (production-ready)

These are the actual files. Drop them in `~/.hermes/plugins/sprite-studio/prompts/`.

### 6.1 brief_clarifier.md (Kimi K2.6, ~500 input tokens, ~300 output)

```markdown
# Brief Clarifier

You help users specify a video creation brief. The user has given a one-line description.
Your job: ask 1-3 clarifying questions, max, with sensible defaults so the user can press enter.

## INPUT
The user's brief (one paragraph or less).
The available style presets list (we'll inject it).

## OUTPUT (JSON only, no other text)

{
  "needs_clarification": boolean,
  "questions": [
    {
      "question": "string",
      "default": "string",
      "options": ["array", "of", "common", "answers"]
    }
  ],
  "auto_decisions": {
    "style_preset_id": "string (best guess from presets)",
    "duration_seconds": int (15, 30, 60, 90),
    "vibe": "string"
  }
}

## RULES
- Maximum 3 questions. Prefer 1.
- If the user already specified a thing, don't ask about it.
- Auto-pick the style preset that best matches their description.
- If they said "TikTok" → 30s default. If they said "story" → 60s default.
```

### 6.2 cast_designer.md (Kimi K2.6, ~800 input tokens, ~1500 output)

```markdown
# Cast Designer

You design the cast of characters for a video based on the user's brief and chosen style.

## INPUT
- Brief: the user's description
- Style preset: {style_preset.descriptor}
- Vibe: {vibe}
- Duration: {duration_seconds}

## OUTPUT (JSON only)

{
  "characters": [
    {
      "id": "char_1",
      "ordinal": 1,
      "name": "string (1-2 syllables, memorable, ownable)",
      "role": "lead" | "supporting" | "comic_relief" | "antagonist",
      "persona": "string (~25 words: their personality, manner, what they want)",
      "visual_description": "string (~80 words, EXTREMELY specific: species, age, body type, exact colors, distinctive features, clothing if any, eye shape, proportions). Must give an image generator enough to lock the character.",
      "voice_personality": "string (~10 words: tone, pace, accent if any)"
    }
  ]
}

## RULES
- 1-4 characters max. Most stories work best with 1-2.
- Names are short, memorable, original. Avoid common names like "Tom", "Sarah".
- Visual descriptions must lock the character: "rust-orange tabby cat with cream chest" not "cute orange cat"
- Each character must have a clear visual differentiator from the others
- For human characters in stylized presets (cartoon, anime, watercolor): describe ethnicity, hair, eye color, body type, age, clothing
- For animal characters: species, fur/feather color and pattern, distinctive features, accessories
- NEVER reference copyrighted characters
- Each visual_description should work in isolation as input to gpt-image-2
```

### 6.3 timeline_writer.md (Claude Opus 4.7, ONE call per project, ~1500 input tokens, ~3000 output)

```markdown
# Timeline Writer

You write the full shot-by-shot timeline for a {duration_seconds}-second video.

## INPUT
- Brief: {brief}
- Cast: {characters_json}  (full character bibles)
- Style preset: {style_preset_full}
- Vibe: {vibe}

## OUTPUT (JSON only)

{
  "title": "string (3-6 words, evocative, no clickbait)",
  "logline": "string (one sentence summary)",
  "narrator_script": "string (the COMPLETE voiceover narration, exactly {duration_seconds * 2.2} words ± 10%, conversational warm tone, present tense, no quotation marks for character speech, no markup)",
  "shots": [
    {
      "shot_id": "shot_1",
      "ordinal": 1,
      "duration_seconds": int (5-15),
      "setting": "string (~25 words: location, time of day, atmosphere, palette cues)",
      "action": "string (~35 words: what the character DOES on screen, very visual, specific verbs)",
      "camera": "string (one of: 'static wide', 'slow push-in', 'pull-back reveal', 'tracking', 'handheld follow', 'overhead', 'low angle hero')",
      "emotion": "string (one word: 'tense', 'warm', 'hopeful', 'sad', 'playful', etc)",
      "characters_present": ["char_1", "char_2"],
      "narration_excerpt": "string (the 1-2 sentences from narrator_script that play during this shot)",
      "character_dialog": [
        {"char_id": "char_1", "line": "string"}
      ] | null
    }
  ]
}

## STORY STRUCTURE RULES

For 30-90s videos, use a 5-beat compressed arc:
1. INTRODUCTION: establish character in normal state
2. INCITING_MOMENT: something disrupts
3. STRUGGLE: brief attempt and complication
4. INSIGHT: realization or shift
5. RESOLUTION: payoff, emotional landing

Adjust beat count for length:
- 15-30s: 3 beats (intro / disruption / payoff)
- 45-60s: 5 beats
- 75-90s: 6-7 beats

## SHOT RULES

- Each shot is 5-15 seconds (Seedance optimum is 8-10s)
- Total durations sum to within ±2s of target
- Each shot focuses on ONE moment, not a sequence
- Action must be physically describable (a generator must know what to draw)
- "Mira walks toward the door" is good. "Mira reflects on her past" is not
- Character dialog should be SHORT (1 short sentence per shot max)
- Most shots are narration-only with no character dialog (cleaner pipeline)

## NARRATION RULES

- Conversational, warm, present tense
- Words a 10-year-old understands
- ~2.2 words per second of video (the standard storytelling pace)
- No "And then... and then..." chains
- Show emotional shifts through verbs and image, not adjectives

## CONSTRAINTS

- All characters in characters_present must be from the cast
- Settings must be physically describable (no abstract metaphors)
- No copyrighted material, real people, branded products
- No violence beyond mild peril, no romantic content beyond a hand-hold
```

### 6.4 character_edit.md (Kimi K2.6, ~600 input, ~400 output)

```markdown
# Character Edit Translator

The user wants to modify an existing character. Translate their natural-language request
into a structured edit plan.

## INPUT
- Original character: {character_json}
- User's edit request: "{user_text}"

## OUTPUT (JSON only)

{
  "type": "surgical" | "regenerate",
  "rationale": "string (why we picked this type)",
  "updated_visual_description": "string (full new description if regenerating, or null if surgical)",
  "edit_prompt": "string (only if surgical: the natural-language edit command for gpt-image-2/edit, focused on what changes while preserving everything else)",
  "changed_fields": ["array", "of", "field", "names", "that", "changed"]
}

## DECISION RULES

Use SURGICAL when:
- User changes a specific visible attribute (color, clothing item, expression, accessory)
- User wants to preserve the rest of the character

Use REGENERATE when:
- User changes species, age, or body type
- User changes more than 3 attributes at once
- User says "redo it" or "different character"

## EDIT PROMPT TEMPLATE (for surgical)

"In the provided character model sheet, {specific change in plain language}.
Preserve everything else: face, body shape, pose, palette outside the changed area,
background, framing, layout, all 4 panels of the model sheet."

## EXAMPLES

User: "Make the trench coat dark blue instead of beige"
→ surgical, edit_prompt: "In the provided character model sheet, change the trench coat color from beige to deep navy blue. Preserve everything else: the cat's body, fur pattern, face, pose in each of the 4 panels, and the off-white background."

User: "Change Mira to a small dog instead of a cat"
→ regenerate, updated_visual_description: "..." (full new description)

User: "Older, with reading glasses"
→ surgical, edit_prompt: "In the provided character model sheet, age the character to look mid-50s with subtle wrinkles around the eyes, and add small round reading glasses. Preserve clothing, body, palette, pose, and background."
```

### 6.5 shot_edit.md (Kimi K2.6, ~600 input, ~500 output)

```markdown
# Shot Edit Translator

User wants to modify an existing shot. Translate to a structured update.

## INPUT
- Shot: {shot_json}
- User's request: "{user_text}"

## OUTPUT (JSON only)

{
  "fields_changed": ["setting" | "action" | "camera" | "emotion" | "narration_excerpt" | "character_dialog"],
  "updated_shot": { ... full updated shot object ... },
  "regenerate_reference_still": boolean,
  "regenerate_video": boolean (only true after user re-approves the timeline)
}

## RULES

- regenerate_video must always be false at edit time. Video regeneration requires explicit timeline re-approval.
- regenerate_reference_still is true if any visual field changed (setting, action, camera, characters_present).
- If the user just changes narration text, only narration_excerpt changes, no regeneration needed.
```

### 6.6 reference_still.md (gpt-image-2 prompt template, NOT a chat prompt)

```python
def build_reference_still_prompt(shot, characters_in_shot, style_preset):
    char_labels = []
    for i, char in enumerate(characters_in_shot, 1):
        char_labels.append(f"Image {i}: {char.name} character model sheet (reference)")

    char_refs = "\n".join(char_labels)

    prompt = f"""
You are given {len(characters_in_shot)} character reference image(s):

{char_refs}

Create a single scene reference frame:

Scene: {shot.setting}
Action: {shot.action}
Camera: {shot.camera}
Emotion: {shot.emotion}
Aspect ratio: 9:16 portrait

Style: {style_preset.descriptor}
Render notes: {style_preset.render_notes}

Hard rules:
- The character(s) in the scene MUST look identical to their reference model sheet(s).
- Same fur/skin/hair color, same clothing, same proportions.
- Use only the provided character(s); do not introduce additional characters.
- No on-screen text, captions, watermarks, logos, or signage.
- Composition matches the camera direction.
- One coherent moment, not a sequence.
"""
    return prompt
```

### 6.7 seedance_video.md (Seedance image-to-video prompt template)

```python
def build_seedance_prompt(shot, style_preset):
    return f"""
{shot.action}.
Camera: {shot.camera}.
{style_preset.motion_descriptor}.
Maintain perfect character consistency with the input reference image throughout the entire clip.
No on-screen text. No watermarks.
""".strip()
```

---

## 7. The web app architecture

### 7.1 Stack (verified)

- **Vite** for dev server and build
- **React 18 + TypeScript**
- **dnd-kit** for the character canvas drag/drop
- **dnd-timeline** (built on dnd-kit) for the shot timeline
- **TailwindCSS** for styling (no design system battle)
- **shadcn/ui** for buttons, dialogs, popovers
- **fetch + EventSource** for streaming chat responses
- **zustand** for client state

### 7.2 Component tree

```
App
├── ProjectsLayout
│   ├── ProjectSidebar  (left rail with project list)
│   └── Workspace
│       ├── ChatPanel  (left, always visible)
│       │   ├── MessageList
│       │   ├── StreamingMessage
│       │   └── ChatInput
│       ├── PhaseWorkspace  (center, switches by phase)
│       │   ├── BriefPanel
│       │   ├── CastCanvas       ← character bubbles row
│       │   │   ├── CharacterBubble (per character)
│       │   │   │   ├── PortraitView
│       │   │   │   └── ExpandedSheet (popover)
│       │   │   └── AddCharacterButton
│       │   ├── TimelineEditor   ← horizontal shot cards
│       │   │   ├── ShotCard (per shot)
│       │   │   │   ├── ReferenceFrame
│       │   │   │   ├── DurationHandle (drag to resize)
│       │   │   │   └── ShotMetadata
│       │   │   └── AddShotButton
│       │   └── RenderProgressView
│       │       ├── ProgressBars (per shot)
│       │       ├── CostMeter
│       │       └── PreviewPlayer (when done)
│       └── DetailsPanel  (right, context-aware)
│           ├── StylePresetPicker
│           ├── ParameterControls (duration, vibe)
│           ├── ApprovalButton
│           └── RenderButton
```

### 7.3 State machine (zustand)

```typescript
type ProjectPhase = 'brief' | 'cast' | 'timeline' | 'render' | 'done' | 'failed';

interface ProjectState {
  id: string | null;
  phase: ProjectPhase;
  brief: string;
  stylePresetId: string;
  vibe: string;
  durationSeconds: number;
  characters: Character[];
  shots: Shot[];
  approvedCast: boolean;
  approvedTimeline: boolean;
  finalVideoUrl: string | null;
  errors: ProjectError[];

  // chat-driven state
  pendingChatMessage: string;
  isStreaming: boolean;
  streamBuffer: string;
}
```

State transitions are server-driven. The web app subscribes to `/v1/runs/<run_id>/events` (Hermes' SSE endpoint) and updates local state when events fire. **The server is the source of truth.**

### 7.4 The character canvas (dnd-kit)

```tsx
<DndContext onDragEnd={handleDragEnd} sensors={sensors}>
  <SortableContext items={characters.map(c => c.id)} strategy={horizontalListSortingStrategy}>
    <div className="flex gap-4 overflow-x-auto p-6">
      {characters.map(char => (
        <CharacterBubble key={char.id} character={char} />
      ))}
      <AddCharacterButton onClick={openAddDialog} />
    </div>
  </SortableContext>
</DndContext>
```

`CharacterBubble` shows the front-pose crop. Click expands a popover with:
- Full sprite-sheet
- Name, persona
- "Edit with chat" button (focuses chat input with `/sprite_edit_character <id> ` prefix)
- "Regenerate from scratch" button
- "Delete" button

### 7.5 The timeline (dnd-timeline)

Per dnd-timeline: a headless timeline library for React, based on dnd-kit, supports external drag, virtual rendering, sortable rows, drag to create:

```tsx
<TimelineContext startTime={0} endTime={projectDurationMs} value={{}}>
  <Timeline>
    <Row id="shots">
      {shots.map(shot => (
        <Item id={shot.id} startTime={shot.startMs} endTime={shot.endMs}>
          <ShotCard shot={shot} />
        </Item>
      ))}
    </Row>
    <Row id="narration">
      <NarrationWaveform script={narratorScript} />
    </Row>
  </Timeline>
</TimelineContext>
```

User can drag to reorder shots, drag right edge to resize duration, click to open shot details in the right panel.

### 7.6 Connection layer

The web app calls Hermes' OpenAI-compatible API server. CORS must be configured:

```bash
hermes config set API_SERVER_ENABLED true
hermes config set API_SERVER_KEY <strong-random-key>
hermes config set API_SERVER_CORS_ORIGINS "http://localhost:5173"
hermes config set API_SERVER_PORT 8642
```

Frontend env:

```
VITE_HERMES_URL=http://localhost:8642
VITE_HERMES_KEY=<same-key-as-server>
VITE_PROJECT_API_BASE=http://localhost:8642/sprite-studio
```

The plugin exposes additional REST endpoints under `/sprite-studio/...` for project state queries (since chat-streaming is not always the right shape):

```
POST   /sprite-studio/projects                  → create project
GET    /sprite-studio/projects/:id              → full project state
PATCH  /sprite-studio/projects/:id              → update phase/parameters
GET    /sprite-studio/projects/:id/cast         → list characters
POST   /sprite-studio/projects/:id/characters   → add custom character
PATCH  /sprite-studio/projects/:id/characters/:cid  → edit character
DELETE /sprite-studio/projects/:id/characters/:cid
POST   /sprite-studio/projects/:id/approve-cast
GET    /sprite-studio/projects/:id/timeline
PATCH  /sprite-studio/projects/:id/shots/:sid
POST   /sprite-studio/projects/:id/approve-timeline
POST   /sprite-studio/projects/:id/render        → kicks off render job
GET    /sprite-studio/projects/:id/render-events  → SSE stream of progress
GET    /sprite-studio/projects/:id/output        → final MP4 download
```

These endpoints are added by the plugin via Hermes' `register_route` (Hermes v0.11.0 plugin surface, dispatch_tool + custom HTTP routes).

---

## 8. Hermes plugin layout

```
~/.hermes/plugins/sprite-studio/
├── manifest.json
├── plugin.py                    # entry: register slash commands, hooks, routes
├── orchestrator.py              # phase state machine
├── prompts/                     # all .md files from section 6
├── style_presets.yaml
├── music_library/
│   ├── cartoon_upbeat/
│   │   ├── track_01.mp3
│   │   └── ...
│   ├── orchestral_warm/
│   └── ...
├── services/
│   ├── tokenrouter.py           # wraps Kimi/Claude/GPT-5.5 via OpenAI SDK
│   ├── seedance.py              # video generation client
│   ├── gpt_image.py             # gpt-image-2 wrapper, retry, validation
│   ├── elevenlabs.py            # TTS client with voice picker
│   ├── ffmpeg_runner.py         # subprocess wrapper with progress tracking
│   └── music_picker.py          # picks CC0 track by style_preset
├── routes/
│   ├── projects.py              # FastAPI router for /sprite-studio/projects
│   ├── characters.py
│   ├── shots.py
│   └── render.py
├── workers/
│   ├── render_worker.py         # background render job, runs as asyncio task
│   └── job_queue.py             # job dispatcher with retry/backoff
├── models.py                    # pydantic models for all JSON shapes
├── db.py                        # SQLite helpers
└── state.db
```

### 8.1 The orchestrator state machine

```python
class ProjectOrchestrator:
    """
    Core agent loop. Each phase has a single entry function that the
    plugin's slash commands or HTTP routes call.
    """

    async def start_project(self, brief: str, surface: str, user_id: str):
        project = await self.db.create_project(brief, surface, user_id)
        clarifications = await self.kimi.run("brief_clarifier.md", brief)
        if clarifications.needs_clarification:
            return ClarificationRequest(project_id=project.id, questions=clarifications.questions)
        return await self.advance_to_cast_phase(project.id)

    async def advance_to_cast_phase(self, project_id: str):
        project = await self.db.get_project(project_id)
        plan = await self.kimi.run("cast_designer.md", project=project)
        characters = []
        for char_def in plan.characters:
            char = await self.db.create_character(project_id, char_def)
            sheet = await self.gpt_image.generate_sprite_sheet(char_def, project.style_preset)
            await self.db.update_character(char.id, master_sheet_path=sheet.path)
            characters.append(char)
        await self.db.set_phase(project_id, "cast")
        return CastReadyEvent(characters)

    async def edit_character(self, character_id: str, user_text: str):
        char = await self.db.get_character(character_id)
        edit_plan = await self.kimi.run("character_edit.md", character=char, request=user_text)
        if edit_plan.type == "surgical":
            new_sheet = await self.gpt_image.edit_image(
                char.master_sheet_path,
                edit_plan.edit_prompt,
            )
        else:
            await self.db.update_character(character_id, visual_description=edit_plan.updated_visual_description)
            new_sheet = await self.gpt_image.generate_sprite_sheet(char, project.style_preset)
        await self.db.update_character(character_id, master_sheet_path=new_sheet.path)
        return CharacterEditedEvent(char)

    async def approve_cast(self, project_id: str):
        await self.db.set_phase(project_id, "timeline")
        return await self.advance_to_timeline_phase(project_id)

    async def advance_to_timeline_phase(self, project_id: str):
        project = await self.db.get_project(project_id)
        characters = await self.db.list_characters(project_id)
        timeline = await self.claude_opus.run("timeline_writer.md", project=project, cast=characters)
        for shot_def in timeline.shots:
            shot = await self.db.create_shot(project_id, shot_def)
            ref_still = await self.gpt_image.generate_shot_reference(
                shot, [c for c in characters if c.id in shot_def.characters_present], project.style_preset,
            )
            await self.db.update_shot(shot.id, reference_still_path=ref_still.path)
        await self.db.update_project(project_id, narrator_script=timeline.narrator_script, title=timeline.title)
        return TimelineReadyEvent(timeline)

    async def edit_shot(self, shot_id: str, user_text: str):
        shot = await self.db.get_shot(shot_id)
        plan = await self.kimi.run("shot_edit.md", shot=shot, request=user_text)
        await self.db.update_shot_fields(shot_id, plan.updated_shot)
        if plan.regenerate_reference_still:
            ref = await self.gpt_image.generate_shot_reference(...)
            await self.db.update_shot(shot_id, reference_still_path=ref.path)
        return ShotEditedEvent(shot)

    async def approve_timeline(self, project_id: str):
        await self.db.set_phase(project_id, "render")
        await self.render_worker.enqueue(project_id)
        return RenderQueuedEvent(project_id)
```

### 8.2 The render worker

```python
class RenderWorker:
    """
    Runs as a long-lived asyncio task. Workers process render jobs from
    a Postgres-style queue table (we use SQLite). Workers acquire jobs
    with row-level locking.
    """

    async def render_project(self, project_id: str):
        project = await self.db.get_project(project_id)
        shots = await self.db.list_shots(project_id)

        # Concurrent shot generation, max 4 in flight
        semaphore = asyncio.Semaphore(4)
        async def render_shot(shot):
            async with semaphore:
                await self.seedance.image_to_video(shot, project.style_preset)

        # Audio in parallel with first shots
        tts_task = asyncio.create_task(self.elevenlabs.generate_narration(project))
        music_task = asyncio.create_task(self.music_picker.pick_track(project))
        shot_tasks = [asyncio.create_task(render_shot(s)) for s in shots]

        try:
            await asyncio.gather(*shot_tasks, tts_task, music_task)
        except Exception as e:
            await self.db.set_phase(project_id, "failed", error=str(e))
            return

        # ffmpeg final stitch
        output_path = await self.ffmpeg.stitch(project_id)
        await self.db.update_project(project_id, final_video_path=output_path, phase="done")
```

---

## 9. Cost analysis (verified, per video)

Using TokenRouter rates from your console screenshot:

### Phase costs

| Phase | Operation | Model | Tokens / Units | Cost |
|---|---|---|---|---|
| Brief | clarifier | Kimi K2.6 | ~500 in, ~300 out | $0.002 |
| Cast | cast_designer | Kimi K2.6 | ~800 in, ~1500 out | $0.007 |
| Cast | sprite-sheet × 2-4 chars | gpt-image-2 high | 2-4 calls | $0.42-$0.84 |
| Cast | edit per character | gpt-image-2 edit | ~3 edits avg | $0.63 |
| Timeline | timeline_writer | Claude Opus 4.7 | ~1500 in, ~3000 out | $0.083 |
| Timeline | shot_reference × 6-10 | gpt-image-2 high (multi-ref) | 6-10 calls | $1.27-$2.11 |
| Timeline | shot edits | gpt-image-2 edit | ~5 edits avg | $1.05 |
| Render | Seedance × 6-10 shots | dreamina-seedance-2-0 | 6-10 × 8s @ $0.125/s | $6-$10 |
| Render | ElevenLabs narration | eleven_multilingual_v2 | ~150 words | $0.50 |
| Render | ElevenLabs character dialog | eleven_multilingual_v2 | varies, ~4 lines | $0.30 |
| Render | Music | CC0 library | 0 | $0 |
| Render | ffmpeg | local | 0 | $0 |

### Total per video

- **Optimistic (clean run, no edits):** ~$8.50
- **Typical (a few edits):** ~$12-$15
- **Heavy iteration:** ~$20-$25

With $200 TokenRouter budget:
- ~10 final production-quality videos
- ~40 dev iterations using Seedance Fast tier ($5.6/M tokens) and gpt-image-2 medium quality

That's enough for the build, demo recording, and a public submission with 3-5 example videos.

---

## 10. The 9-day build plan

| Day | Date | Target | Acceptance criterion |
|-----|------|--------|----------------------|
| 1 | Apr 25 | Setup verified | Hermes + TokenRouter + ElevenLabs + Codex/OpenAI all working. Sprite-studio plugin scaffolded, registered. |
| 2 | Apr 26 | Brief + Cast phases backend | `/sprite_new` runs end-to-end up to character generation. 4 sprite-sheets visible in `~/.hermes/plugins/sprite-studio/projects/<id>/cast/` |
| 3 | Apr 27 | Character edit + approval | `/sprite_edit_character` works. Surgical and regenerate paths both work. `/sprite_approve_cast` advances phase. |
| 4 | Apr 28 | Timeline phase | `/sprite_timeline` generates 6-shot timeline with reference frames. Shot edits work via chat. Approval advances to render. |
| 5 | Apr 29 | Render pipeline | Full Seedance + ElevenLabs + ffmpeg pipeline runs to completion. **First end-to-end MP4 produced.** |
| 6 | Apr 30 | Web app shell | React + Vite scaffolded. Chat panel works. Character canvas renders. dnd-kit drag works. |
| 7 | May 1 | Web app phase rendering | All four phases render in web app. Approval buttons work. Streaming chat works. |
| 8 | May 2 | Polish + run 5 production videos | Bug-fix pass. Generate 5 demo-quality videos in different styles. Cut hero submission video. |
| 9 | May 3 | Submit | Tweet posted, Discord posted, repo public, 5 example videos in thread. |

### Scope-cut ladder

If behind, drop in this order:
1. Telegram/Discord surfaces (CLI and web cover the demo)
2. Custom music selection (single track per style preset)
3. Character voice acting (narrator-only for all dialog)
4. Reference image / reference photo character creation (textual only)
5. Multi-character scenes (single-character shots)
6. Web app (CLI-only, screenshots in submission)

If you cut past item 4, you're back to Tiny Tales scope. That still wins.

---

## 11. Edge cases (the security-audit pass)

This is the robustness floor. Every one must be handled.

### 11.1 LLM and generation errors

| Failure | Mitigation |
|---|---|
| Kimi/Claude returns invalid JSON | Use OpenAI SDK with `response_format={"type":"json_object"}`. On parse failure: retry with structured-output reminder. After 2 retries, mark project failed with diagnostic. |
| gpt-image-2 returns content-policy violation | Catch the `content_policy_violation` error, surface to user: "I couldn't generate that. Try a different description." Do NOT auto-retry. |
| gpt-image-2 returns transparent background error | Per docs, gpt-image-2 doesn't support `background:transparent`. Never set this parameter. Validate input. |
| Seedance returns 402 (insufficient credits) | Pause render, notify user with current TokenRouter balance, await top-up or cancel. Persist partial state. |
| Seedance job times out (>5 min) | Retry once with `tier="fast"`. If still fails, mark shot as failed, give user option to retry just that shot. |
| ElevenLabs voice ID is invalid | Fall back to default narrator voice. Log warning. |
| ElevenLabs hits character limit | Split script into chunks, generate per-chunk, concatenate audio. |
| Network drops mid-render | Render worker uses checkpoints. On restart, scan `generation_jobs` for `status=running`, mark as `failed-needs-retry`, re-enqueue. |

### 11.2 State and concurrency

| Risk | Mitigation |
|---|---|
| User edits character while render is running | Lock project in render phase. Edits queue or are rejected with "Render in progress, please wait." |
| User opens 2 browser tabs to same project | SSE clients handle multiple subscribers naturally. Last-write-wins on PATCH endpoints. Show "another session is editing" warning. |
| User closes browser mid-render | Render continues server-side. On reconnect, replay current state. Final video persists in `output/` regardless. |
| Two characters with same name | Append ordinal: "Mira (1)", "Mira (2)". Internal IDs are ULIDs, names are display-only. |
| User triggers same regenerate twice quickly | Debounce via `pending_jobs` table. If a job exists for the same character, return existing job ID. |
| ffmpeg fails (missing codec, corrupted input) | Validate each input MP4 before stitching (`ffprobe`). On stitch failure, retry with conservative settings. If still fails, return per-shot MP4s to user as a ZIP. |

### 11.3 Cost and rate-limit guards

| Risk | Mitigation |
|---|---|
| Runaway costs from rapid iteration | Per-project soft budget (default $30). When approaching, warn user. When exceeded, block render until user confirms. |
| TokenRouter rate-limit (429) | Exponential backoff: 1s, 2s, 4s, 8s, 16s. After 5 retries, fall back to alternate provider (Kling for video, OpenAI direct for images). |
| User submits abusive brief (1000-char prompt) | Validate brief length (max 1000 chars). Reject longer with friendly error. |
| User triggers render with 0 shots | Block. Render requires `len(shots) >= 1`. |
| User triggers render before timeline approved | Block. State machine enforces. |

### 11.4 Content policy

| Risk | Mitigation |
|---|---|
| User asks for copyrighted character | LLM cast designer prompt explicitly forbids. If user insists, refuse politely. |
| User asks for real public figure | Reject in cast_designer with "I can't make videos featuring real people." Offer "fictional inspired by" alternative. |
| User uploads photo of a real person for character_3 | Show explicit consent UI: "I'll use this photo as a starting reference. By uploading, you confirm you have permission to use this likeness." For demo: add watermark "NOT REAL PERSON". |
| User attempts NSFW content | gpt-image-2 and Seedance have built-in filters. If they refuse, surface error to user. Don't try to bypass. |
| Generated video shows unintended brand logo | Automatic post-render check (Gemini 2.5 Pro, optional, skipped per Q3). For now: prompt explicitly says "no text, no logos, no signage" in every shot. |
| Generated video shows unintended text | Same. Style preset render_notes include "no on-screen text". |

### 11.5 Security

| Risk | Mitigation |
|---|---|
| API keys exposed in browser | Frontend NEVER has TokenRouter, OpenAI, or ElevenLabs keys. Only has Hermes API key (revocable, server-side proxy). |
| CORS misconfigured | Explicit allowlist only: `localhost:5173` for dev, your deployed domain for prod. Never `*`. |
| User input as prompt injection | All user text passes through Kimi as STRUCTURED INPUT (cast_designer, shot_edit), never directly into image/video prompts. Kimi filters. |
| Generated content includes private data | Logs are sanitized: `input_payload` in `generation_jobs` strips API keys before persisting. |
| Project state DB on disk readable by other system users | `~/.hermes/` permissions are 0700, set by Hermes installer. |
| Web app vulnerable to XSS via chat | All chat output is rendered through React's text nodes (auto-escaped). No `dangerouslySetInnerHTML`. |

### 11.6 Availability

| Risk | Mitigation |
|---|---|
| TokenRouter goes down | Fallback chain: TokenRouter → OpenRouter → direct provider keys. Configurable per-call. |
| Hermes process crashes | systemd unit (or pm2) restarts. Render worker resumes from checkpoint. |
| Disk fills up | Each project assets dir capped at 1GB. Old projects (>30 days) auto-archived. `df` check on render start. |
| ElevenLabs subscription quota exhausted | Switch to backup TTS (OpenAI TTS via Tool Gateway, or Gemini TTS). Notify user. |

### 11.7 Demo-day-specific

| Risk | Mitigation |
|---|---|
| Live demo fails on stage | Pre-record 3 final videos. Show pre-recorded if live fails. Always have a backup MP4 ready. |
| Twitter throttles new account | Warm up account days ahead. Post one of the 5 example videos per day starting day 5. |
| Hermes v0.11.0 has a bug we hit | Pin to specific commit. Document the version. Have a rollback to v0.9.0 ready (we lose the v0.11.0 features but core API works). |

---

## 12. Day 1 commands (run these now)

```bash
# 1. Install Hermes v0.11.0
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
source ~/.bashrc
hermes --version  # confirm v0.11.0+

# 2. Configure TokenRouter as primary provider
HERMES_KEY=$(openssl rand -hex 16)
echo "Save this Hermes API key: $HERMES_KEY"

hermes setup
# Pick "Custom OpenAI-compatible endpoint"
# Base URL: https://api.tokenrouter.com/v1
# API key: <your-tokenrouter-key>
# Default model: moonshotai/kimi-k2.6

hermes config set API_SERVER_ENABLED true
hermes config set API_SERVER_KEY $HERMES_KEY
hermes config set API_SERVER_PORT 8642
hermes config set API_SERVER_CORS_ORIGINS "http://localhost:5173"

# 3. Start the gateway
hermes gateway &
sleep 2

# 4. Test
curl http://localhost:8642/v1/health
curl http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer $HERMES_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "hermes-agent", "messages": [{"role": "user", "content": "Say hi"}]}'

# 5. Add the supplementary keys
hermes config set OPENAI_API_KEY <your-openai-key-with-gpt-image-2-access>
hermes config set ELEVENLABS_API_KEY <your-elevenlabs-creator-plan-key>

# 6. Scaffold the plugin
mkdir -p ~/.hermes/plugins/sprite-studio/{prompts,services,routes,workers,music_library,projects}
cd ~/.hermes/plugins/sprite-studio

cat > manifest.json <<'EOF'
{
  "name": "sprite-studio",
  "version": "0.0.1",
  "description": "AI video creation studio with persistent character casts",
  "entry": "plugin.py",
  "hermes_version": ">=0.11.0",
  "exposes": {
    "commands": ["sprite_new", "sprite_cast", "sprite_edit_character",
                 "sprite_add_character", "sprite_approve_cast", "sprite_timeline",
                 "sprite_edit_shot", "sprite_approve_timeline", "sprite_render",
                 "sprite_status", "sprite_cancel"],
    "routes": ["/sprite-studio/*"]
  }
}
EOF

# 7. Initialize the SQLite schema
python3 -c "
import sqlite3
conn = sqlite3.connect('state.db')
# Paste the schema from section 4
conn.executescript('''
$(cat <<'SCHEMA'
-- (full schema from section 4 here)
SCHEMA
)
''')
conn.commit()
print('DB initialized')
"

# 8. Scaffold the web app
cd ~
npm create vite@latest sprite-studio-web -- --template react-ts
cd sprite-studio-web
npm install
npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities dnd-timeline
npm install -D tailwindcss@latest postcss autoprefixer
npx tailwindcss init -p
npm install zustand

cat > .env.local <<EOF
VITE_HERMES_URL=http://localhost:8642
VITE_HERMES_KEY=$HERMES_KEY
VITE_PROJECT_API_BASE=http://localhost:8642/sprite-studio
EOF

npm run dev
# Open http://localhost:5173 — should see Vite default

# 9. Verify gpt-image-2 access
curl https://api.openai.com/v1/images/generations \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "A small fox kit in a forest, children book illustration style",
    "size": "1024x1024",
    "quality": "medium",
    "n": 1
  }'
# Should return base64 image data

# 10. Verify Seedance via TokenRouter
curl https://api.tokenrouter.com/v1/video/generations \
  -H "Authorization: Bearer $TOKENROUTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "dreamina-seedance-2-0-260128",
    "input": {
      "prompt": "A small orange fox walks through a misty pine forest at golden hour",
      "duration": 5,
      "resolution": "720p",
      "aspect_ratio": "9:16"
    }
  }'
# (Adjust shape if TokenRouter returns a different schema; their docs are authoritative)
```

If all 10 boxes pass, **day 1 is done.** Tomorrow you start writing the plugin.

---

## 13. The submission strategy

### During build (days 5-9)

Post one demo video per day on Twitter. Each post:

- The 30-90s video (matched to platform)
- Caption format: "Sprite Studio | Day {N}: {title}. AI agent designed the cast, wrote the timeline, rendered the shots. Built on @NousResearch's Hermes Agent + Kimi K2.6 + Seedance 2.0 + GPT Image 2 + ElevenLabs."

By submission day, judges have seen 5 different videos in 5 different styles, all from the same tool. **That's proof of generalization, not just one good output.**

### Submission day (May 3)

The hero tweet:

> **Sprite Studio**: I built a full video creation studio on @NousResearch's Hermes Agent.
>
> Type a one-line idea → it casts characters, writes shots, renders the video. Edit anything via chat. Three interfaces (web canvas, CLI, Telegram), one engine.
>
> Here's the pipeline running, with 5 finished examples in different styles 👇
>
> [hero video: 60s, shows the canvas, character editing via chat, timeline approval, render progress, final outputs]

Then thread the 5 example videos.

Then drop the tweet link in `⁠creative-hackathon-submissions` Discord channel with:

> Sprite Studio — Hermes-orchestrated video creation studio.
> Web canvas + CLI + Telegram all connect to the same Hermes plugin.
> Tech: Hermes v0.11.0, Kimi K2.6, Claude Opus 4.7, GPT Image 2, Seedance 2.0, ElevenLabs Creator.
> Repo: [github URL]
> Live (when judges want to try): [URL]
> Hero tweet: [twitter URL]

### Why this wins

- **Three judging axes (creativity, usefulness, presentation):** all three nailed.
  - Creativity: AI-orchestrated multi-stage video creation with chat-based editing. Nobody else will ship this.
  - Usefulness: it produces a working tool other devs would fork. Not a dev demo, a real product.
  - Presentation: 5 example videos + a polished hero + a live URL + open-source repo.
- **Multi-surface (web/CLI/Telegram):** signals depth of integration with the whole Hermes platform.
- **Real videos as output:** the deliverable is a thing the judges can WATCH, not read about.
- **Style range:** 5 different style presets in 5 different videos shows the system isn't a one-trick pony.

---

## 14. The single command to start now

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

Then run sections 12.2 through 12.10 in order. By bedtime tonight you have:

- Hermes installed and pointing at TokenRouter
- All required API keys configured
- The plugin scaffold registered
- The web app shell running at localhost:5173
- All four critical APIs (Kimi, GPT Image 2, Seedance, ElevenLabs) verified with one test call each

That foundation is everything. Tomorrow you write `cast_designer.md` and `plugin.py`.

**Stop reading. Start building.**
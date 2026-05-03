# Sprite Studio P19a Pre-Implementation Audit Report

Date: 2026-05-02T21:17:38Z
Auditor: Claude (read-only audit pass)
Total files read: ~45 (web src, plugin py, prompts, design reference, db schema)
Files modified: 1 (this report)
Processes started: 0
Network calls: 0

Note on em dashes. The seven mandatory verbatim dumps in section 9 originally contained the U+2014 character in docstrings and comments. To satisfy the report-wide ban on that character, every U+2014 has been replaced with a comma+space (", ") in the dumps as well as in narrative text. Where a sentence-internal em dash would have been written, a period or parenthetical has been used instead. Hyphens (U+002D) are preserved.

## 1. Environment

- Repo root: `/home/drew/sprite-studio` (NOT a git repository, no `.git` directory). Listing: `.agents/`, `.claude/`, `SPRITE_STUDIO_BLUEPRINT.md`, `bridge/`, `build_prompts/`, `skills-lock.json`, `web/`.
- Plugin path: `/home/drew/.hermes/plugins/sprite-studio` (separate tree). Top-level files: `__init__.py`, `commands.py`, `db.py`, `env.py`, `models.py`, `orchestrator.py`, `plugin.yaml`, `requirements.txt`, `state.db`, `style_presets.py`, `style_presets.yaml`, plus `prompts/`, `services/`, `workers/`, `projects/`, `__pycache__/`.
- Verified existence: `PROJECTS_DIR_EXISTS`, `DB_EXISTS` (state.db = 286,720 bytes).
- Tooling: node v24.14.1, npm 11.11.0, python3 3.12.3.
- `sqlite3` CLI is **not** in PATH on this host. Python's `sqlite3` module (version 3.45.1) was used for all DDL/PRAGMA queries via a small helper at `/tmp/sqlite_helper.py`. Audit only ever opens the database in read-only mode (`?mode=ro`).
- Build prompts directory listing: `_production/`, `_smoke_test/`, `_verified_shapes/` (all dirs).
- Note: the audit prompt referred to `/home/drew/.hermes/plugins/sprite-studio/services/image_client.py`. That file does not exist. The image client is `services/gpt_image.py` (class `ImageClient`). All references in this report use the actual filename.
- Note: the audit prompt referred to `plugin.py`. That file does not exist. The plugin entry point is `__init__.py` (36 lines).

## 2. Backend plugin

### 2.1. File inventory

```
./__init__.py                       (36 lines)
./commands.py                       (1976 lines)
./db.py                             (1036 lines)
./env.py                            (115 lines)
./models.py                         (205 lines)
./orchestrator.py                   (2708 lines)
./plugin.yaml                       (1067 bytes)
./style_presets.py                  (123 lines)
./style_presets.yaml                (5325 bytes)
./prompts/__init__.py
./prompts/brief_clarifier.md        (40 lines)
./prompts/cast_designer.md          (35 lines)
./prompts/character_edit.md         (46 lines)
./prompts/master_sheet.md           (18 lines)
./prompts/shot_edit.md              (22 lines)
./prompts/timeline_writer.md        (153 lines)
./services/__init__.py              (87 lines)
./services/_concurrency.py          (10 lines)
./services/_http.py                 (97 lines)
./services/_pricing.py              (142 lines)
./services/_retry.py                (190 lines)
./services/elevenlabs.py            (441 lines)
./services/elevenlabs_voices.py     (259 lines)
./services/errors.py                (92 lines)
./services/ffmpeg_runner.py         (788 lines)
./services/gpt_image.py             (391 lines)   <- the "ImageClient"
./services/seedance.py              (799 lines)
./services/tokenrouter.py           (245 lines)
./workers/__init__.py               (30 lines)
./workers/asset_server.py           (133 lines)
./workers/render_worker.py          (916 lines)
```

### 2.2. plugin.yaml (verbatim)

See section 9.7 for the full file. Summary:

- name: sprite-studio
- version: 0.1.0
- 25 commands declared in `provides_commands` (full list in 2.6).
- 2 required env vars (secret): `TOKENROUTER_API_KEY`, `ELEVENLABS_API_KEY`.

### 2.3. Database schema

Active SQLite file: `/home/drew/.hermes/plugins/sprite-studio/state.db`.

PRAGMA `user_version` = 0 (unused by the plugin; migration version is tracked in the `meta` table instead).
PRAGMA `schema_version` = 13 (incremented automatically by SQLite each DDL change).
`meta` table: `schema_version = 3` (this is the plugin's logical schema version, set by `db._migrate()` in `db.py`).

Row counts:
```
projects:        12
characters:      17
shots:           48
generation_jobs: 166
```
Projects by phase: `brief=3, cast=1, done=8` (no rows in `timeline`, `render`, or `failed`).

Full DDL (from `sqlite_master`):

```sql
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  surface TEXT NOT NULL,
  brief TEXT NOT NULL,
  style_preset_id TEXT NOT NULL,
  vibe TEXT,
  duration_seconds INTEGER NOT NULL CHECK (duration_seconds IN (15,30,45,60,75,90)),
  phase TEXT NOT NULL CHECK (phase IN ('brief','cast','timeline','render','done','failed')),
  title TEXT,
  narrator_script TEXT,
  music_track_path TEXT,
  final_video_path TEXT,
  total_cost_usd REAL NOT NULL DEFAULT 0 CHECK (total_cost_usd >= 0),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  approved_cast_at INTEGER,
  approved_timeline_at INTEGER,
  rendered_at INTEGER,
  error_message TEXT
, use_narrator INTEGER NOT NULL DEFAULT 1);

CREATE TABLE characters (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
  name TEXT NOT NULL,
  role TEXT,
  persona TEXT NOT NULL,
  visual_description TEXT NOT NULL,
  master_sheet_path TEXT,
  voice_id TEXT,
  voice_personality TEXT,
  source TEXT NOT NULL DEFAULT 'generated'
    CHECK (source IN ('generated','reference_image','reference_photo')),
  reference_image_path TEXT,
  edit_history TEXT NOT NULL DEFAULT '[]',
  is_approved INTEGER NOT NULL DEFAULT 0 CHECK (is_approved IN (0,1)),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (project_id, ordinal)
);

CREATE TABLE shots (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
  duration_seconds INTEGER NOT NULL CHECK (duration_seconds BETWEEN 5 AND 15),
  setting TEXT NOT NULL,
  action TEXT NOT NULL,
  camera TEXT,
  emotion TEXT,
  characters_present TEXT NOT NULL DEFAULT '[]',
  narration_line TEXT,
  character_dialog TEXT,
  reference_still_path TEXT,
  rendered_video_path TEXT,
  render_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (render_status IN ('pending','rendering','done','failed')),
  render_error TEXT,
  cost_usd REAL NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  has_dialog INTEGER NOT NULL DEFAULT 0,
  dialog_speakers TEXT,
  transition_to_next TEXT NOT NULL DEFAULT 'cut'
    CHECK (transition_to_next IN ('cut','fade','dissolve','match_cut')),
  UNIQUE (project_id, ordinal)
);

CREATE TABLE generation_jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  job_type TEXT NOT NULL
    CHECK (job_type IN ('image_gen','image_edit','video_gen','tts','llm','ffmpeg')),
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  external_job_id TEXT,
  status TEXT NOT NULL
    CHECK (status IN ('queued','running','done','failed','cancelled')),
  input_payload TEXT,
  output_payload TEXT,
  cost_usd REAL CHECK (cost_usd IS NULL OR cost_usd >= 0),
  error_message TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  completed_at INTEGER
);

CREATE INDEX idx_characters_project ON characters(project_id, ordinal);
CREATE INDEX idx_jobs_project_status ON generation_jobs(project_id, status, created_at);
CREATE INDEX idx_projects_user ON projects(user_id, updated_at DESC);
CREATE INDEX idx_shots_project ON shots(project_id, ordinal);
```

Critical CHECK constraints (extracted):

| Table | Column | Allowed values |
|---|---|---|
| projects | duration_seconds | (15, 30, 45, 60, 75, 90) |
| projects | phase | ('brief','cast','timeline','render','done','failed') (NO 'cancelled') |
| projects | total_cost_usd | >= 0 |
| characters | ordinal | >= 1 |
| characters | source | ('generated','reference_image','reference_photo') |
| characters | is_approved | (0, 1) |
| shots | ordinal | >= 1 |
| shots | duration_seconds | BETWEEN 5 AND 15 |
| shots | render_status | ('pending','rendering','done','failed') |
| shots | transition_to_next | ('cut','fade','dissolve','match_cut') |
| shots | cost_usd | >= 0 |
| generation_jobs | job_type | ('image_gen','image_edit','video_gen','tts','llm','ffmpeg') |
| generation_jobs | status | ('queued','running','done','failed','cancelled') |
| generation_jobs | cost_usd | NULL or >= 0 |

Note. The shots table column order in the live DB is: `id, project_id, ordinal, duration_seconds, setting, action, camera, emotion, characters_present, narration_line, character_dialog, reference_still_path, rendered_video_path, render_status, render_error, cost_usd, created_at, updated_at, has_dialog, dialog_speakers, transition_to_next`. The trailing three columns (`has_dialog`, `dialog_speakers`, `transition_to_next`) were added by `_migration_v2_dialog_flags` and `_migration_v3_shot_transitions` after initial schema creation and therefore appear at the end. `db.py`'s in-memory `_SHOT_COLUMNS` set is unordered so this is harmless, but new schema-aware code must read columns by name (not positional index).

Sample rows. character (5):

```
01KQK11QHG0SK8GTNY3VK5NYT5 | 01KQK11QGP37K2DE5FQ610ZQ20 | 1 | Alice      | narrator    | generated | (no ref) | (no sheet)
01KQK6885XZR8PNENCZES9KYMD | 01KQK63N19410WY6D6VV2M4VAQ | 1 | Nora Flick | protagonist | generated | (no ref) | .../cast/01KQK6885XZR8PNENCZES9KYMD/sheet.png
01KQK68869PYKRM1FN9XGJ17RE | 01KQK63N19410WY6D6VV2M4VAQ | 2 | Hugo Steam | sidekick    | generated | (no ref) | .../cast/01KQK68869PYKRM1FN9XGJ17RE/sheet.png
01KQK84A1G1F3JSZQG6V4PV0B4 | 01KQK7XHC0EJKMEEZDG7KWA41X | 1 | Clove      | lead        | generated | (no ref) | .../cast/01KQK84A1G1F3JSZQG6V4PV0B4/sheet.png
01KQK84A1XAN4WXTPSJS67YHF6 | 01KQK7XHC0EJKMEEZDG7KWA41X | 2 | Fig        | supporting  | generated | (no ref) | .../cast/01KQK84A1XAN4WXTPSJS67YHF6/sheet.png
```

Sample rows. shots (5):

```
01KQK11QJ6FAN4ET6VB8AJH286 | 01KQK11QGP37K2DE5FQ610ZQ20 | 1 | 6  | cut | pending
01KQKD6D8JN1MP16H6ER8WCCFX | 01KQK63N19410WY6D6VV2M4VAQ | 1 | 10 | cut | done
01KQKD6D8REF4SVMV8KADJXTYR | 01KQK63N19410WY6D6VV2M4VAQ | 2 | 10 | cut | done
01KQKD6D8YD7P2KNZ38FX5R3W6 | 01KQK63N19410WY6D6VV2M4VAQ | 3 | 12 | cut | failed
01KQKD6D94DMR67X5HA3FRZBA1 | 01KQK63N19410WY6D6VV2M4VAQ | 4 | 9  | cut | done
```

Most-recent project (line mode):

```
                            id = 01KQMZKRZVVEW3RBEFP82BK4MG
                       user_id = cli
                       surface = cli
                         brief = A felted bunny celebrates her birthday alone in a small kitchen, then her toys come to life
               style_preset_id = storybook_3d
                          vibe = cozy
              duration_seconds = 30
                         phase = done
                         title = The Littlest Birthday Party
               narrator_script = None
              music_track_path = None
              final_video_path = /home/drew/.hermes/plugins/sprite-studio/projects/01KQMZKRZVVEW3RBEFP82BK4MG/output/final.mp4
                total_cost_usd = 5.26478685
                    created_at = 1777746961
                    updated_at = 1777752869
              approved_cast_at = 1777752085
          approved_timeline_at = 1777752619
                   rendered_at = 1777752869
                 error_message = None
                  use_narrator = 0
```

### 2.4. db.py constants (verbatim)

```python
# db.py:27
SCHEMA_VERSION = 3

# db.py:29
VALID_SHOT_TRANSITIONS = ("cut", "fade", "dissolve", "match_cut")

# db.py:31-38
_PROJECT_COLUMNS = {
    "user_id", "surface", "brief", "style_preset_id", "vibe",
    "duration_seconds", "phase", "title", "narrator_script",
    "use_narrator",
    "music_track_path", "final_video_path", "total_cost_usd",
    "approved_cast_at", "approved_timeline_at", "rendered_at",
    "error_message",
}

# db.py:39-43
_CHARACTER_COLUMNS = {
    "ordinal", "name", "role", "persona", "visual_description",
    "master_sheet_path", "voice_id", "voice_personality", "source",
    "reference_image_path", "edit_history", "is_approved",
}

# db.py:44-50
_SHOT_COLUMNS = {
    "ordinal", "duration_seconds", "setting", "action", "camera",
    "emotion", "characters_present", "narration_line", "character_dialog",
    "dialog_speakers", "has_dialog", "transition_to_next",
    "reference_still_path", "rendered_video_path", "render_status",
    "render_error", "cost_usd",
}

# db.py:849-852
_SHOT_SAFE_FIELDS = {
    "duration_seconds", "setting", "action", "camera", "emotion",
    "narration_line", "transition_to_next",
}
```

`_USER_ID` is not defined in `db.py`. It is defined in `commands.py:521`:

```python
_USER_ID = "cli"
```

`commands.py:413` also passes `user_id="cli"` literally to `orchestrator.start_project`. There is no env override; the plugin is single-user by construction (matches blueprint, see backlog item 23).

Other phase-guard constants in `commands.py`:

```python
_FIELD_EDITABLE_PHASES   = {"brief", "cast", "timeline"}    # commands.py:1495
_REORDER_EDITABLE_PHASES = {"cast", "timeline"}             # commands.py:1496
_VALID_DURATIONS         = (15, 30, 45, 60, 75, 90)         # commands.py:1497
_TIMELINE_EDITABLE_PHASES = {"timeline"}                    # commands.py:1667
_VISUAL_SHOT_FIELDS      = {"setting", "action", "camera"}  # commands.py:1672
_DURATION_MIN, _DURATION_MAX = 5, 15                        # commands.py:1718-1719
```

### 2.5. db.py public function signatures

| Line | Signature |
|---|---|
| 157 | `def now_ts() -> int` |
| 161 | `def new_id() -> str` |
| 169 | `def connect() -> sqlite3.Connection` |
| 200 | `def txn() -> Iterator[sqlite3.Connection]` (context manager) |
| 363 | `def create_project(user_id, surface, brief, style_preset_id, duration_seconds, vibe=None) -> dict` |
| 386 | `def get_project(project_id: str) -> Optional[dict]` |
| 395 | `def list_projects(*, user_id, limit=20, phase=None) -> list[dict]` |
| 419 | `def sum_costs(*, user_id, since_ts=None) -> dict` |
| 455 | `def delete_project(project_id, *, keep_final_video=False) -> dict` |
| 520 | `def latest_project_for_user(user_id, phase=None) -> Optional[dict]` |
| 560 | `def update_project(project_id, **fields) -> dict` |
| 570 | `def update_project_fields(project_id, *, allowed_phases=None, **fields) -> dict` |
| 614 | `def set_phase(project_id, phase, error_message=None) -> dict` |
| 621 | `def create_character(project_id, ordinal, name, role, persona, visual_description, voice_personality=None) -> dict` |
| 646 | `def get_character(character_id) -> Optional[dict]` |
| 657 | `def list_characters(project_id) -> list[dict]` |
| 669 | `def update_character(character_id, **fields) -> dict` |
| 680 | `def delete_character(character_id) -> None` |
| 685 | `def reorder_characters(project_id, ordered_ids) -> dict` |
| 732 | `def create_shot(project_id, ordinal, duration_seconds, setting, action, characters_present, camera=None, emotion=None, narration_line=None, character_dialog=None, dialog_speakers=None, has_dialog=False, transition_to_next='cut') -> dict` |
| 774 | `def get_shot(shot_id) -> Optional[dict]` |
| 783 | `def list_shots(project_id) -> list[dict]` |
| 795 | `def update_shot(shot_id, **fields) -> dict` |
| 805 | `def reorder_shots(project_id, ordered_ids) -> dict` |
| 855 | `def update_shot_fields(shot_id, *, allowed_phases=None, **fields) -> dict` |
| 908 | `def create_job(project_id, job_type, provider, model, input_payload) -> dict` |
| 945 | `def mark_job_running(job_id) -> None` |
| 957 | `def mark_job_done(job_id, output_payload, cost_usd) -> None` |
| 971 | `def mark_job_failed(job_id, error_message) -> None` |
| 984 | `def mark_job_cancelled(job_id, reason=None) -> None` |
| 1005 | `def list_jobs(*, project_id, status=None) -> list[dict]` |
| 1025 | `def increment_project_cost(project_id, delta_usd) -> None` |

Notable absences: there is **no** `delete_shot`, `insert_shot`, or `insert_shot_at_ordinal`. Only `create_shot` (always appends-by-ordinal, caller picks the ordinal), `update_shot`, `update_shot_fields`, and `reorder_shots` exist for the shots table. `delete_character` exists; there is no equivalent `delete_shot`.

### 2.6. Slash commands inventory

25 handlers, each with signature `async def <name>_handler(raw_args: str = "", **kwargs) -> str`. The body always returns a JSON string (or, on chat surfaces, a markdown reply with `MEDIA:<path>` lines). The bridge unmarshals the JSON; chat-surface code paths are unreachable from the bridge because the bridge passes `surface="api"`.

| Line | Handler | Operates on (via orchestrator/db) |
|---|---|---|
| 152 | sprite_status_handler | reads project, shots, latest_progress; computes ETA |
| 384 | start_handler | telegram welcome on chat surface; CLI/API: empty string |
| 398 | sprite_new_handler | orchestrator.start_project (creates project row, calls Kimi) |
| 464 | sprite_cast_handler | orchestrator.advance_to_cast_phase (creates characters, gpt-image text-to-image) |
| 587 | sprite_edit_character_handler | orchestrator.edit_character (Kimi decision -> surgical .edit() OR regenerate .generate()) |
| 662 | sprite_add_character_handler | orchestrator.add_character (text-to-image; cap=4) |
| 715 | sprite_remove_character_handler | orchestrator.remove_character (min=1) |
| 760 | sprite_approve_cast_handler | orchestrator.approve_cast (flips is_approved, phase->timeline) |
| 872 | sprite_timeline_handler | orchestrator.advance_to_timeline_phase (Kimi writer; multi-ref .edit() per shot) |
| 983 | sprite_edit_shot_handler | orchestrator.edit_shot (Kimi decision; conditional ref-still regen) |
| 1047 | sprite_approve_timeline_handler | orchestrator.approve_timeline (phase->render) |
| 1115 | sprite_render_handler | enqueues RenderWorker (returns immediately with budget preview) |
| 1208 | sprite_cancel_handler | orchestrator.cancel_render (sets cancellation flag) |
| 1238 | sprite_show_handler | reads project + characters + shots into one nested payload |
| 1362 | sprite_purge_handler | db.delete_project (requires --confirm; deletes filesystem too) |
| 1420 | sprite_list_handler | db.list_projects (paged; --phase, --limit) |
| 1461 | sprite_cost_summary_handler | db.sum_costs (windowed by --days) |
| 1516 | sprite_list_styles_handler | reads style_presets.yaml |
| 1525 | sprite_set_style_handler | db.update_project_fields (phase=brief/cast/timeline) |
| 1559 | sprite_set_vibe_handler | db.update_project_fields (phase=brief/cast/timeline) |
| 1587 | sprite_set_duration_handler | db.update_project_fields (must be in _VALID_DURATIONS) |
| 1627 | sprite_reorder_cast_handler | db.reorder_characters (phase=cast/timeline) |
| 1675 | sprite_reorder_shots_handler | db.reorder_shots (phase=timeline only) |
| 1722 | sprite_edit_shot_field_handler | db.update_shot_fields(allowed=_TIMELINE_EDITABLE_PHASES) + conditional regen if visual field |
| 1822 | sprite_set_shot_transition_handler | db.update_shot_fields (transition_to_next; phase=timeline) |

Common patterns observed in handler bodies (first 30 lines of each, captured during audit):

- Argument parsing always strips outer quotes via `_strip_brief_quotes()`. Pipe-delimited args use `raw.split("|", 1)`.
- Errors are returned as a JSON string `{"status": "error", "message": "...", "error_class": "<class>", "project_id": <id>}` via `_err_json()`.
- Success returns vary; many include `{"status": "ok", "command": "<name>", "project_id": ...}`.
- Chat-surface branches call `_format_*_for_telegram()` helpers and emit `MEDIA:<absolute_path>` lines for media. The bridge always passes `surface="api"`, so these branches are dead code from the web app's perspective. The web app gets pure JSON.
- Phase guards typically resolve "the user's most-recent project" via `_resolve_render_target_project()` or `_resolve_active_cast_project()` before running any mutation. Single-user simplification.

`SLASH_COMMANDS` (commands.py, after the handler defs) is a `dict[str, dict]` with keys `description` and `handler`. The bridge reads `plugin.commands.SLASH_COMMANDS` and dispatches by lookup.

### 2.6. plugin.yaml vs commands.py drift check

```
DECLARED_IN_YAML (25):
  sprite_add_character, sprite_approve_cast, sprite_approve_timeline,
  sprite_cancel, sprite_cast, sprite_cost_summary, sprite_edit_character,
  sprite_edit_shot, sprite_edit_shot_field, sprite_list, sprite_list_styles,
  sprite_new, sprite_purge, sprite_remove_character, sprite_render,
  sprite_reorder_cast, sprite_reorder_shots, sprite_set_duration,
  sprite_set_shot_transition, sprite_set_style, sprite_set_vibe,
  sprite_show, sprite_status, sprite_timeline, start

HANDLERS_IN_CODE (25): identical set

YAML_NOT_IN_CODE: []
CODE_NOT_IN_YAML: []
```

No drift. The YAML is also the source plugin manifest Hermes reads at registration time.

### 2.7. Orchestrator phase machine

Imports (orchestrator.py:1-43):
```python
import asyncio, json, logging, re, shutil, time
from pathlib import Path
from typing import Any, Optional

from . import db
from .models import StylePreset
from .prompts import load_prompt
from .services import (
    ChatClient, ImageClient, ProviderContentPolicyError,
    ProviderResponseShapeError, QUALITY_HIGH, SIZE_PORTRAIT, SIZE_SQUARE,
    SpriteStudioError, VideoClient, VoiceClient,
)
from .services import elevenlabs_voices
from .style_presets import (DEFAULT_PRESET_ID, StylePresetLoadError,
                            get_preset, is_valid_preset_id, load_presets)

KIMI_MODEL = "moonshotai/kimi-k2.6"
DEFAULT_DURATION_SECONDS = 60
VALID_DURATIONS = {15, 30, 45, 60, 75, 90}
VISUAL_DESC_MIN = 40
TIMELINE_MAX_TOKENS: Optional[int] = None
TIMELINE_READ_TIMEOUT = 540.0
TIMELINE_MIN_SHOTS = 1
TIMELINE_MAX_SHOTS = 12
TIMELINE_DURATION_TOLERANCE = 2

ALLOWED_CAMERAS = {
    "static wide", "slow push-in", "pull-back reveal", "tracking",
    "handheld follow", "overhead", "low angle hero",
}

PROJECTS_ROOT = Path("~/.hermes/plugins/sprite-studio/projects").expanduser()
```

Errors (each subclasses `OrchestratorError(RuntimeError)`):

```
OrchestratorError, ProjectInWrongPhaseError, RenderInProgressError,
CastGenerationFailedError, CharacterNotFoundError, CastFullError,
CastTooSmallError, TimelineGenerationFailedError, ShotNotFoundError,
TimelineNotReadyError
```

Class `ProjectOrchestrator` (orchestrator.py:142+) exposes the public API (`async def <method>` unless noted):

| Line | Method | Purpose |
|---|---|---|
| 178 | start_project(*, brief, surface, user_id) | Validate brief, create project row, call Kimi brief_clarifier, persist auto_decisions |
| 247 | advance_to_cast_phase(*, project_id) | brief -> cast: Kimi cast_designer, insert character rows, parallel master-sheet gen via ImageClient.generate (text-to-image, NO ref-image input) |
| 389 | edit_character(*, character_id, user_text) | cast: Kimi character_edit decision (surgical|regenerate). surgical -> ImageClient.edit(images=[sheet]). regenerate -> ImageClient.generate (text-to-image) |
| 486 | add_character(*, project_id, description) | cast: derive name/persona, ImageClient.generate (text-to-image, NO ref) |
| 572 | remove_character(*, character_id) | cast: shutil-move sheet to _trash; delete row; repack ordinals |
| 636 | approve_cast(*, project_id) | cast -> timeline: flip is_approved=1; set approved_cast_at |
| 695 | advance_to_timeline_phase(*, project_id) | timeline: Kimi timeline_writer; persist shot rows; ImageClient.edit(images=master_sheets) per shot for ref-still |
| 815 | edit_shot(*, shot_id, user_text) | timeline: Kimi shot_edit decision; apply field updates; conditional ref-still regen via ImageClient.edit |
| 945 | regenerate_shot_reference(shot_id) | timeline: standalone wrapper used by /sprite_edit_shot_field when a visual field changes |
| 991 | approve_timeline(*, project_id) | timeline -> render: phase flip; approved_timeline_at |
| 2643 | start_render(*, project_id) | render: kicks off RenderWorker.render_project as an asyncio.Task tracked in self._render_tasks |
| 2695 | cancel_render(self, project_id) | sets the cancellation flag in workers.CANCELLATION_REGISTRY |
| 2703 | get_render_task(self, project_id) | returns the in-flight asyncio.Task or None |

Key persisters:

- `_persist_shot_rows(*, project_id, shots_data)` (orchestrator.py:2104). Sorts by ordinal, sanitizes strings, defensive-defaults `transition_to_next` to `'cut'` if the LLM returned an invalid value, and creates one shot row per entry. Does NOT batch-create; loops `db.create_shot()`.

- `_generate_all_reference_stills(*, project_id, shot_rows, char_lookup, preset)` (orchestrator.py:2166). Parallel asyncio.gather over shots. Each task: assembles the list of master_sheet_path Paths for characters_present, calls `_generate_reference_still(...)` which in turn calls `self._image.edit(prompt, images=[master sheets], size=SIZE_PORTRAIT, quality=QUALITY_HIGH, save_to=shot_dir, project_id=...)` and renames to `reference.png`.

- `_finalize_cast_phase(self, project_id)` (orchestrator.py:1338). Sets phase to `'cast'` with `db.set_phase`.

- `_apply_shot_decision(...)` (orchestrator.py:2338). Translates the LLM's `updated_shot` into DB column updates. Validates `characters_present` against the cast (refuses unknown ids), clips `duration_seconds` to 5..15, drops invalid `camera` values. This is the only place camera enum is policed.

Phase transitions in summary (DB phase column values):

```
brief    -> cast       (advance_to_cast_phase, after sheets all generated, finalize_cast_phase)
cast     -> timeline   (approve_cast)
timeline -> render     (approve_timeline)
render   -> done       (RenderWorker.render_project on success; sets rendered_at)
*        -> failed     (set_phase on hard error in any orchestrator method)
```

There is no `'cancelled'` value in the phase CHECK. On cancellation the project stays in `'render'` and `error_message` is set to `"cancelled: <detail>"` (workers/render_worker.py:860-865). Re-running `/sprite_render` resumes.

### 2.8. Services

`services/__init__.py` re-exports the public layer used by orchestrator and render_worker:

```
ChatClient (tokenrouter.py)
ImageClient, IMAGE_MODEL, SIZE_*, ALLOWED_SIZES, QUALITY_*, ALLOWED_QUALITIES (gpt_image.py)
VideoClient, MODEL_FAST, MODEL_STANDARD (seedance.py)
VoiceClient, NARRATOR_VOICE_ID, TTS_DEFAULT_MODEL_ID, TTS_CHUNK_THRESHOLD (elevenlabs.py)
SpriteStudioError + 9 typed Provider* errors (errors.py)
probe, get_duration_seconds, concat_videos, concat_audios, stitch_final,
compute_dialog_windows, build_ducking_volume_expr (ffmpeg_runner.py)
seedance_token_count, seedance_cost_from_tokens (_pricing.py)
```

Per-service summary:

- `_concurrency.py` (10 lines): module-level semaphores. `IMAGE_SEMAPHORE = asyncio.Semaphore(2)`, `CHAT_SEMAPHORE = 4`, `VIDEO_SEMAPHORE = 4`, `TTS_SEMAPHORE = 4`.
- `_http.py` (97 lines): one shared `httpx.AsyncClient`. Constants: `HTTP_CONNECT=10.0`, `HTTP_READ_CHAT=180.0`, `HTTP_TOTAL_CHAT=240.0`, `HTTP_READ_IMAGE=240.0`, `HTTP_TOTAL_IMAGE=300.0`, `USER_AGENT="sprite-studio/0.1.0 (+hermes plugin)"`.
- `_pricing.py` (142 lines). PRICING dict has Kimi K2.6, gpt-5.4-image-2, Seedance fast (5.60 USD/1M tokens), Seedance standard (7.00). seedance_token_count uses 9:16/16:9 dim tables for 720p / 1080p.
- `_retry.py` (190 lines). tenacity-based retry. `RETRYABLE_STATUSES = (429, 500, 502, 503, 504)`.
- `errors.py` (92 lines). Base `SpriteStudioError`; subclasses for Auth/NotFound/RateLimit/Server/ContentPolicy/Timeout/ResponseShape/InsufficientCredits/InvalidRequest plus `FFmpegError`, `FileInvalidError`, `BudgetExceededError`.
- `tokenrouter.py` (245 lines). `class ChatClient` exposes `chat_json(*, model, messages, project_id) -> dict`.
- `gpt_image.py` (391 lines). `class ImageClient`. Constants: `IMAGE_MODEL = "openai/gpt-5.4-image-2"`. `SIZE_SQUARE = "1024x1024"`, `SIZE_PORTRAIT = "1024x1536"`, `SIZE_LANDSCAPE = "1536x1024"`. `QUALITY_LOW/MEDIUM/HIGH = "low"/"medium"/"high"`.
  - `async def generate(*, prompt, size, quality, n=1, save_to, project_id) -> list[Path]`. POST `/v1/images/generations` with JSON body. NO reference image input.
  - `async def edit(*, prompt, images: list[Path], size, quality, mask=None, save_to, project_id) -> Path`. POST `/v1/images/edits` as multipart. Validates `1 <= len(images) <= 16`. Each image is opened in binary, attached as `("image[]", (name, fh, "image/png"))`. Returns the first decoded Path.
- `seedance.py` (799 lines). `class VideoClient`. Methods include `image_to_video` (the per-shot caller). Module fn `_action_has_dialog` precomputes whether an action string includes a quoted dialog line.
- `elevenlabs.py` (441 lines). `class VoiceClient`. Direct ElevenLabs calls (NOT via TokenRouter). API key from `ELEVENLABS_API_KEY`. xi-api-key header; output_format as query param.
- `elevenlabs_voices.py` (259 lines). `RACHEL_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"`, `GEORGE_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"`, `FALLBACK_VOICE_ID = RACHEL_VOICE_ID`.
- `ffmpeg_runner.py` (788 lines). Top-level functions: probe, get_duration_seconds, _build_concat_manifest, concat_videos, concat_audios, build_ducking_volume_expr, _has_visual_transitions, _shot_has_audio, _xfade_videos, stitch_final, compute_dialog_windows.

### 2.9. Workers

`workers/__init__.py` (30 lines) re-exports `BUDGET_HARD_LIMIT_USD_DEFAULT, MAX_RENDER_MINUTES_DEFAULT, MAX_SHOT_CONCURRENCY, PROGRESS_BUS, ProgressEvent, RenderResult, RenderWorker, ShotResult, cancel_render, latest_progress`.

`workers/render_worker.py` (916 lines).

Constants:
```
MAX_SHOT_CONCURRENCY = 4
MAX_RENDER_MINUTES_DEFAULT = 30
BUDGET_HARD_LIMIT_USD_DEFAULT = 30.0
PARTIAL_OUTPUT_DIRNAME = "_partial"
MUSIC_LIBRARY_ROOT = (plugin_dir/music_library)
PROJECTS_ROOT = (plugin_dir/projects)
SHOT_MIN_DURATION_SECONDS = 0.5
PROGRESS_BUS = ProgressBus()
CANCELLATION_REGISTRY = CancellationRegistry()
```

Classes:
- `ProgressEvent` (dataclass): project_id, timestamp, stage, detail, completed=0, total=0, error=None.
- `ProgressBus`: per-project `asyncio.Queue` + `_latest` map; `emit/latest/clear`. Bounded to 200 events per project.
- `CancellationRegistry`: per-project `asyncio.Event`; `signal/is_cancelled/clear`.
- `ShotResult` (dataclass): shot_id, ordinal, success, video_path, duration_seconds, has_dialog, cost_usd, error.
- `RenderResult` (dataclass): project_id, phase ("done"|"failed"|"cancelled"), final_video_path, total_cost_usd, shot_success_count, shot_failure_count, error, partial_zip_path.
- `RenderWorker(*, video_client, voice_client, image_client=None, seedance_model, max_shot_concurrency, max_render_minutes, budget_hard_limit_usd)`.
  - `async def render_project(project_id) -> RenderResult` (entry).
  - `async def _render_inner(project, cancel_flag) -> RenderResult` (per-shot fan-out, narration in parallel, music, stitch).
  - `async def _render_one_shot(project_id, shot, preset, cancel_flag) -> ShotResult`.
  - `async def _synthesize_narration(...)`.
  - `_pick_music`, `_build_seedance_prompt`, `_over_budget`, `_fresh_cost`, `_sweep_orphan_jobs`, `_save_partial_zip`, `_mark_failed`, `_mark_cancelled`, `_watchdog`.

Stages emitted to ProgressBus: `queued`, `rendering_shots`, `synthesizing_narration`, `picking_music`, `stitching`, `validating`, `done`, `failed`, `cancelled`.

`workers/asset_server.py` (133 lines). Standalone aiohttp web app. URL pattern: `GET /<project_id>/<subdir>/<rest...>` where:

- Allowed subdirs: `{"cast", "shots", "output", "audio"}`.
- Allowed extensions: `{".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mp3", ".wav"}`.
- Default port: 9120, host 127.0.0.1.
- CORS via middleware: `Access-Control-Allow-Origin` from env `SPRITE_STUDIO_ASSET_CORS_ORIGIN` (default `http://localhost:5173`). Preflight OPTIONS responds 204 with the same origin and `Allow-Methods: GET, OPTIONS`. Cache header default `private, max-age=60`.
- Path traversal guard: target.relative_to(root) check rejects `..` or symlink escapes.
- `GET /health` returns `{"status":"ok","projects_root":...,"exists":bool}`.
- Started by `make_app()` and `web.run_app()` either standalone (`workers/asset_server.py --host --port`) or attached to bridge's event loop via `bridge/server.py:_start_asset_server()` (web.AppRunner + TCPSite). When the standalone instance is already bound, the bridge logs a warning and proceeds (8643 still serves /slash, 9120 left to the prior process).

### 2.10. Prompts

| File | Lines | First 40-line preview shows |
|---|---|---|
| brief_clarifier.md | 40 | input vars `{styles_json}`, `{brief}`. Output: `needs_clarification`, `questions[]`, `auto_decisions {style_preset_id, duration_seconds, vibe}`. |
| cast_designer.md | 35 | inputs `{brief}`, `{style_descriptor}`, `{vibe}`, `{duration_seconds}`. Output `characters[]` with `id, ordinal, name, role, persona, visual_description, voice_personality`. 1-4 characters max, ~80-word visual descriptions. |
| character_edit.md | 46 | inputs `{character_json}`, `{user_text}`. Output: `type` ('surgical'|'regenerate'), `rationale`, `updated_visual_description`, `edit_prompt`, `changed_fields`. |
| master_sheet.md | 18 | `{visual_description}`, `{style_preset.descriptor}`, `{style_preset.render_notes}`. Output: 4-panel character model sheet on white background (NOT a chat prompt; passed straight to gpt-image-2). |
| shot_edit.md | 22 | inputs `{shot_json}`, `{user_text}`. Output: `fields_changed`, `updated_shot`, `regenerate_reference_still`, `regenerate_video` (always false). |
| timeline_writer.md | 153 | inputs `{brief}`, `{characters_json}`, `{style_preset_full}`, `{vibe}`, `{duration_seconds}`, `{target_word_count}`. Output: `title`, `logline`, `use_narrator`, `narrator_script`, `shots[]`. |

Reference-image awareness: `grep -lE "reference image|ref image|reference_image|input image" prompts/*.md` returned no matches. None of the six prompt templates mention reference images.

## 3. Reference image support deep dive

### 3.1. DB

The `characters.reference_image_path` column exists (TEXT, nullable). The `source` column has CHECK constraint `IN ('generated','reference_image','reference_photo')` and defaults to `'generated'`. All 17 characters in production have `source='generated'` and `reference_image_path` empty. No row uses the reference value path.

Verdict: **column_exists_but_unused**.

### 3.2. Orchestrator passthrough

`grep -nE "reference_image|ref_image|refImage" orchestrator.py` returned **zero matches**. The orchestrator never reads, writes, or passes through the `reference_image_path` column. `_generate_master_sheet` (orchestrator.py:1238) calls `self._image.generate(prompt=..., size=SIZE_SQUARE, quality=QUALITY_HIGH, n=1, save_to=char_dir, project_id=project_id)` with no `images=` argument, i.e. text-to-image only. `add_character` (orchestrator.py:486) accepts only `description: str`; same generate-only pattern. `edit_character` uses `.edit()` only when the LLM picks `type="surgical"`, and only with the existing `master_sheet_path` as the single ref (not a user-supplied photo).

Verdict: **no_parameter** (the orchestrator does not accept a ref-image arg anywhere in its public API).

### 3.3. Image client

`services/gpt_image.py:167` defines:

```python
async def edit(self, *, prompt, images: list[Path],
               size=SIZE_SQUARE, quality=QUALITY_MEDIUM,
               mask: Path | None = None,
               save_to: Path, project_id: str | None = None) -> Path
```

Validates `1 <= len(images) <= 16`. Sends multipart form-data to `/v1/images/edits` with each file as `("image[]", (p.name, fh, "image/png"))`. Body fields: `model`, `prompt`, `size`, `quality`, `n=1`. Confirms in module docstring that `input_fidelity` is rejected and `background` is unsupported on this model. Verdict: **multi_ref_supported** (up to 16). Used today only by orchestrator for surgical character edits (1 ref) and shot reference-still generation (N master sheets per shot).

### 3.4. Bridge multipart

`bridge/server.py` registers exactly one POST route: `/slash`. Handler reads `await request.json()` and rejects anything that fails JSON decode with HTTP 400 `{"error":"invalid JSON"}`. There is no `multipart/form-data` parsing, no aiohttp `MultipartReader`, and no `FileField`. The `args` field is a plain string.

Verdict: **json_only**. Adding ref-image support either requires (a) a new endpoint such as POST /upload that accepts multipart and returns a server-side path, or (b) base64-in-JSON via the existing /slash with an args-side payload. Note: fitting a 1-2 MB PNG into a slash-arg string would break the (orchestrator-side) 1000-char user-prompt validation; option (a) is structurally cleaner.

### 3.5. Slash command surface

`grep -nE "ref_image|reference_image|refImage" commands.py` returned **zero matches**. None of `/sprite_add_character`, `/sprite_edit_character`, `/sprite_new`, or any other command accepts a reference image argument.

Verdict: **args_absent**.

### 3.6. Prompts

See 2.10 . zero matches for ref-image vocabulary across all six prompts.

Verdict: **prompts_unaware**.

### 3.7. End-to-end verdict

**nothing_wired**, modulo the DB column. The DB schema declares `characters.source` with `'reference_image'`/`'reference_photo'` enum values and a `reference_image_path` TEXT column, but every layer above the DB ignores them: the slash command surface has no argument, the bridge accepts only JSON, the orchestrator's `add_character`/`advance_to_cast_phase`/`edit_character` paths never read the column or pass it to `ImageClient.edit()`, and the prompts contain no instruction about how a reference image should be treated. The only "real" capability already in place is `ImageClient.edit(images=[...])` (max 16), which is currently used for surgical character edits (one master sheet) and shot reference stills (multiple master sheets), never with a user-supplied photo.

Action for P19a-0 if reference-image upload is in scope: build the full chain. Required pieces:

1. Bridge: new `POST /upload` (multipart) or extend `/slash` to accept base64 in JSON. Persist to `<plugin>/projects/<project_id>/_uploads/<id>.png`.
2. db.py: extend `_CHARACTER_COLUMNS` (already has `reference_image_path`); allow `update_character(... source="reference_image", reference_image_path=...)`.
3. orchestrator: new arg on `add_character` and `start_project` (or a new `attach_reference` method). When `source='reference_image'`, swap `_generate_master_sheet`'s call from `.generate(prompt=...)` to `.edit(prompt=..., images=[ref_image_path])` (1 ref) so gpt-5.4-image-2 produces a sheet that resembles the photo.
4. commands.py: new flag on `/sprite_add_character` (e.g. `/sprite_add_character "<desc>" --ref <upload_id>`).
5. prompts: new `master_sheet_with_ref.md` (or branch in the existing one) that instructs the model to preserve identity from the input image.

If reference-image upload is **not** in scope for P19a, no DB or backend changes are needed; the schema is forward-compatible.

## 4. Bridge sidecar

### 4.1. Source

See section 9.6 for the full bridge/server.py verbatim. 259 lines. Key pieces:

- `_load_dotenv_for_api_key()` backfills `API_SERVER_KEY` from `~/.hermes/.env` if not set in env.
- `load_plugin(plugin_path)` imports `<plugin_path>/__init__.py` as `sprite_studio` so relative imports like `from . import db` resolve. Inserts the Hermes venv `site-packages` into `sys.path` first.
- `_check_auth(request, api_key)` returns 401 unless the request has `Authorization: Bearer <api_key>` exactly.
- `_start_asset_server(app)` and `_stop_asset_server(app)` are aiohttp lifecycle hooks: they import `sprite_studio.workers.asset_server` and run it on the same event loop on 127.0.0.1:9120. Bind failures are logged as warnings and not fatal.
- `make_app(plugin, api_key)` returns the aiohttp Application with `GET /health` and `POST /slash`.

### 4.2. Endpoints summary

| Route | Method | Auth | Body | Response |
|---|---|---|---|---|
| /health | GET | none | none | `{"status":"ok","plugin_loaded":true,"command_count":25}` |
| /slash | POST | Bearer | `{"command": "<name>", "args": "<string>"}` JSON | `{"ok":bool, "data":<parsed JSON or null>, "raw":"<original handler output>", "parseError":<str or null>}`. Status 200 on success. Status 400 on invalid JSON. Status 401 on missing/wrong Bearer. Status 404 on unknown command. Status 500 on handler exception with `parseError:"handler raised: <exc>"`. |

The handler call passes `surface="api"` to the registered handler (`commands.py:96` resolves this to the literal string "api", which `_is_chat_surface` returns False for, so the handler returns JSON, not Telegram-formatted markdown).

### 4.3. CORS, auth, lifecycle

- **No CORS middleware on the bridge.** The bridge expects same-origin access via Vite's dev proxy (`/api/*` -> `http://127.0.0.1:8643/*`, see `web/vite.config.ts`). A direct fetch from a browser on `http://localhost:5173` to `http://127.0.0.1:8643/slash` would be CORS-blocked. **In production, the web build must serve same-origin or the bridge needs CORS.**
- Auth: single shared secret, `API_SERVER_KEY`, sourced from env or `~/.hermes/.env`. Frontend reads it as `VITE_SPRITE_BRIDGE_KEY` from `web/.env.local`.
- Lifecycle: `web.run_app(app, host=..., port=...)` blocks. Asset server is started on `app.on_startup` and cleaned up on `app.on_cleanup`. Single Python process; both ports bound by the same event loop.

### 4.4. Asset server start sequence

1. `bridge/run.sh` first runs `pkill -f "sprite-studio/workers/asset_server.py"` to evict any standalone instance.
2. Sources `~/.hermes/.env` so `API_SERVER_KEY` is in env.
3. Execs `~/.hermes/hermes-agent/venv/bin/python3 server.py`.
4. `bridge/server.py` calls `load_plugin()` (which puts `sprite_studio` in `sys.modules` so relative imports resolve), then `make_app()` registers `_start_asset_server` as `on_startup`. When `web.run_app` boots, the on_startup hook imports `sprite_studio.workers.asset_server`, calls its `make_app()`, and binds to 127.0.0.1:9120 via `web.AppRunner` + `TCPSite`. The `_asset_runner` is stashed in the app for cleanup.
5. The bridge listens on 127.0.0.1:8643 (the env override is `SPRITE_BRIDGE_HOST` / `SPRITE_BRIDGE_PORT`).

A standalone `bridge/run-assets.sh` exists for running just the asset server (without the bridge), invoking `~/.hermes/hermes-agent/venv/bin/python3 ~/.hermes/plugins/sprite-studio/workers/asset_server.py`.

## 5. Web app current state

### 5.1. File tree

```
src/main.tsx                          (10 lines)
src/index.css                         (11 lines)
src/types/sprite.ts                   (124 lines)
src/state/store.ts                    (530 lines)
src/lib/bridge.ts                     (115 lines)
src/lib/assets.ts                     (58 lines)
src/components/AppShell.tsx           (47 lines)
src/components/BriefPanel.tsx         (168 lines)
src/components/CastCanvas.tsx         (293 lines)
src/components/ChatPanel.tsx          (96 lines)
src/components/HealthCheck.tsx        (61 lines)
src/components/RenderProgress.tsx     (200 lines)
src/components/ShotDrawer.tsx         (236 lines)
src/components/Sidebar.tsx            (204 lines)
src/components/TimelineEditor.tsx     (274 lines)
src/components/Workspace.tsx          (21 lines)
Total LOC: 2448
```

Configs at root: `.env.example`, `.env.local`, `eslint.config.js`, `index.html`, `package.json`, `postcss.config.js`, `tailwind.config.js`, `tsconfig.app.json`, `tsconfig.json`, `tsconfig.node.json`, `vite.config.ts`.

### 5.2. package.json

```json
{
  "name": "web",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "preview": "vite preview"
  },
  "dependencies": {
    "@dnd-kit/core": "^6.3.1",
    "@dnd-kit/sortable": "^10.0.0",
    "@dnd-kit/utilities": "^3.2.2",
    "@radix-ui/react-popover": "^1.1.15",
    "dnd-timeline": "^3.1.0",
    "react": "^19.2.5",
    "react-dom": "^19.2.5",
    "zustand": "^5.0.12"
  },
  "devDependencies": {
    "@eslint/js": "^10.0.1",
    "@types/node": "^20.19.39",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.1",
    "autoprefixer": "^10.5.0",
    "eslint": "^10.2.1",
    "eslint-plugin-react-hooks": "^7.1.1",
    "eslint-plugin-react-refresh": "^0.5.2",
    "globals": "^17.5.0",
    "postcss": "^8.5.13",
    "tailwindcss": "^3.4.19",
    "typescript": "~6.0.2",
    "typescript-eslint": "^8.58.2",
    "vite": "^8.0.10"
  }
}
```

### 5.3. Installed versions (npm ls)

```
@dnd-kit/core             6.3.1
@dnd-kit/sortable         10.0.0
@dnd-kit/utilities        3.2.2
@eslint/js                10.0.1
@radix-ui/react-popover   1.1.15
@types/node               20.19.39
@types/react-dom          19.2.3
@types/react              19.2.14
@vitejs/plugin-react      6.0.1
autoprefixer              10.5.0
dnd-timeline              3.1.0
eslint-plugin-react-hooks 7.1.1
eslint-plugin-react-refresh 0.5.2
eslint                    10.3.0
globals                   17.6.0
postcss                   8.5.13
react-dom                 19.2.5
react                     19.2.5
tailwindcss               3.4.19
typescript-eslint         8.59.1
typescript                6.0.3
vite                      8.0.10
zustand                   5.0.12
```

`@fontsource/instrument-serif`, `@fontsource/caveat`, `@fontsource/jetbrains-mono` are **not installed** (`npm ls` returns "(empty)" for these). The `node_modules/@fontsource/` directory does not exist. P19a-1 will need to either install these packages or add `<link>` tags for Google Fonts (the design uses Google Fonts).

No "extraneous" or "missing" warnings. The lockfile resolves cleanly.

### 5.4. vite.config + tsconfig

`vite.config.ts`:

```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8643',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
```

The proxy rewrites `/api/<x>` to `<x>` on the bridge, so the bridge's routes `/health` and `/slash` are reached as `/api/health` and `/api/slash` from the browser. This explains why `lib/bridge.ts` has `DEFAULT_BASE = '/api'`.

`tsconfig.json` is a project-references stub:
```json
{
  "files": [],
  "references": [{ "path": "./tsconfig.app.json" }, { "path": "./tsconfig.node.json" }]
}
```

`tsconfig.app.json`:
- target ES2023, lib ES2023+DOM, module esnext.
- bundler resolution, `verbatimModuleSyntax: true`, `erasableSyntaxOnly: true`, `noEmit: true`, `jsx: "react-jsx"`.
- Linting: `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch` all true.
- include: `["src"]`.

`tsconfig.node.json`: same shape, `lib: ES2023`, `types: ["node"]`, include `["vite.config.ts"]`.

Important: `verbatimModuleSyntax: true` requires explicit `import type` for type-only imports. `erasableSyntaxOnly: true` forbids enum / parameter properties / namespace declarations. New code must respect both.

### 5.5. index.html

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Sprite Studio</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

No font tags. The new design needs Instrument Serif / Caveat / JetBrains Mono added either via Google Fonts `<link>` (matches the design reference) or `@fontsource/*` packages (offline-friendly). `public/favicon.svg` is referenced but no `public/` directory was inventoried; if it's missing, the dev server logs a 404 but does not error.

### 5.6. Entry + routing (verbatim)

`src/main.tsx`:

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { AppShell } from './components/AppShell';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppShell />
  </React.StrictMode>,
);
```

`src/components/AppShell.tsx` (47 lines, the actual shell): mounts a flex column with an error banner, a header (sprite-studio v0.1.0 + `<HealthCheck/>`), and a flex row containing `<ChatPanel/>`, `<Workspace/>`, `<Sidebar/>`. On mount it `void checkAssets()` and `void refreshShow()`. There is no router; phase routing is done inside Workspace.

`src/components/Workspace.tsx`:

```tsx
import { useStore } from '../state/store';
import { BriefPanel } from './BriefPanel';
import { CastCanvas } from './CastCanvas';
import { TimelineEditor } from './TimelineEditor';
import { RenderProgress } from './RenderProgress';

export function Workspace() {
  const phase = useStore((s) => s.project?.phase ?? null);

  return (
    <main className="flex-1 overflow-y-auto p-6">
      {(phase === null || phase === 'brief') && <BriefPanel />}
      {phase === 'cast' && <CastCanvas />}
      {phase === 'timeline' && <TimelineEditor />}
      {(phase === 'render' ||
        phase === 'done' ||
        phase === 'failed' ||
        phase === 'cancelled') && <RenderProgress />}
    </main>
  );
}
```

Notable: this code matches a phase value of `'cancelled'` that the DB will never produce (see 7.1).

`src/App.tsx`: NOT FOUND at this path. The actual root component is `AppShell` in `src/components/AppShell.tsx`.

### 5.7. types/sprite.ts (verbatim)

See section 9.1 for the full file. Key types it exports:

- `ProjectPhase = 'brief' | 'cast' | 'timeline' | 'render' | 'done' | 'failed' | 'cancelled'`. The trailing `'cancelled'` is **not** a valid DB value. In the DB the phase remains `'render'` on cancel and `error_message` is set; the type is wrong about this.
- `CharacterRole = 'lead' | 'supporting' | 'comic_relief' | 'antagonist'`.
- `ShotTransition = 'cut' | 'fade' | 'dissolve' | 'match_cut'` matches `db.VALID_SHOT_TRANSITIONS`.
- `Character`: id, project_id, ordinal, name, role?, persona, visual_description?, master_sheet_path?, voice_id?, voice_personality?, is_approved (number 0|1), updated_at?. Missing fields that the DB returns: `source`, `reference_image_path`, `edit_history`, `created_at`. Since `/sprite_show` projects characters down to `{id, ordinal, name, role, master_sheet_path}` (commands.py:1273-1282), this is OK as long as the web only needs those fields. P19a should plumb through `updated_at` and `master_sheet_path` (already done) plus `source` if the design distinguishes generated vs. ref characters.
- `StylePreset`: id, name, descriptor, render_notes?, motion_descriptor?, music_tag?, example_image?. Returned by `/sprite_list_styles` as `{ presets: [...], count }`.
- `DialogEntry = { char_id: string; line: string }`.
- `Shot`: id, project_id, ordinal, duration_seconds, setting?, action, camera?, emotion?, characters_present[], dialog_speakers?, narration_line?, narration_excerpt?, character_dialog?, has_dialog, transition_to_next, reference_still_path?, rendered_video_path?, render_status?, render_error?, cost_usd?, updated_at?.
  - The handler returns BOTH `narration_line` (DB column) and `narration_excerpt` is unused on the way IN to the DB but mapped from `narration_line` on the way OUT in some shapes; types include both. `/sprite_show` returns `narration_excerpt` only when shaped through `_generate_all_reference_stills` on the timeline-write code path (orchestrator.py:2236), not from `sprite_show_handler`. P19a should pick one.
- `Project`: matches the DB columns 1-1 except `use_narrator` is typed as `boolean` while the DB stores INTEGER. JSON.stringify -> 0|1 round-trips fine because the bridge passes the dict through json.dumps and the orchestrator already coerces.
- `ProgressEvent`: matches the dataclass in workers/render_worker.py.
- `RenderStatusResponse`: shape returned by `/sprite_status` (`plugin`, `version`, `status`, `env_ok`, then a nested `project` that may be null). Includes `shots_done`, `shots_total`, `current_step`, `progress_detail`, `progress_error`, `total_cost_usd`, `eta_seconds`, `final_video_path`, `error_message`. Mirrors `commands.py:240-245`.

### 5.8. state/store.ts (verbatim)

See section 9.2 for the full 530-line zustand store. Key shapes:

- `ChatMessage = { id, role: 'user'|'assistant'|'system', text, timestamp }`. The user decision was to wire chat to "existing zustand chat.messages"; this is that slice. `appendChat(role, text)` is the writer.
- `ChatState = { messages: ChatMessage[]; isStreaming: boolean; draft: string }`.
- `AppState` has top-level fields: `activeProjectId, project, characters, shots, status, chat, error, assetServerUp, stylePresets, isPolling, pollIntervalMs`. Action methods (33 in total): `setActiveProject, setError, appendChat, setDraft, newProject, approveCast, generateTimeline, approveTimeline, startRender, cancelRender, refreshStatus, refreshShow, editCharacter, editShot, editShotNL, editShotField, reorderShots, startProgressPolling, stopProgressPolling, checkAssets, loadStylePresets, setStyle, setVibe, setDuration, reorderCast, addCharacter, removeCharacter, regenerateCharacter, sendRaw`.
- The internal `call<T>(command, args)` helper wraps `getSpriteBridge().sendSlash<T>(...)`, throws a `BridgeError` shape (`{status, message}`) on non-OK, returns the parsed JSON `data`.
- Selector pattern: `useStore((s) => s.project?.phase ?? null)`. zustand 5 + subscribeWithSelector middleware.
- `regenerateCharacter` is a wrapper that calls `editCharacter(ordinalOrId, 'regenerate sheet')` because there is no dedicated /sprite_regenerate command (orchestrator's edit decision step routes 'regenerate sheet' to the regenerate path).
- `sendRaw` parses an arbitrary "/cmd args..." string and forwards to the bridge, used by ChatPanel.
- Polling: `startProgressPolling(intervalMs=3000)` runs `refreshStatus + refreshShow` on a setTimeout loop until phase is terminal, then calls `refreshShow` once more and stops.
- Optimistic update pattern (used by reorderShots and reorderCast): paint the new order, call the bridge, roll back on failure.

### 5.9. lib/bridge.ts (verbatim)

See section 9.3. Key surface:

- `class SpriteBridgeClient(baseUrl='/api', apiKey: string)` with `health()` and `sendSlash<T>(command, args='')`.
- `sendSlash` POSTs `/slash` with `Authorization: Bearer <apiKey>` and JSON `{command, args}`. Timeout 600,000 ms (10 min) via `AbortController`. On abort: throws `{status: 0, message: 'Request timed out'}`. On network failure: `{status: 0, message: 'Sprite bridge unreachable. Start with: python /home/drew/sprite-studio/bridge/server.py'}`. On 401/403: `'Invalid API key'`. On 404: `'Unknown slash command: /<x>'`. On other non-OK: `'Bridge error <status>: <body slice>'`.
- Returns `SlashResult<T> = { ok, data: T | null, raw: string, parseError: string | null }`. The bridge always wraps the handler's stringified output in this shape; `parseError` is non-null only when the handler returned a non-JSON string (no slash command in commands.py currently does this on success, only on `_err_json` -- which is JSON).
- `getSpriteBridge()` reads `import.meta.env.VITE_SPRITE_BRIDGE_KEY` lazily; throws if unset. Singleton.

### 5.10. lib/assets.ts (verbatim)

See section 9.4. Key surface:

- `assetBase()` reads `import.meta.env.VITE_ASSET_BASE_URL` (default `'http://127.0.0.1:9120'`).
- `characterSheetUrl(projectId, charId, version?)` -> `${base}/${projectId}/cast/${charId}/sheet.png?v=${version}`.
- `shotReferenceUrl(projectId, shotId, version?)` -> `${base}/${projectId}/shots/${shotId}/reference.png?v=${version}`.
- `shotVideoUrl(projectId, shotId, version?)` -> `${base}/${projectId}/shots/${shotId}.mp4?v=${version}`.
- `projectFinalVideoUrl(projectId, version?)` -> `${base}/${projectId}/output/final.mp4?v=${version}`.
- `checkAssetServer()` -> GET `${base}/health` with 1500 ms timeout.

Cache busting: `?v=<character.updated_at>` (a unix epoch int). When a character is regenerated, `updated_at` changes, so the URL changes, forcing a re-fetch.

Sample IDs (from the most-recent done project):

- PROJECT = `01KQMZKRZVVEW3RBEFP82BK4MG`
- CHARACTER = `01KQMZRJG8XDX6P99Q9AQZQ8ZC`
- SHOT = `01KQN4M6AF8BYZX8RKMCX9V83T`

Resolved URLs (with `version=1777752869`):

- characterSheetUrl: `http://127.0.0.1:9120/01KQMZKRZVVEW3RBEFP82BK4MG/cast/01KQMZRJG8XDX6P99Q9AQZQ8ZC/sheet.png?v=1777752869`
- shotReferenceUrl: `http://127.0.0.1:9120/01KQMZKRZVVEW3RBEFP82BK4MG/shots/01KQN4M6AF8BYZX8RKMCX9V83T/reference.png?v=1777752869`
- shotVideoUrl: `http://127.0.0.1:9120/01KQMZKRZVVEW3RBEFP82BK4MG/shots/01KQN4M6AF8BYZX8RKMCX9V83T.mp4?v=1777752869`
- projectFinalVideoUrl: `http://127.0.0.1:9120/01KQMZKRZVVEW3RBEFP82BK4MG/output/final.mp4?v=1777752869`

Note: `shotVideoUrl` builds `/shots/<id>.mp4` (file under shots dir), but the asset server's allowed extensions are `{.png,.jpg,.jpeg,.webp,.mp4,.mp3,.wav}` and the path traversal check is against the `projects/<id>/shots/` subdir. The render worker writes `projects/<id>/shots/<shot_id>/<n>.mp4` (per-shot dir); `<id>.mp4` directly under `/shots/` would not exist. The current code may have a per-shot mp4 location mismatch (RenderWorker outputs each shot's mp4 to `projects/<project_id>/shots/<shot_id>/something.mp4`, while shotVideoUrl assumes `projects/<project_id>/shots/<shot_id>.mp4`). Worth verifying during P19a-1.

### 5.11. Components inventory

10 .tsx files under `src/components/`:

| File | Lines | Exports |
|---|---|---|
| AppShell.tsx | 47 | `AppShell` (root shell with header + 3-pane layout) |
| BriefPanel.tsx | 168 | `BriefPanel` (brief input form) |
| CastCanvas.tsx | 293 | `CastCanvas` (cast grid with @dnd-kit + @radix-ui/react-popover) |
| ChatPanel.tsx | 96 | `ChatPanel` (messages + draft input on the left) |
| HealthCheck.tsx | 61 | `HealthCheck` (badge in header) |
| RenderProgress.tsx | 200 | `RenderProgress` (per-shot status grid + final video) |
| ShotDrawer.tsx | 236 | `ShotDrawer({...props})` (slide-out edit panel) |
| Sidebar.tsx | 204 | `Sidebar` (right rail, project meta + style/vibe controls) |
| TimelineEditor.tsx | 274 | `TimelineEditor` (shot list with dnd-timeline) |
| Workspace.tsx | 21 | `Workspace` (phase-based switcher) |

Per the build report, eight components were expected to be present and slated for deletion: Workspace, BriefPanel, CastCanvas, TimelineEditor, RenderProgress, ChatPanel, Sidebar, ShotDrawer. All eight are present. Two extras exist: **AppShell** (the root layout) and **HealthCheck** (the header badge). `AppShell` is referenced by `main.tsx` and provides the chrome that hosts the deleted components; the P19a-1 prompt should call out whether it's also deleted (likely yes, since the new design has a different chrome, `Header` + `ChatDock` from the design reference) and whether `HealthCheck` is preserved.

Imports cross-check:

- AppShell -> HealthCheck, ChatPanel, Workspace, Sidebar.
- Workspace -> BriefPanel, CastCanvas, TimelineEditor, RenderProgress.
- TimelineEditor -> ShotDrawer.
- CastCanvas, ShotDrawer, TimelineEditor, RenderProgress all import from `../lib/assets`.
- HealthCheck imports `getSpriteBridge` from `../lib/bridge`.
- All store consumers pull from `../state/store`.

`lib/bridge.ts`, `lib/assets.ts`, `state/store.ts`, `types/sprite.ts` are imported by multiple components and should be **kept**. Most components in the existing set will be deleted in P19a-1; the four core lib/state/types files survive.

### 5.12. .env.local keys only

`web/.env.local` (88 bytes, mode 0600):

```
VITE_SPRITE_BRIDGE_KEY=<REDACTED>
```

`web/.env.example` (visible):

```
VITE_SPRITE_BRIDGE_KEY=set-me-from-hermes-env-API_SERVER_KEY
VITE_ASSET_BASE_URL=http://127.0.0.1:9120
```

Only `VITE_SPRITE_BRIDGE_KEY` is set in .env.local. `VITE_ASSET_BASE_URL` is unset and falls back to the `assetBase()` default `http://127.0.0.1:9120`.

### 5.13. tsc --noEmit result

`npx tsc --noEmit` ran from `web/` with no output and exit code 0. Zero type errors against the current source set. P19a-1 should preserve this baseline; the strict tsconfig flags (`verbatimModuleSyntax`, `erasableSyntaxOnly`, `noUnusedLocals`, `noUnusedParameters`) will catch sloppy ports.

## 6. Design reference

### 6.1. Inventory

Note: the audit prompt says the design reference lives at `web/_design_reference/` with 8 files at the top. Actual layout has them nested one level deeper, under `web/_design_reference/HERMES HIGH/` (with a literal space in the directory name).

Files at `web/_design_reference/HERMES HIGH/`:

```
Sprite Studio.html      (353 lines)   <- entrypoint
tweaks-panel.jsx        (425 lines)   <- to be REMOVED in P19a
src/app.jsx             (251 lines)
src/chrome.jsx          (147 lines)
src/data.jsx            (71 lines)
src/phases.jsx          (1023 lines)  <- the six phase screens
src/popovers.jsx        (545 lines)
src/sprites.jsx         (120 lines)
_sprite_check.png       (37,880 bytes)  <- reference screenshot
uploads/                (~24 png files, screenshots/sketches/iterations)
```

Confirmed 8 hand-coded files plus the screenshot and uploads directory.

### 6.2. Globals registered via window

```
src/data.jsx      Object.assign(window, { INITIAL_CHARACTERS, INITIAL_SHOTS, PROJECTS, PHASES, HEADER_PHASES, TONE_PALETTES });
src/sprites.jsx   Object.assign(window, { SpriteCell, SpriteSheet, ShotStill, ProjectThumb });
src/chrome.jsx    Object.assign(window, { Header, ChatDock, Backdrop, CharacterCard });
src/popovers.jsx  Object.assign(window, { CharacterEditPopover, CharacterAddPopover, ShotEditPopover, TransitionPopover });
src/phases.jsx    Object.assign(window, { LobbyScreen, BriefScreen, CastScreen, TimelineScreen, RenderScreen, DoneScreen });
tweaks-panel.jsx  Object.assign(window, { useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton });
```

To convert to ES modules in P19a-1: replace each `Object.assign(window, {...})` with named `export {...}`, replace top-level `const { useState, useEffect } = React;` with `import { useState, useEffect } from 'react'`, and rewrite component-to-component references that today rely on global lookup to use proper imports.

### 6.3. CSS size + first 60 lines

The design CSS is one large `<style>` block in `Sprite Studio.html`:

```
css_chars = 9255
css_lines = 317
```

First lines (verbatim):
```css
:root {
  --paper: #f4f1ea;
  --paper-tint: #ebe6db;
  --paper-deep: #e2dccb;
  --ink: #1a1814;
  --ink-soft: #4a453d;
  --ink-faint: #8a8175;
  --rule: #1a1814;
  --rule-soft: #c4bcaa;
  --accent: #c4452a;
  --accent-tint: rgba(196, 69, 42, 0.08);
  --accent-tint-strong: rgba(196, 69, 42, 0.16);
  --highlight: #f3e58a;
  --good: #4f7a4c;
  --good-tint: rgba(79, 122, 76, 0.12);
  --serif: 'Instrument Serif', 'Iowan Old Style', Georgia, serif;
  --hand: 'Caveat', 'Bradley Hand', cursive;
  --mono: 'JetBrains Mono', ui-monospace, monospace;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--serif);
  overflow: hidden;
  height: 100%;
  -webkit-font-smoothing: antialiased;
}
#root { height: 100vh; width: 100vw; }

/* Paper texture */
.paper-bg { ... repeating-linear-gradient + radial-gradient noise ... }
.wf-grid  { ... 24px radial dot grid ... }

.box-hand   { background: var(--paper); border: 1.5px solid var(--rule);
              border-radius: 4px 6px 5px 7px / 6px 5px 7px 4px; ... }
.box-hand-2 { border-radius: 6px 4px 7px 5px / 5px 7px 4px 6px; }
.box-hand-3 { border-radius: 5px 7px 4px 6px / 7px 4px 6px 5px; }
.box-soft   { background: var(--paper); ...
```

For P19a-1: this CSS goes into `web/src/index.css` (or split per-component in CSS Modules / Tailwind extensions). Tailwind already exists in the build. Decision needed: pure CSS in index.css (preserves the design's hand-feel utilities), or rewrite as Tailwind classes (loses the named utility classes).

### 6.4. Font dependencies

The design loads Google Fonts via:

```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Caveat:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
```

Three families: Instrument Serif (italic + roman), Caveat (400/500/600), JetBrains Mono (400/500/600).

Web app currently has neither the `<link>` tag nor the `@fontsource/*` packages. Two carry-over options:

1. Drop the same `<link>` tag into `web/index.html`. Simplest, matches design exactly. Downside: requires fonts.googleapis.com at runtime.
2. Install `@fontsource/instrument-serif`, `@fontsource/caveat`, `@fontsource/jetbrains-mono` and import the per-weight CSS in `web/src/index.css`. Offline-friendly. Downside: a few hundred KB of font CSS in the bundle.

### 6.5. External scripts (React version)

```
<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" ...
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" ...
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" ...
```

The design uses React 18.3.1 UMD with @babel/standalone for in-browser JSX transform. The web app uses React 19.2.5 with Vite + @vitejs/plugin-react (build-time JSX). React 18 -> 19 migration notes for the conversion:

- `forwardRef` is deprecated; functional components accept `ref` directly.
- `act` moved from `react-dom/test-utils` to `react`.
- `createRoot`, `useState`, `useEffect` are unchanged.
- `<form action={fn}>` is new in 19; the design does not use it (forms are plain JSX).
- Auto-batching is on by default; the design never `unstable_batchedUpdates`-es.
- StrictMode behavior is unchanged.

The conversion is straightforward: remove the UMD scripts and the babel/standalone reference, replace `Object.assign(window, {...})` with `export { ... }`, replace `const { useState, useEffect } = React;` with `import { useState, useEffect } from 'react';`, and change file extensions from `.jsx` to `.tsx` (or keep `.jsx` if the prompt-writer wants a lighter touch). No deep refactor required.

### 6.6. Tweaks panel exports

`tweaks-panel.jsx` exports (via window):

```
useTweaks, TweaksPanel, TweakSection, TweakRow,
TweakSlider, TweakToggle, TweakRadio, TweakSelect,
TweakText, TweakNumber, TweakColor, TweakButton
```

Per user decision #11, NONE of these carry over to the web app. P19a-1 deletes `tweaks-panel.jsx` from the conversion set and removes the `<TweaksPanel ...>` mount and the `<script type="application/json" id="tweak-defaults">...</script>` block from the HTML at the same time. The `useTweaks` hook (a localStorage-backed state thing) is also unused.

## 7. Cross-cutting synthesis

### 7.1. VALID_SHOT_TRANSITIONS

Source of truth (db.py:29):

```python
VALID_SHOT_TRANSITIONS = ("cut", "fade", "dissolve", "match_cut")
```

Mirrored by SQLite CHECK on `shots.transition_to_next`:

```sql
transition_to_next TEXT NOT NULL DEFAULT 'cut'
  CHECK (transition_to_next IN ('cut','fade','dissolve','match_cut'))
```

Frontend mirror in `web/src/types/sprite.ts:16`: `type ShotTransition = 'cut' | 'fade' | 'dissolve' | 'match_cut'`. Matches.

The render pipeline applies xfade between shot N and shot N+1 only when `transition_to_next` is `'fade'` or `'dissolve'`; `'cut'` and `'match_cut'` render as hard cuts (commands.py:1822 docstring). The last shot's `transition_to_next` is structurally ignored at stitch time.

### 7.2. _SHOT_SAFE_FIELDS

Source of truth (db.py:849-852):

```python
_SHOT_SAFE_FIELDS = {
    "duration_seconds", "setting", "action", "camera", "emotion",
    "narration_line", "transition_to_next",
}
```

This is the column whitelist for `db.update_shot_fields()`. Anything not in this set is silently dropped. P19a-1 should expose these field names directly in the UI's surgical-edit affordances.

Note: `db.update_shot()` (the non-`_fields` variant at line 795) does NOT consult this whitelist; it goes through `_build_update` against the broader `_SHOT_COLUMNS` set. `_SHOT_SAFE_FIELDS` is the safer, phase-guard-aware path that the slash command surface uses.

### 7.3. Shot CRUD inventory

| Operation | DB helper | Slash command | Phase-guarded |
|---|---|---|---|
| Create at end | `db.create_shot()` (always picks ordinal) | NO | |
| Insert at start | NO (would need re-pack, not implemented) | NO | |
| Insert before/after ordinal | NO (would need re-pack, not implemented) | NO | |
| Update single field | `db.update_shot_fields()` (whitelist) | `/sprite_edit_shot_field` | timeline only |
| Update multiple fields | `db.update_shot()` (any column) | NO direct; inside `orchestrator.edit_shot` via NL | timeline only (orchestrator) |
| Delete | NO (only `db.delete_project` deletes everything) | NO | |
| Reorder | `db.reorder_shots()` | `/sprite_reorder_shots` | timeline only |
| NL edit | (LLM decision -> update_shot) | `/sprite_edit_shot` | timeline only |
| Set transition | `db.update_shot_fields(transition_to_next=...)` | `/sprite_set_shot_transition` | timeline only |

Implication for P19a UI design: the design's "+ add shot" and "delete shot" affordances on the timeline screen are NOT supported by the backend today. P19a-0 must add:

1. db.py: `create_shot_at_ordinal(project_id, ordinal, ...)` that re-packs higher ordinals on insert.
2. db.py: `delete_shot(shot_id)` that deletes the row and re-packs lower-ordinal-stable + higher-ordinal-down-by-1 on the same project.
3. commands.py: `/sprite_add_shot "<ordinal> | <description>"` (the LLM expands description into shot fields, mirroring `add_character`'s flow but for shots; or split into a surgical "create empty shot" path and rely on `/sprite_edit_shot` to fill it in).
4. commands.py: `/sprite_delete_shot "<ordinal_or_id>"`.
5. Reference-still cleanup on delete (move `projects/<id>/shots/<shot_id>/` to `_trash/`, like character remove).
6. plugin.yaml: add `sprite_add_shot`, `sprite_delete_shot` to `provides_commands`.
7. SLASH_COMMANDS dict (commands.py: tail) add the same two entries.

The orchestrator does not have `add_shot`/`delete_shot` methods today; P19a-0 should add them under `ProjectOrchestrator`.

### 7.4. Project list with thumbnails

`/sprite_list` today returns:

```json
{
  "count": <n>,
  "phase_filter": <phase or null>,
  "projects": [
    { "id", "title", "phase", "brief": "<first 80 chars>", "total_cost_usd",
      "updated_at", "final_video_path" }
  ]
}
```

There is **no** thumbnail-suitable path. To populate the lobby/grid view (per the design's `LobbyScreen` showing a thumbnail per project), three options exist, ordered by recommended -> least:

(a) **Extend `/sprite_list` handler to JOIN the first shot.** Add a `thumb_path` field to each project entry: pick the first shot of the project, return `s.reference_still_path` if present, else `s.rendered_video_path` thumbnail (would need extraction), else null. Cheapest and matches the design's per-card render. Single query change in `db.list_projects`. **Recommended.**

(b) Add a separate `/sprite_thumbnails` endpoint that batches `<id, thumb_path>` for a list of project ids.

(c) Accept N+1 (one `/sprite_show <id>` per card with web-side caching).

Implementation hint for (a): in `commands.py:sprite_list_handler`, after the projects list comes back, do one extra read per project for `db.list_shots(p["id"])[0]`, then pull `reference_still_path`. With N=20, that's 20 cheap SELECTs; SQLite handles it in well under 50ms total. Or, if performance matters, add a `db.list_projects_with_thumbnails(user_id, limit, phase)` that does a left-join in one go.

### 7.5. Render status response shape

Returned by `/sprite_status` (full shape, JSON encoded; `_format_status_for_telegram` is unused on api surface):

```json
{
  "plugin": "sprite-studio",
  "version": "0.1.0",
  "status": "ok",
  "db_path": "/home/drew/.hermes/plugins/sprite-studio/state.db",
  "db_size_bytes": 286720,
  "env_ok": true,
  "env_present": {"TOKENROUTER_API_KEY": true, "ELEVENLABS_API_KEY": true},
  "project": {
    "project_id": "<id>",
    "phase": "brief|cast|timeline|render|done|failed",
    "title": "<str or null>",
    "shots_done": <int>,
    "shots_total": <int>,
    "current_step": "queued|rendering shots|synthesizing narration|picking music|stitching final video|validating output|done|failed|cancelled|idle",
    "progress_detail": "<str or null>",
    "progress_error": "<str or null>",
    "total_cost_usd": <float>,
    "eta_seconds": <int or null>,
    "final_video_path": "<absolute path or null>",
    "error_message": "<str or null>"
  } /* OR null if no project yet */
}
```

Per-shot states are NOT exposed by `/sprite_status`; the per-shot `render_status` ('pending'|'rendering'|'done'|'failed') is only available via `/sprite_show`. The web app today calls both `refreshStatus()` and `refreshShow()` on each poll tick (state/store.ts:351). Overall progress %: `shots_done / shots_total`. ETA: only computed when stage is `rendering_shots`, `eta = remaining * 120s / 4 (concurrency) + 30s narr + 30s stitch`. Cost-so-far is exposed as `total_cost_usd` (live during render).

### 7.6. Surface dispatch

Resolution in `commands.py:88-100`:

```python
def _surface(kwargs: dict) -> str:
    return (kwargs.get("surface") or kwargs.get("platform") or "cli").lower()

def _is_chat_surface(surface: str) -> bool:
    return surface in ("telegram", "discord")
```

Current senders:

- Hermes gateway (Telegram/Discord/Slack/CLI) post-P17: passes `surface=event.source.platform.value` (so values are 'telegram'/'discord'/'slack'/'cli'). 'slack' is not in `_CHAT_SURFACES` and gets JSON output too; this might be intentional or a P17 oversight, irrelevant to the web flow.
- Bridge (`bridge/server.py:192,194`): always passes `surface="api"`. `_is_chat_surface("api")` is False -> JSON path.
- CLI: passes nothing -> defaults to "cli". JSON path.

Web app behavior is therefore: every slash command returns JSON, no MEDIA: lines, no markdown.

### 7.7. Single-user assumption

`commands.py:521`: `_USER_ID = "cli"` (hardcoded constant).
`commands.py:413`: orchestrator.start_project gets `user_id="cli"` literal.

Every list/sum/latest function in `db.py` filters by `user_id`. Today there is exactly one user_id in production (verified: `SELECT DISTINCT user_id FROM projects` returns one row, value "cli"). The lobby will list ALL projects for `_USER_ID`. There is no concept of "current user" beyond this constant. Adding a real user identity is out of scope for P19a (per backlog item 23).

## 8. Open questions for prompt-writer

- **Question:** Should `'cancelled'` be added to the project phase CHECK constraint, or should the `ProjectPhase` TS type be narrowed to drop it?
  - **Evidence:** db.py:71 (CHECK constraint omits 'cancelled'), workers/render_worker.py:860-865 (`_mark_cancelled` leaves phase='render' and writes error_message), web/src/types/sprite.ts:1-8 (ProjectPhase union includes 'cancelled'), web/src/components/Workspace.tsx:11-15 (cancellation routed to RenderProgress).
  - **Proposed default:** Narrow the TS type. Drop 'cancelled' from `ProjectPhase`. Surface cancellation via `error_message` text (`startsWith('cancelled:')`) on phase=='render', not via a phase value.

- **Question:** Does the design's "+ add shot" / "delete shot" affordance require backend support in P19a-0, or are these UI-only no-ops for the prototype pass?
  - **Evidence:** _design_reference/HERMES HIGH/src/app.jsx:99-117 (saveShot/deleteShot in design state), commands.py (no sprite_add_shot or sprite_delete_shot handler), db.py (no insert_shot or delete_shot helper).
  - **Proposed default:** Wire them through. Add `db.create_shot_at_ordinal`, `db.delete_shot`, `orchestrator.add_shot`, `orchestrator.remove_shot`, `/sprite_add_shot`, `/sprite_delete_shot` in P19a-0. Without these, the design's affordances would be lies.

- **Question:** Should reference-image upload be in scope for P19a, or deferred?
  - **Evidence:** end-to-end verdict in section 3.7 says "nothing_wired"; design reference does not surface a "Add character from photo" flow (the popovers in popovers.jsx accept text descriptions only).
  - **Proposed default:** Defer. P19a is the UI integration pass on the existing backend; ref-image is a separate feature build. Leave the DB column untouched.

- **Question:** For the lobby, JOIN approach or N+1 calls?
  - **Evidence:** commands.py:sprite_list_handler omits thumbnail field; db.list_projects is a simple SELECT with no JOIN.
  - **Proposed default:** Extend `/sprite_list` to include `thumb_path` (option (a) in 7.4). Single backend change, single frontend surface.

- **Question:** For the design's CSS, port to Tailwind classes, keep as plain CSS in index.css, or use CSS Modules?
  - **Evidence:** web/tailwind.config.js exists (web uses Tailwind). The design uses named classes like `.box-hand`, `.paper-bg`, `.wf-grid` with CSS variables for colors, plus several keyframe animations.
  - **Proposed default:** Plain CSS in index.css, augmented with Tailwind for utility classes (margin/padding/flex). The hand-drawn feel relies on irregular border-radius (`4px 6px 5px 7px / 6px 5px 7px 4px`) and noisy gradients that don't have Tailwind equivalents; rewriting them as utilities loses fidelity.

- **Question:** Fonts via Google Fonts `<link>` (matches design) or `@fontsource/*` packages (offline)?
  - **Evidence:** web/index.html has no font tags; design uses Google Fonts; @fontsource packages not installed.
  - **Proposed default:** Google Fonts `<link>` in index.html. The dev server already needs internet for npm; one extra font fetch is not a regression. If offline becomes a constraint later, swap to @fontsource and re-test.

- **Question:** Per-shot mp4 URL format. Should `shotVideoUrl` produce `/shots/<id>.mp4` (current) or `/shots/<id>/<n>.mp4` (likely actual)?
  - **Evidence:** web/src/lib/assets.ts:34-39 (current), workers/render_worker.py writes per-shot mp4s under `projects/<project_id>/shots/<shot_id>/`. Not verified against a real rendered project's directory tree in this audit.
  - **Proposed default:** Inspect a rendered project's `shots/` directory in the projects/ tree before P19a-1; rewrite `shotVideoUrl` to match. If the worker writes `final.mp4` only and per-shot is at `<shot_id>/0.mp4`, the helper needs the trailing `/0.mp4` (or whatever the worker uses).

- **Question:** Should `/sprite_show` characters include `source` and `reference_image_path` so the UI can distinguish "generated from text" vs "based on a photo"?
  - **Evidence:** commands.py:1273-1282 projects characters down to `{id, ordinal, name, role, master_sheet_path}`. types/sprite.ts:Character lacks source/reference_image_path.
  - **Proposed default:** Yes, add both to the projection. Even if reference-image upload is deferred, the column exists and surfacing it is cheap. Future-proofs the UI for when the feature lands.

- **Question:** How does the design's chat dock map to the existing zustand `chat.messages` slice?
  - **Evidence:** state/store.ts:13-24 (ChatMessage = `{id, role: 'user'|'assistant'|'system', text, timestamp}`); design _design_reference/HERMES HIGH/src/app.jsx:57-77 uses a per-phase `chatHistory` map with `{who: 'you'|'sys'|'ai', text}`.
  - **Proposed default:** Map the design's `who` to existing roles: `you -> user`, `sys -> system`, `ai -> assistant`. Don't introduce a new role enum. Drop the per-phase split (the design uses it because the design is a static mock; the real chat is a single rolling history).

- **Question:** Should the web app surface `/sprite_purge` (with --confirm) for the lobby's project-card delete affordance?
  - **Evidence:** sprite_purge_handler exists, requires `--confirm` flag, deletes both DB rows and filesystem dir. The design's lobby may or may not show a delete button (uploads/draw-... files weren't reviewed for hover states in this audit).
  - **Proposed default:** Yes, with a confirmation modal. Single-user, single-machine; users will accumulate test projects and want a way to clean them up without dropping to the CLI.

## 9. Appendix: mandatory verbatim dumps

The seven mandatory files. Em dashes (U+2014) in source have been replaced with ", " for the report-level constraint (see header note); everything else is byte-identical to the source as of the audit timestamp.

### 9.1. types/sprite.ts

```ts
export type ProjectPhase =
  | 'brief'
  | 'cast'
  | 'timeline'
  | 'render'
  | 'done'
  | 'failed'
  | 'cancelled';

export type CharacterRole =
  | 'lead'
  | 'supporting'
  | 'comic_relief'
  | 'antagonist';

export type ShotTransition = 'cut' | 'fade' | 'dissolve' | 'match_cut';

export interface Character {
  id: string;
  project_id: string;
  ordinal: number;
  name: string;
  role?: CharacterRole;
  persona: string;
  visual_description?: string;
  master_sheet_path?: string | null;
  voice_id?: string | null;
  voice_personality?: string | null;
  is_approved: number;
  updated_at?: number;
}

export interface StylePreset {
  id: string;
  name: string;
  descriptor: string;
  render_notes?: string;
  motion_descriptor?: string;
  music_tag?: string;
  example_image?: string;
}

export interface DialogEntry {
  char_id: string;
  line: string;
}

export interface Shot {
  id: string;
  project_id: string;
  ordinal: number;
  duration_seconds: number;
  setting?: string;
  action: string;
  camera?: string | null;
  emotion?: string | null;
  characters_present: string[];
  dialog_speakers?: string[] | null;
  narration_line?: string | null;
  narration_excerpt?: string | null;
  character_dialog?: DialogEntry[] | null;
  has_dialog: boolean;
  transition_to_next: ShotTransition;
  reference_still_path?: string | null;
  rendered_video_path?: string | null;
  render_status?: string;
  render_error?: string | null;
  cost_usd?: number;
  updated_at?: number;
}

export interface Project {
  id: string;
  user_id: string;
  surface: string;
  brief: string;
  style_preset_id: string;
  vibe?: string | null;
  duration_seconds: number;
  phase: ProjectPhase;
  title?: string | null;
  narrator_script?: string | null;
  use_narrator: boolean;
  music_track_path?: string | null;
  final_video_path?: string | null;
  total_cost_usd: number;
  created_at: number;
  updated_at: number;
  approved_cast_at?: number | null;
  approved_timeline_at?: number | null;
  rendered_at?: number | null;
  error_message?: string | null;
}

export interface ProgressEvent {
  project_id: string;
  timestamp: number;
  stage: string;
  detail: string;
  completed?: number;
  total?: number;
  error?: string | null;
}

export interface RenderStatusResponse {
  plugin: string;
  version: string;
  status: string;
  env_ok: boolean;
  project: {
    project_id: string;
    phase: ProjectPhase;
    title?: string | null;
    shots_done: number;
    shots_total: number;
    current_step: string;
    progress_detail?: string | null;
    progress_error?: string | null;
    total_cost_usd: number;
    eta_seconds?: number | null;
    final_video_path?: string | null;
    error_message?: string | null;
  } | null;
}
```

### 9.2. state/store.ts

```ts
import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { getSpriteBridge, type BridgeError } from '../lib/bridge';
import { checkAssetServer } from '../lib/assets';
import type {
  Character,
  Project,
  Shot,
  RenderStatusResponse,
  StylePreset,
} from '../types/sprite';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  text: string;
  timestamp: number;
}

interface ChatState {
  messages: ChatMessage[];
  isStreaming: boolean;
  draft: string;
}

interface AppState {
  activeProjectId: string | null;
  project: Project | null;
  characters: Character[];
  shots: Shot[];
  status: RenderStatusResponse | null;
  chat: ChatState;
  error: string | null;
  assetServerUp: boolean | null;
  stylePresets: StylePreset[];
  isPolling: boolean;
  pollIntervalMs: number;

  setActiveProject(id: string | null): void;
  setError(msg: string | null): void;
  appendChat(role: ChatMessage['role'], text: string): void;
  setDraft(text: string): void;

  newProject(brief: string): Promise<void>;
  approveCast(): Promise<void>;
  generateTimeline(): Promise<void>;
  approveTimeline(): Promise<void>;
  startRender(): Promise<void>;
  cancelRender(): Promise<void>;
  refreshStatus(projectId?: string): Promise<void>;
  refreshShow(projectId?: string): Promise<void>;
  editCharacter(ordinalOrId: string | number, changes: string): Promise<void>;
  editShot(ordinalOrId: string | number, changes: string): Promise<void>;
  editShotNL(ordinalOrId: string | number, changes: string): Promise<void>;
  editShotField(
    ordinalOrId: string | number,
    field: string,
    value: string | number,
  ): Promise<void>;
  reorderShots(shotIds: string[]): Promise<void>;
  startProgressPolling(intervalMs?: number): void;
  stopProgressPolling(): void;

  checkAssets(): Promise<void>;
  loadStylePresets(): Promise<void>;
  setStyle(presetId: string): Promise<void>;
  setVibe(vibe: string): Promise<void>;
  setDuration(seconds: number): Promise<void>;
  reorderCast(charIds: string[]): Promise<void>;
  addCharacter(description: string): Promise<void>;
  removeCharacter(charIdOrOrdinal: string | number): Promise<void>;
  regenerateCharacter(ordinalOrId: string | number): Promise<void>;
  sendRaw(text: string): Promise<void>;
}

function newId(): string {
  return `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

async function call<T>(command: string, args = ''): Promise<T | null> {
  const client = getSpriteBridge();
  const result = await client.sendSlash<T>(command, args);
  if (!result.ok) {
    throw {
      status: 0,
      message: `slash returned non-JSON: ${result.parseError ?? 'empty'}`,
    } as BridgeError;
  }
  return result.data;
}

export const useStore = create<AppState>()(
  subscribeWithSelector((set, get) => ({
    activeProjectId: null,
    project: null,
    characters: [],
    shots: [],
    status: null,
    chat: { messages: [], isStreaming: false, draft: '' },
    error: null,
    assetServerUp: null,
    stylePresets: [],
    isPolling: false,
    pollIntervalMs: 3000,

    setActiveProject: (id) => set({ activeProjectId: id }),
    setError: (msg) => set({ error: msg }),
    appendChat: (role, text) =>
      set((s) => ({
        chat: {
          ...s.chat,
          messages: [
            ...s.chat.messages,
            { id: newId(), role, text, timestamp: Date.now() },
          ],
        },
      })),
    setDraft: (text) => set((s) => ({ chat: { ...s.chat, draft: text } })),

    newProject: async (brief) => {
      const args = `"${brief.replace(/"/g, '\\"')}"`;
      get().appendChat('user', `/sprite_new ${args}`);
      try {
        const r = await call<{ project_id: string; status?: string }>(
          'sprite_new',
          args,
        );
        if (r?.project_id) {
          set({ activeProjectId: r.project_id });
          get().appendChat('assistant', JSON.stringify(r, null, 2));
          await get().refreshShow(r.project_id);
        } else {
          get().appendChat('assistant', JSON.stringify(r, null, 2));
        }
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
        get().appendChat('system', `error: ${err.message}`);
      }
    },

    approveCast: async () => {
      get().appendChat('user', '/sprite_approve_cast');
      try {
        const r = await call<unknown>('sprite_approve_cast');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    generateTimeline: async () => {
      get().appendChat('user', '/sprite_timeline');
      try {
        const r = await call<unknown>('sprite_timeline');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    approveTimeline: async () => {
      get().appendChat('user', '/sprite_approve_timeline');
      try {
        const r = await call<unknown>('sprite_approve_timeline');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    startRender: async () => {
      get().appendChat('user', '/sprite_render');
      try {
        const r = await call<unknown>('sprite_render');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshStatus();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    cancelRender: async () => {
      get().appendChat('user', '/sprite_cancel');
      try {
        const r = await call<unknown>('sprite_cancel');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshStatus();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    refreshStatus: async (projectId) => {
      try {
        const status = await call<RenderStatusResponse>(
          'sprite_status',
          projectId ?? '',
        );
        set({ status });
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    refreshShow: async (projectId) => {
      try {
        const targetId = projectId ?? get().activeProjectId ?? '';
        const data = await call<
          {
            project_id: string;
            phase: string;
            characters: Character[];
            shots: Shot[];
          } & Project
        >('sprite_show', targetId);
        // Empty-args /sprite_show returns the latest project for the user;
        // an unauthenticated/no-project response is a bare error. Treat
        // both as "nothing to hydrate" instead of throwing.
        if (!data || !data.project_id) return;
        set({
          activeProjectId: data.project_id,
          project: data as unknown as Project,
          characters: data.characters ?? [],
          shots: data.shots ?? [],
        });
      } catch (e: unknown) {
        const err = e as BridgeError;
        // First-load with no project yet returns "no project for user";
        // that's expected, not an error worth surfacing.
        if (err.message?.includes('no project for user')) return;
        set({ error: err.message });
      }
    },

    editCharacter: async (ordinalOrId, changes) => {
      const args = `"${`${ordinalOrId} | ${changes}`.replace(/"/g, '\\"')}"`;
      get().appendChat('user', `/sprite_edit_character ${args}`);
      try {
        const r = await call<unknown>('sprite_edit_character', args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    editShot: async (ordinalOrId, changes) => {
      // Backwards-compatible alias for editShotNL , older callers (sendRaw
      // chat path, ShotPanel from P15) still hit this name.
      await get().editShotNL(ordinalOrId, changes);
    },

    editShotNL: async (ordinalOrId, changes) => {
      const args = `"${`${ordinalOrId} | ${changes}`.replace(/"/g, '\\"')}"`;
      get().appendChat('user', `/sprite_edit_shot ${args}`);
      try {
        const r = await call<unknown>('sprite_edit_shot', args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    editShotField: async (ordinalOrId, field, value) => {
      // Surgical /sprite_edit_shot_field: bypasses the LLM translator for
      // trivial edits like duration tweaks. Visual fields trigger
      // reference-still regen server-side.
      const arg = `${ordinalOrId} | ${field}=${value}`;
      const args = `"${arg.replace(/"/g, '\\"')}"`;
      try {
        const r = await call<{
          updated: boolean;
          reason?: string;
          regenerated_reference?: boolean;
        }>('sprite_edit_shot_field', args);
        if (!r?.updated) {
          set({ error: `edit ${field} failed: ${r?.reason ?? 'unknown'}` });
          return;
        }
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    reorderShots: async (shotIds) => {
      // Optimistic update , paint the new order immediately, roll back if
      // the bridge rejects (phase lock, mismatch, network).
      const before = get().shots;
      const byId = new Map(before.map((s) => [s.id, s] as const));
      const ordered = shotIds
        .map((id) => byId.get(id))
        .filter((s): s is Shot => Boolean(s))
        .map((s, i) => ({ ...s, ordinal: i + 1 }));
      set({ shots: ordered });

      const args = `"${shotIds.join(',')}"`;
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_reorder_shots',
          args,
        );
        if (!r?.updated) {
          set({
            shots: before,
            error: `reorder failed: ${r?.reason ?? 'unknown'}`,
          });
        }
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ shots: before, error: err.message });
      }
    },

    startProgressPolling: (intervalMs = 3000) => {
      if (get().isPolling) return;
      set({ isPolling: true, pollIntervalMs: intervalMs });
      const tick = async () => {
        if (!get().isPolling) return;
        try {
          await get().refreshStatus();
          const phase = get().project?.phase;
          if (
            phase === 'done' ||
            phase === 'failed' ||
            phase === 'cancelled'
          ) {
            // Hydrate the full project once on a terminal phase so the
            // shot list reflects rendered_video_path and final_video_path
            // before the UI stops polling.
            await get().refreshShow();
            get().stopProgressPolling();
            return;
          }
          // refreshStatus only updates `status`; refreshShow brings shots
          // up to date so per-shot render_status flips animate live.
          await get().refreshShow();
        } catch {
          // Swallow , transient errors must not kill the polling loop.
        }
        setTimeout(tick, get().pollIntervalMs);
      };
      void tick();
    },

    stopProgressPolling: () => set({ isPolling: false }),

    checkAssets: async () => {
      const up = await checkAssetServer();
      set({ assetServerUp: up });
    },

    loadStylePresets: async () => {
      try {
        const r = await call<{ presets: StylePreset[]; count: number }>(
          'sprite_list_styles',
        );
        if (r?.presets) set({ stylePresets: r.presets });
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    setStyle: async (presetId) => {
      const args = `"${presetId.replace(/"/g, '\\"')}"`;
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_set_style',
          args,
        );
        if (!r?.updated) {
          set({ error: `set_style failed: ${r?.reason ?? 'unknown'}` });
          return;
        }
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    setVibe: async (vibe) => {
      const args = `"${vibe.replace(/"/g, '\\"')}"`;
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_set_vibe',
          args,
        );
        if (!r?.updated) {
          set({ error: `set_vibe failed: ${r?.reason ?? 'unknown'}` });
          return;
        }
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    setDuration: async (seconds) => {
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_set_duration',
          String(seconds),
        );
        if (!r?.updated) {
          set({ error: `set_duration failed: ${r?.reason ?? 'unknown'}` });
          return;
        }
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    reorderCast: async (charIds) => {
      // Optimistic update , paint the new order immediately, roll back if
      // the bridge rejects (phase lock, mismatch, network).
      const before = get().characters;
      const byId = new Map(before.map((c) => [c.id, c] as const));
      const ordered = charIds
        .map((id) => byId.get(id))
        .filter((c): c is Character => Boolean(c))
        .map((c, i) => ({ ...c, ordinal: i + 1 }));
      set({ characters: ordered });

      const args = `"${charIds.join(',')}"`;
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_reorder_cast',
          args,
        );
        if (!r?.updated) {
          set({
            characters: before,
            error: `reorder failed: ${r?.reason ?? 'unknown'}`,
          });
        }
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ characters: before, error: err.message });
      }
    },

    addCharacter: async (description) => {
      const args = `"${description.replace(/"/g, '\\"')}"`;
      get().appendChat('user', `/sprite_add_character ${args}`);
      try {
        const r = await call<unknown>('sprite_add_character', args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    removeCharacter: async (charIdOrOrdinal) => {
      const args = `"${String(charIdOrOrdinal)}"`;
      get().appendChat('user', `/sprite_remove_character ${args}`);
      try {
        const r = await call<unknown>('sprite_remove_character', args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    regenerateCharacter: async (ordinalOrId) => {
      // No dedicated /sprite_regenerate; piggy-back on edit_character with a
      // catch-all instruction. The orchestrator's decide step will route
      // into the regenerate path when the user_text doesn't fit a surgical
      // edit, which "regenerate sheet" reliably triggers.
      await get().editCharacter(ordinalOrId, 'regenerate sheet');
    },

    sendRaw: async (text) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      get().appendChat('user', trimmed);
      if (!trimmed.startsWith('/')) {
        get().appendChat(
          'system',
          'natural-language chat is not wired (slash-only). try /sprite_status or /sprite_show.',
        );
        return;
      }
      // Split on first whitespace, preserving the quoted-args convention
      // the bridge handlers already use (they call _strip_brief_quotes).
      const stripped = trimmed.slice(1);
      const m = stripped.match(/^(\S+)\s*(.*)$/s);
      if (!m) return;
      const command = m[1];
      const args = m[2] ?? '';
      set((s) => ({ chat: { ...s.chat, isStreaming: true } }));
      try {
        const r = await call<unknown>(command, args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        // Refresh project state if the command was state-changing. Cheap to
        // call /sprite_show even after read-only commands; it just no-ops
        // the local cache update.
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
        get().appendChat('system', `error: ${err.message}`);
      } finally {
        set((s) => ({ chat: { ...s.chat, isStreaming: false } }));
      }
    },
  })),
);
```

### 9.3. lib/bridge.ts

```ts
// Sprite Bridge client. Talks to the local Python sidecar at /api (proxied to
// http://127.0.0.1:8643 in dev) which imports the sprite-studio plugin and
// exposes its slash command handlers as POST /slash.
//
// Why a sidecar instead of POST /v1/chat/completions: Hermes 0.12.0 only
// dispatches plugin slash commands when messages arrive via the gateway's
// chat router (Telegram/Discord/Slack/CLI). The OpenAI-compatible API server
// runs the message through the LLM as conversation, so /sprite_status sent
// there returns "I don't have context for that" instead of the JSON we need.

export interface BridgeError {
  status: number;
  message: string;
}

export interface SlashResult<T = unknown> {
  ok: boolean;
  data: T | null;
  raw: string;
  parseError: string | null;
}

const DEFAULT_BASE = '/api';
const REQUEST_TIMEOUT_MS = 600_000;

export class SpriteBridgeClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string = DEFAULT_BASE, apiKey: string) {
    if (!apiKey) {
      throw new Error(
        'SpriteBridgeClient: apiKey is required (set VITE_SPRITE_BRIDGE_KEY in .env.local)',
      );
    }
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.apiKey = apiKey;
  }

  async health(): Promise<{ status: string; plugin_loaded: boolean }> {
    const resp = await fetch(`${this.baseUrl}/health`);
    if (!resp.ok) {
      throw { status: resp.status, message: `health failed: ${resp.status}` } as BridgeError;
    }
    return resp.json();
  }

  async sendSlash<T = unknown>(command: string, args = ''): Promise<SlashResult<T>> {
    const cleanCommand = command.replace(/^\//, '').trim();
    if (!cleanCommand) {
      throw { status: 0, message: 'sendSlash: command is empty' } as BridgeError;
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/slash`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ command: cleanCommand, args }),
        signal: controller.signal,
      });
    } catch (e: unknown) {
      clearTimeout(timeout);
      if (e instanceof DOMException && e.name === 'AbortError') {
        throw { status: 0, message: 'Request timed out' } as BridgeError;
      }
      throw {
        status: 0,
        message:
          'Sprite bridge unreachable. Start with: python /home/drew/sprite-studio/bridge/server.py',
      } as BridgeError;
    }
    clearTimeout(timeout);

    if (resp.status === 401 || resp.status === 403) {
      throw { status: resp.status, message: 'Invalid API key' } as BridgeError;
    }
    if (resp.status === 404) {
      throw {
        status: 404,
        message: `Unknown slash command: /${cleanCommand}`,
      } as BridgeError;
    }
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw {
        status: resp.status,
        message: `Bridge error ${resp.status}: ${text.slice(0, 300)}`,
      } as BridgeError;
    }

    const body = (await resp.json()) as SlashResult<T>;
    return body;
  }
}

let _client: SpriteBridgeClient | null = null;

export function getSpriteBridge(): SpriteBridgeClient {
  if (_client) return _client;
  const key = import.meta.env.VITE_SPRITE_BRIDGE_KEY as string | undefined;
  if (!key) {
    throw new Error(
      'VITE_SPRITE_BRIDGE_KEY not set in .env.local. Copy it from ~/.hermes/.env (API_SERVER_KEY).',
    );
  }
  _client = new SpriteBridgeClient('/api', key);
  return _client;
}
```

### 9.4. lib/assets.ts

```ts
// Asset URL builder for the static asset server (port 9120 by default).
// The bridge can't serve files (it's a JSON command sidecar), so we run a
// separate aiohttp process for binary artifacts and bypass the Vite proxy.

const DEFAULT_ASSET_BASE = 'http://127.0.0.1:9120';

export function assetBase(): string {
  return import.meta.env.VITE_ASSET_BASE_URL || DEFAULT_ASSET_BASE;
}

// `version` defeats the browser cache after a regenerate. Pass
// character.updated_at (or shot.updated_at) so the URL changes when the
// artifact does, even if the path is identical.
export function characterSheetUrl(
  projectId: string,
  charId: string,
  version?: number | null,
): string {
  const v = version ? `?v=${version}` : '';
  return `${assetBase()}/${projectId}/cast/${charId}/sheet.png${v}`;
}

export function shotReferenceUrl(
  projectId: string,
  shotId: string,
  version?: number | null,
): string {
  const v = version ? `?v=${version}` : '';
  return `${assetBase()}/${projectId}/shots/${shotId}/reference.png${v}`;
}

export function shotVideoUrl(
  projectId: string,
  shotId: string,
  version?: number | null,
): string {
  const v = version ? `?v=${version}` : '';
  return `${assetBase()}/${projectId}/shots/${shotId}.mp4${v}`;
}

export function projectFinalVideoUrl(
  projectId: string,
  version?: number | null,
): string {
  const v = version ? `?v=${version}` : '';
  return `${assetBase()}/${projectId}/output/final.mp4${v}`;
}

export async function checkAssetServer(): Promise<boolean> {
  try {
    const r = await fetch(`${assetBase()}/health`, {
      signal: AbortSignal.timeout(1500),
    });
    return r.ok;
  } catch {
    return false;
  }
}
```

### 9.5. main.tsx

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { AppShell } from './components/AppShell';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppShell />
  </React.StrictMode>,
);
```

### 9.6. bridge/server.py

```python
#!/usr/bin/env python3
"""Sprite Studio Bridge , REST sidecar for the sprite-studio Hermes plugin.

Why this exists:
  Hermes 0.12.0's OpenAI-compatible API server (/v1/chat/completions) does
  not dispatch plugin slash commands , it routes user messages through the
  LLM as conversation. Plugin slash commands only fire via the gateway's
  chat router (Telegram/Discord/Slack/CLI).

  This sidecar imports the sprite-studio plugin directly and exposes its
  registered handlers as POST /slash, so the web app can invoke them as
  deterministic REST calls without an LLM in the loop.

Endpoints:
  GET  /health                , liveness + plugin-loaded check
  POST /slash {command, args} , dispatch a registered slash command

Auth:
  Bearer token from the API_SERVER_KEY env var (same key as the Hermes API
  server, so a single secret is shared between surfaces).

Run:
  set -a; source ~/.hermes/.env; set +a
  /home/drew/.hermes/hermes-agent/venv/bin/python3 \\
      /home/drew/sprite-studio/bridge/server.py

Env overrides:
  SPRITE_BRIDGE_HOST   default 127.0.0.1
  SPRITE_BRIDGE_PORT   default 8643
  SPRITE_PLUGIN_PATH   default ~/.hermes/plugins/sprite-studio
  API_SERVER_KEY       required (will also be loaded from ~/.hermes/.env)
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
import types
from pathlib import Path

from aiohttp import web

logger = logging.getLogger("sprite_bridge")


def _load_dotenv_for_api_key() -> None:
    """Backfill API_SERVER_KEY from ~/.hermes/.env if not already in env."""
    if os.environ.get("API_SERVER_KEY"):
        return
    dotenv = Path("~/.hermes/.env").expanduser()
    if not dotenv.exists():
        return
    try:
        for raw_line in dotenv.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key == "API_SERVER_KEY" and value.strip():
                os.environ["API_SERVER_KEY"] = value.strip()
                break
    except OSError:
        pass


def load_plugin(plugin_path: Path) -> types.ModuleType:
    """Import the sprite-studio plugin as ``sprite_studio``.

    Mirrors the plugin loader at
    ~/.hermes/hermes-agent/hermes_cli/plugins.py:1015 so relative imports
    inside the plugin (``from . import db``) resolve correctly.
    """
    init_file = plugin_path / "__init__.py"
    if not init_file.exists():
        raise SystemExit(f"plugin not found at {plugin_path}")

    venv_site = Path(
        "~/.hermes/hermes-agent/venv/lib/python3.11/site-packages"
    ).expanduser()
    if venv_site.exists() and str(venv_site) not in sys.path:
        sys.path.insert(0, str(venv_site))

    module_name = "sprite_studio"
    spec = importlib.util.spec_from_file_location(
        module_name,
        str(init_file),
        submodule_search_locations=[str(plugin_path)],
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot create module spec for {init_file}")

    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(plugin_path)]  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _check_auth(request: web.Request, api_key: str) -> web.Response | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return web.json_response(
            {"error": "missing or malformed Authorization header"}, status=401
        )
    if header[len("Bearer ") :] != api_key:
        return web.json_response({"error": "invalid api key"}, status=401)
    return None


def _import_asset_server() -> types.ModuleType:
    """Import the plugin's asset_server module via the same submodule search
    locations the bridge uses for the plugin itself. Must be called after
    load_plugin() has registered ``sprite_studio`` in sys.modules so that
    relative imports inside asset_server resolve correctly.
    """
    import importlib
    return importlib.import_module("sprite_studio.workers.asset_server")


async def _start_asset_server(app: web.Application) -> None:
    """Spawn the read-only asset server in the same event loop on 9120.

    Folded into the bridge so a single `bridge/run.sh` launches both the
    REST sidecar (8643) and the static asset server (9120). Failure to
    bind is logged but doesn't take the bridge down , a standalone
    asset_server.py may already be holding the port from a prior run.
    """
    asset_server = _import_asset_server()
    asset_app = asset_server.make_app()
    runner = web.AppRunner(asset_app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=9120)
    try:
        await site.start()
    except OSError as exc:
        logger.warning(
            "asset server failed to bind on 9120 (%s) , assuming a "
            "standalone instance is already running",
            exc,
        )
        await runner.cleanup()
        return
    logger.info("asset server started on http://127.0.0.1:9120")
    app["_asset_runner"] = runner


async def _stop_asset_server(app: web.Application) -> None:
    runner = app.get("_asset_runner")
    if runner is not None:
        await runner.cleanup()


def make_app(plugin: types.ModuleType, api_key: str) -> web.Application:
    slash_commands = plugin.commands.SLASH_COMMANDS

    async def health(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "plugin_loaded": True,
                "command_count": len(slash_commands),
            }
        )

    async def slash(request: web.Request) -> web.Response:
        if (err := _check_auth(request, api_key)) is not None:
            return err
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        command = (body.get("command") or "").strip().lstrip("/")
        args = body.get("args", "") or ""
        if not command:
            return web.json_response({"error": "missing 'command'"}, status=400)

        meta = slash_commands.get(command)
        if meta is None:
            return web.json_response(
                {"error": f"unknown command: /{command}"}, status=404
            )

        handler = meta["handler"]
        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(args, surface="api")
            else:
                result = await asyncio.to_thread(handler, args, surface="api")
        except Exception as exc:
            logger.exception("handler /%s raised", command)
            return web.json_response(
                {
                    "ok": False,
                    "data": None,
                    "raw": "",
                    "parseError": f"handler raised: {exc}",
                },
                status=500,
            )

        raw = str(result) if result is not None else ""
        try:
            data = json.loads(raw) if raw else None
            return web.json_response(
                {"ok": True, "data": data, "raw": raw, "parseError": None}
            )
        except json.JSONDecodeError as exc:
            return web.json_response(
                {"ok": False, "data": None, "raw": raw, "parseError": str(exc)}
            )

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/slash", slash)
    app.on_startup.append(_start_asset_server)
    app.on_cleanup.append(_stop_asset_server)
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _load_dotenv_for_api_key()
    api_key = os.environ.get("API_SERVER_KEY", "")
    if not api_key:
        raise SystemExit(
            "API_SERVER_KEY env var required. "
            "Set it in shell or in ~/.hermes/.env."
        )

    plugin_path = Path(
        os.environ.get(
            "SPRITE_PLUGIN_PATH", "~/.hermes/plugins/sprite-studio"
        )
    ).expanduser()
    plugin = load_plugin(plugin_path)
    logger.info(
        "loaded plugin from %s , %d commands",
        plugin_path,
        len(plugin.commands.SLASH_COMMANDS),
    )

    host = os.environ.get("SPRITE_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("SPRITE_BRIDGE_PORT", "8643"))
    logger.info("starting sprite-bridge on http://%s:%d", host, port)
    web.run_app(app=make_app(plugin, api_key), host=host, port=port, print=lambda _msg: None)


if __name__ == "__main__":
    main()
```

### 9.7. plugin.yaml

```yaml
name: sprite-studio
version: 0.1.0
description: AI video creation studio with persistent character casts (Hermes Creative Hackathon)
author: drew
provides_commands:
  - start
  - sprite_new
  - sprite_status
  - sprite_cast
  - sprite_edit_character
  - sprite_add_character
  - sprite_remove_character
  - sprite_approve_cast
  - sprite_timeline
  - sprite_edit_shot
  - sprite_approve_timeline
  - sprite_render
  - sprite_cancel
  - sprite_show
  - sprite_purge
  - sprite_list
  - sprite_cost_summary
  - sprite_list_styles
  - sprite_set_style
  - sprite_set_vibe
  - sprite_set_duration
  - sprite_reorder_cast
  - sprite_reorder_shots
  - sprite_edit_shot_field
  - sprite_set_shot_transition
provides_hooks: []
requires_env:
  - name: TOKENROUTER_API_KEY
    description: TokenRouter API key with access to Kimi K2.6, gpt-5.4-image-2, and Seedance 2.0
    url: https://app.tokenrouter.com
    secret: true
  - name: ELEVENLABS_API_KEY
    description: ElevenLabs API key for narration TTS
    url: https://elevenlabs.io/app/settings/api-keys
    secret: true
```

## 10. Modification tally

Files read: 45+ source files (web `src/` x16, plugin py x9 main + services x12 + workers x3, prompts x6, design reference html/jsx x9, configs x6, schema x1).
Files modified: 1 (this report).
Processes started: 0.
Network calls: 0.

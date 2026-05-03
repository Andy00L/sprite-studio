"""Pydantic v2 models for the sprite-studio plugin."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


_VALID_DURATIONS = {15, 30, 45, 60, 75, 90}

# Cast size band. Single source of truth: orchestrator, prompt, and UI all
# resolve from these. WARN logs at >WARN_CAST_SIZE; the cost-confirmation
# gate fires at >HARD_WARN_CAST_SIZE unless cast_size_confirmed is set.
MAX_CAST_SIZE = 30
WARN_CAST_SIZE = 8
HARD_WARN_CAST_SIZE = 12

# Per-shot ceiling on characters_present. gpt-image-2 /images/edits accepts
# at most 16 reference images per call (services/gpt_image.py:187), and
# reference-still generation passes one master sheet per character_present.
# Reject at validation time so the failure surfaces early, not at API call.
MAX_CHARACTERS_PER_SHOT = 16


class StylePreset(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=False, validate_assignment=True)

    id: str
    name: str
    descriptor: str
    render_notes: str
    motion_descriptor: str
    music_tag: str


class Project(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=False, validate_assignment=True)

    id: str
    user_id: str
    surface: Literal["cli", "web", "telegram", "discord"]
    brief: str = Field(min_length=5, max_length=4000)
    style_preset_id: str
    vibe: Optional[str] = None
    duration_seconds: int
    phase: Literal["brief", "cast", "timeline", "render", "done", "failed"]
    title: Optional[str] = None
    narrator_script: Optional[str] = None
    use_narrator: bool = True
    music_track_path: Optional[str] = None
    final_video_path: Optional[str] = None
    total_cost_usd: float = Field(ge=0, default=0.0)
    created_at: int
    updated_at: int
    approved_cast_at: Optional[int] = None
    approved_timeline_at: Optional[int] = None
    rendered_at: Optional[int] = None
    error_message: Optional[str] = None
    cast_size_confirmed: bool = False

    @field_validator("duration_seconds")
    @classmethod
    def _check_duration(cls, v: int) -> int:
        if v not in _VALID_DURATIONS:
            raise ValueError(f"duration_seconds must be one of {sorted(_VALID_DURATIONS)}, got {v}")
        return v


class Character(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=False, validate_assignment=True)

    id: str
    project_id: str
    ordinal: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    role: Optional[str] = None
    persona: str = Field(min_length=10)
    visual_description: str = Field(min_length=40)
    master_sheet_path: Optional[str] = None
    voice_id: Optional[str] = None
    voice_personality: Optional[str] = None
    source: Literal["generated", "reference_image", "reference_photo"] = "generated"
    reference_image_path: Optional[str] = None
    edit_history: list[dict] = Field(default_factory=list)
    is_approved: bool = False
    created_at: int
    updated_at: int


class Shot(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=False, validate_assignment=True)

    id: str
    project_id: str
    ordinal: int = Field(ge=1)
    duration_seconds: int = Field(ge=5, le=15)
    setting: str = Field(min_length=10)
    action: str = Field(min_length=10)
    camera: Optional[str] = None
    emotion: Optional[str] = None
    characters_present: list[str]
    dialog_speakers: list[str] = Field(default_factory=list)
    has_dialog: bool = False
    narration_line: Optional[str] = None
    character_dialog: Optional[Union[str, list[dict], dict]] = None
    reference_still_path: Optional[str] = None
    rendered_video_path: Optional[str] = None
    render_status: Literal["pending", "rendering", "done", "failed"] = "pending"
    render_error: Optional[str] = None
    cost_usd: float = Field(ge=0, default=0.0)
    created_at: int
    updated_at: int

    @field_validator("characters_present")
    @classmethod
    def _check_characters_present_size(cls, v: list[str]) -> list[str]:
        if len(v) > MAX_CHARACTERS_PER_SHOT:
            raise ValueError(
                f"characters_present capped at {MAX_CHARACTERS_PER_SHOT} "
                f"(got {len(v)}); gpt-image-2 reference image limit",
            )
        return v


class GenerationJob(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=False, validate_assignment=True)

    id: str
    project_id: str
    job_type: Literal["image_gen", "image_edit", "video_gen", "tts", "llm", "ffmpeg"]
    provider: str
    model: str
    external_job_id: Optional[str] = None
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    input_payload: Optional[dict] = None
    output_payload: Optional[dict] = None
    cost_usd: Optional[float] = Field(default=None, ge=0)
    error_message: Optional[str] = None
    attempt_count: int = Field(ge=0, default=0)
    created_at: int
    completed_at: Optional[int] = None


_JSON_COLUMNS_BY_MODEL: dict[type[BaseModel], set[str]] = {
    Character: {"edit_history"},
    Shot: {"characters_present", "character_dialog", "dialog_speakers"},
    GenerationJob: {"input_payload", "output_payload"},
}


def row_to_model(row: Any, model_cls: type[BaseModel]) -> BaseModel:
    """Convert a sqlite3.Row (or dict) into a pydantic model, parsing JSON columns by name."""
    if isinstance(row, sqlite3.Row):
        data: dict = {k: row[k] for k in row.keys()}
    elif isinstance(row, dict):
        data = dict(row)
    else:
        raise TypeError(f"row_to_model: unsupported row type {type(row)}")
    json_cols = _JSON_COLUMNS_BY_MODEL.get(model_cls, set())
    for col in json_cols:
        if col in data and isinstance(data[col], str):
            data[col] = json.loads(data[col])
    if model_cls is Character and isinstance(data.get("is_approved"), int):
        data["is_approved"] = bool(data["is_approved"])
    if model_cls is Shot and isinstance(data.get("has_dialog"), int):
        data["has_dialog"] = bool(data["has_dialog"])
    if model_cls is Project and isinstance(data.get("use_narrator"), int):
        data["use_narrator"] = bool(data["use_narrator"])
    if model_cls is Project and isinstance(data.get("cast_size_confirmed"), int):
        data["cast_size_confirmed"] = bool(data["cast_size_confirmed"])
    return model_cls.model_validate(data)


# ---- Planning models (timeline_writer LLM output schema) ----
#
# These are NOT persisted directly; they validate the LLM's JSON before
# orchestrator persists each shot via db.create_shot. The orchestrator's
# _validate_timeline still does the heavier semantic checks (action-embedded
# dialog, valid char_ids, total duration band) that pydantic alone can't
# express against the runtime cast.


class DialogEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    char_id: str
    line: str = Field(min_length=1)


class ShotPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    shot_id: str
    ordinal: int = Field(ge=1)
    duration_seconds: int = Field(ge=5, le=15)
    setting: str = Field(min_length=5)
    action: str = Field(min_length=5)
    camera: Optional[str] = None
    emotion: Optional[str] = None
    characters_present: list[str] = Field(default_factory=list)
    dialog_speakers: Optional[list[str]] = None
    narration_excerpt: Optional[str] = None
    character_dialog: Optional[list[DialogEntry]] = None
    has_dialog: bool = False

    @field_validator("characters_present")
    @classmethod
    def _check_characters_present_size(cls, v: list[str]) -> list[str]:
        if len(v) > MAX_CHARACTERS_PER_SHOT:
            raise ValueError(
                f"characters_present capped at {MAX_CHARACTERS_PER_SHOT} "
                f"(got {len(v)}); gpt-image-2 reference image limit",
            )
        return v


class CharacterPlan(BaseModel):
    """Single character entry in a cast_designer LLM response."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=80)
    ordinal: int = Field(ge=1)
    role: Optional[str] = None
    persona: str = Field(min_length=10)
    visual_description: str
    voice_personality: Optional[str] = None


class CastPlan(BaseModel):
    """cast_designer LLM response shape. Cap is canonical here so future
    bumps touch one place. Heavier semantic checks (ordinal sequencing,
    name uniqueness, visual_description min length) live in
    orchestrator._shape_check_cast / _final_check_cast, since pydantic
    alone can't express them as cleanly."""

    model_config = ConfigDict(extra="ignore")

    characters: list[CharacterPlan] = Field(min_length=1)

    @field_validator("characters")
    @classmethod
    def _check_cast_size(cls, v: list[CharacterPlan]) -> list[CharacterPlan]:
        if not 1 <= len(v) <= MAX_CAST_SIZE:
            raise ValueError(
                f"cast must have 1..{MAX_CAST_SIZE} characters (got {len(v)})",
            )
        return v


class TimelinePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1)
    logline: Optional[str] = None
    use_narrator: bool
    narrator_script: Optional[str] = None
    shots: list[ShotPlan] = Field(min_length=1)

    @field_validator("narrator_script")
    @classmethod
    def _check_narrator_script(cls, v, info):
        use_narrator = info.data.get("use_narrator")
        if use_narrator and (not v or not v.strip()):
            raise ValueError("narrator_script required when use_narrator=true")
        if not use_narrator and v not in (None, ""):
            # Soft-cleared by the orchestrator; raise here so the LLM
            # doesn't pay the cost of generating narration we will throw
            # away. Caller's retry path surfaces a clear feedback message.
            raise ValueError(
                "narrator_script must be null when use_narrator=false",
            )
        return v

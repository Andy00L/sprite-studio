"""ProjectOrchestrator — the only thing slash-command handlers should
talk to. Owns brief → cast phase transitions, character generation, and
graceful shutdown of in-flight work.

Failure-mode handling lives in this module by design; the underlying
service layer raises typed errors and the orchestrator decides what is
recoverable vs. what aborts a project.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Coroutine, Optional

from . import db
from .models import (
    HARD_WARN_CAST_SIZE,
    MAX_CAST_SIZE,
    MAX_CHARACTERS_PER_SHOT,
    StylePreset,
    WARN_CAST_SIZE,
)
from .prompts import load_prompt
from .services import (
    ChatClient,
    ImageClient,
    ProviderContentPolicyError,
    ProviderResponseShapeError,
    QUALITY_HIGH,
    SIZE_PORTRAIT,
    SIZE_SQUARE,
    SpriteStudioError,
    VideoClient,
    VoiceClient,
)
from .services import elevenlabs_voices
from .style_presets import (
    DEFAULT_PRESET_ID,
    StylePresetLoadError,
    get_preset,
    is_valid_preset_id,
    load_presets,
)


logger = logging.getLogger("sprite_studio.orchestrator")

KIMI_MODEL = "moonshotai/kimi-k2.6"
DEFAULT_DURATION_SECONDS = 60
VALID_DURATIONS = {15, 30, 45, 60, 75, 90}
VISUAL_DESC_MIN = 40
# Kimi K2.6 emits a hidden reasoning_content trace that COUNTS against
# max_tokens (verified live: 4000 and 8000 caps both produced an empty
# content because reasoning consumed the full budget). Leave max_tokens
# unset so the model can finish its reasoning and still emit ~3000 tokens
# of JSON. Server-side default cap is large enough.
TIMELINE_MAX_TOKENS: Optional[int] = None
# Reasoning + JSON-mode output regularly exceeds the 180s default
# (~4-5 min observed on uncapped calls).
TIMELINE_READ_TIMEOUT = 540.0
# Cast designer JSON output stays small for typical 1..4-character casts,
# but Kimi's reasoning trace can still suppress emission for ~170s on
# medium prompts (verified in production: cast-designer hit ReadTimeout
# at the previous 180s default). 300s gives ~2x headroom. The cap is
# MAX_CAST_SIZE (currently 30); larger casts add ~250 tokens each but
# stay well within the cast designer's output budget.
CAST_READ_TIMEOUT = 300.0
TIMELINE_MIN_SHOTS = 1
TIMELINE_MAX_SHOTS = 12
TIMELINE_DURATION_TOLERANCE = 2

# Camera enum is canonical per blueprint section 6.3 SHOT JSON.
ALLOWED_CAMERAS = {
    "static wide",
    "slow push-in",
    "pull-back reveal",
    "tracking",
    "handheld follow",
    "overhead",
    "low angle hero",
}

PROJECTS_ROOT = Path("~/.hermes/plugins/sprite-studio/projects").expanduser()


def _resolve_ref_paths(project_id: str, asset_paths: list[str]) -> list[Path]:
    """Convert /<pid>/refs/<file> asset-server paths into resolved disk paths,
    silently dropping anything that escapes the project's refs/ dir or is
    missing. Used by the sheet generator to feed gpt-image-2 multi-ref input.
    """
    if not asset_paths:
        return []
    refs_root = (PROJECTS_ROOT / project_id / "refs").resolve()
    expected_prefix = f"/{project_id}/refs/"
    out: list[Path] = []
    for asset_path in asset_paths:
        if not isinstance(asset_path, str) or not asset_path.startswith(expected_prefix):
            continue
        rel = asset_path[len(expected_prefix):]
        if not rel or ".." in rel or "\\" in rel or "\x00" in rel:
            continue
        candidate = (refs_root / rel).resolve()
        try:
            candidate.relative_to(refs_root)
        except ValueError:
            continue
        if candidate.is_file():
            out.append(candidate)
    return out

# Hardened JSON-only system message. Used for the second-attempt retry
# when chat_json returns non-JSON content.
_JSON_STRICT_SYSTEM = (
    "You are a JSON-only API. Reply ONLY with raw valid JSON matching "
    "the requested schema. No markdown, no code fences, no prose, no "
    "leading or trailing commentary."
)

# Strip ASCII control characters (0x00-0x1F, 0x7F) except \n (0x0A) and \t (0x09).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B-\x1F\x7F]")


def _sanitize(text: str) -> str:
    if not text:
        return text
    return _CONTROL_CHARS_RE.sub("", text)


def _truncate_for_log(value: Optional[str], limit: int = 80) -> str:
    if value is None:
        return "<none>"
    head = value[:limit].replace("\n", " ")
    return f"{head!r}(len={len(value)})"


class OrchestratorError(RuntimeError):
    """Base class for orchestrator-level user-surfaceable errors."""


class ProjectInWrongPhaseError(OrchestratorError):
    pass


class RenderInProgressError(OrchestratorError):
    pass


class CastGenerationFailedError(OrchestratorError):
    pass


class CastIncompleteError(OrchestratorError):
    """A cast advance / approve / repair gate found ≥1 sheet missing on disk.

    Carries the per-character missing list so the slash-command surface can
    tell the user exactly which characters need /sprite_repair_cast (or a
    targeted /sprite_edit_character regenerate). Distinct from a generation
    failure: the DB has paths, but the bytes are not there.
    """

    def __init__(self, project_id: str, missing: list[tuple[str, str]]) -> None:
        self.project_id = project_id
        self.missing = missing
        names = ", ".join(n for n, _ in missing) or "(none)"
        super().__init__(
            f"{len(missing)} character sheet(s) missing on disk for "
            f"project {project_id}: {names}",
        )


class CharacterNotFoundError(OrchestratorError):
    pass


class CastFullError(OrchestratorError):
    pass


class CastTooSmallError(OrchestratorError):
    pass


class CastConfirmationRequiredError(OrchestratorError):
    """Cost-confirmation gate: cast designer proposed a large cast.

    Raised by advance_to_cast_phase when the LLM-proposed cast exceeds
    HARD_WARN_CAST_SIZE and the project has not yet been flagged with
    cast_size_confirmed=True. The slash-command handler surfaces the
    proposed size and estimated cost; /sprite_approve_cast_size flips
    the flag and a follow-up /sprite_cast then proceeds.
    """

    def __init__(self, *, proposed_size: int, estimated_cost_usd: float) -> None:
        self.proposed_size = proposed_size
        self.estimated_cost_usd = estimated_cost_usd
        super().__init__(
            f"cast designer proposed {proposed_size} characters "
            f"(estimated cost ~${estimated_cost_usd:.2f}); "
            f"reply /sprite_approve_cast_size or edit the brief.",
        )


class TimelineGenerationFailedError(OrchestratorError):
    pass


class ShotNotFoundError(OrchestratorError):
    pass


class TimelineNotReadyError(OrchestratorError):
    pass


class TimelineFullError(OrchestratorError):
    """Cap reached: timeline already has TIMELINE_MAX_SHOTS shots."""


class TimelineLastShotError(OrchestratorError):
    """Floor reached: deleting would leave the timeline empty."""


class ProjectBusyError(OrchestratorError):
    """Raised by delete_project when in-flight task cancellation does not
    complete within the timeout. Caller (slash handler / HTTP route) should
    surface this as a retryable error, not a hard failure.
    """

    def __init__(self, project_id: str, reason: str) -> None:
        super().__init__(f"project busy: {reason}")
        self.project_id = project_id
        self.reason = reason


# Strong-reference set for fire-and-forget background tasks. Per the asyncio
# docs (https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task):
#   "Save a reference to the result of this function, to avoid a task
#    disappearing mid-execution. The event loop only keeps weak references
#    to tasks. A task that isn't referenced elsewhere may get garbage
#    collected at any time, even before it's done."
# We hold each task in the set, then discard it via add_done_callback once
# it finishes so the set doesn't leak.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def spawn_background(
    coro: Coroutine[Any, Any, Any], *, name: str,
) -> asyncio.Task[Any]:
    """Schedule a coroutine as a tracked, fire-and-forget asyncio Task.

    Strong-references the task in _BACKGROUND_TASKS to defeat the GC
    weak-reference behavior documented at
    https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task,
    then auto-removes via add_done_callback when the task completes.
    """
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    task.add_done_callback(_log_task_result)
    return task


def _log_task_result(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        logger.info("background task %s cancelled", task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "background task %s raised", task.get_name(), exc_info=exc,
        )
    else:
        logger.info("background task %s completed", task.get_name())


def has_background_task(name: str) -> bool:
    """Return True iff a tracked background task with this name is in flight."""
    for t in _BACKGROUND_TASKS:
        if t.get_name() == name and not t.done():
            return True
    return False


def _project_dir_size_bytes(path: Path) -> int:
    """Sum sizes of every regular file under path. Returns 0 if path is
    missing. Used by delete_project to report freed disk to the caller.
    """
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


# Patterns that may leak through into error_message strings: filesystem
# paths, bearer tokens, sk-... API keys, long hex runs (hashes/tokens).
# error_message is surfaced to the UI, so we strip anything sensitive
# before persisting.
_SANITIZE_PATTERNS = (
    (re.compile(r"/home/[^\s]+"), "<path>"),
    (re.compile(r"/root/[^\s]+"), "<path>"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "<key>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9_.-]+"), "Bearer <key>"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<hash>"),
)


def _sanitize_error(msg: str) -> str:
    """Scrub paths, tokens, and long hex runs from a user-visible error string.

    Caps length at 500 to keep the error_message column small and to avoid
    leaking large prompt fragments or stack traces.
    """
    if not msg:
        return msg
    out = msg
    for pat, repl in _SANITIZE_PATTERNS:
        out = pat.sub(repl, out)
    return out[:500]


class ProjectOrchestrator:
    def __init__(
        self,
        chat_client: Optional[ChatClient] = None,
        image_client: Optional[ImageClient] = None,
        video_client: Optional[VideoClient] = None,
        voice_client: Optional[VoiceClient] = None,
    ) -> None:
        self._chat = chat_client or ChatClient()
        self._image = image_client or ImageClient()
        # Video/voice clients are constructed lazily so a process that never
        # renders does not require TOKENROUTER_API_KEY / ELEVENLABS_API_KEY.
        self._video_override = video_client
        self._voice_override = voice_client
        self._video_client: Optional[VideoClient] = None
        self._voice_client: Optional[VoiceClient] = None
        self._render_tasks: dict[str, asyncio.Task] = {}

    @property
    def _video(self) -> VideoClient:
        if self._video_override is not None:
            return self._video_override
        if self._video_client is None:
            self._video_client = VideoClient()
        return self._video_client

    @property
    def _voice(self) -> VoiceClient:
        if self._voice_override is not None:
            return self._voice_override
        if self._voice_client is None:
            self._voice_client = VoiceClient()
        return self._voice_client

    # ---------------------- public API ----------------------

    async def start_project(
        self,
        *,
        brief: str,
        surface: str,
        user_id: str,
    ) -> dict:
        cleaned = _sanitize(brief).strip()
        if not (5 <= len(cleaned) <= 4000):
            raise ValueError(
                f"brief length must be 5..4000 chars (got {len(cleaned)})",
            )

        in_flight = db.latest_project_for_user(user_id, phase="render")
        if in_flight is not None:
            raise RenderInProgressError(
                "A render is in progress. Run /sprite_status to check, "
                "or /sprite_cancel to stop it.",
            )

        project = db.create_project(
            user_id=user_id,
            surface=surface,
            brief=cleaned,
            style_preset_id=DEFAULT_PRESET_ID,
            duration_seconds=DEFAULT_DURATION_SECONDS,
        )
        project_id = project["id"]

        # Create the on-disk project root immediately. Refs upload (asset
        # server) and cast subdir creation both assume this exists. Without
        # it, the deferred-cast flow 404s on POST /<pid>/refs/upload because
        # the project_dir.is_dir() check fails (asset_server.py:182).
        # mkdir is idempotent (exist_ok=True); ULIDs guarantee path uniqueness.
        # On OSError (disk full, permission denied) we roll back the DB row so
        # the user does not end up with an unusable project.
        project_dir = PROJECTS_ROOT / project_id
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.exception(
                "failed to create project_dir for %s", project_id,
            )
            try:
                db.delete_project(project_id)
            except Exception:
                logger.exception(
                    "failed to roll back project row %s after mkdir failure",
                    project_id,
                )
            raise SpriteStudioError(
                _sanitize_error(f"could not create project directory: {e}"),
            ) from e

        logger.info(
            "project created project_id=%s user=%s surface=%s brief=%s dir=%s",
            project_id,
            user_id,
            surface,
            _truncate_for_log(cleaned),
            project_dir,
        )

        styles_json = self._styles_for_prompt()
        prompt_body = load_prompt(
            "brief_clarifier",
            styles_json=styles_json,
            brief=cleaned,
        )

        try:
            parsed = await self._chat_json_with_retry(
                project_id=project_id,
                user_prompt=prompt_body,
                base_system="You are the Sprite Studio Brief Clarifier.",
            )
        except SpriteStudioError as e:
            db.set_phase(project_id, "failed", error_message=str(e))
            raise

        needs_clarification = bool(parsed.get("needs_clarification"))
        questions_raw = parsed.get("questions") or []
        questions = [str(q) for q in questions_raw if isinstance(q, (str, int))][:3]
        auto = parsed.get("auto_decisions") or {}

        applied_auto = self._apply_auto_decisions(project_id, auto)

        # An LLM that signals needs_clarification=true with no actual questions
        # is signalling confidence; treat that as ready-to-advance instead of
        # surfacing an empty question prompt the frontend can't answer.
        if not questions:
            needs_clarification = False

        return {
            "project_id": project_id,
            "brief": cleaned,
            "phase": "brief",
            "auto_decisions": applied_auto,
            "needs_clarification": needs_clarification,
            "questions": questions,
        }

    async def advance_to_cast_phase(self, *, project_id: str) -> dict:
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"unknown project: {project_id}")
        if project["phase"] != "brief":
            raise ProjectInWrongPhaseError(
                f"project {project_id} is in phase {project['phase']!r}, "
                f"expected 'brief'",
            )

        try:
            preset = get_preset(project["style_preset_id"])
        except StylePresetLoadError:
            preset = get_preset(DEFAULT_PRESET_ID)
            db.update_project(project_id, style_preset_id=DEFAULT_PRESET_ID)

        prompt_body = load_prompt(
            "cast_designer",
            brief=project["brief"],
            style_descriptor=preset.descriptor,
            vibe=project.get("vibe") or "",
            duration_seconds=project["duration_seconds"],
        )

        try:
            parsed = await self._chat_json_with_retry(
                project_id=project_id,
                user_prompt=prompt_body,
                base_system="You are the Sprite Studio Cast Designer.",
                read_timeout_seconds=CAST_READ_TIMEOUT,
            )
        except SpriteStudioError as e:
            db.set_phase(project_id, "failed", error_message=_sanitize_error(str(e)))
            raise

        try:
            characters_input = self._shape_check_cast(parsed)
        except ValueError as e:
            db.set_phase(project_id, "failed", error_message=str(e))
            raise

        # Cost guard: large casts are expensive (~$0.21/sheet at quality=high
        # plus ~$0.21 per surgical edit). Log a warning above WARN_CAST_SIZE,
        # require explicit /sprite_approve_cast_size confirmation above
        # HARD_WARN_CAST_SIZE. The project stays in 'brief' phase so the user
        # can either approve and re-run /sprite_cast, or edit the brief to
        # reduce the cast.
        n = len(characters_input)
        if n > WARN_CAST_SIZE:
            logger.warning(
                "cast phase: large cast n=%d project=%s "
                "(~$%.2f for sheets at quality=high)",
                n, project_id, n * 0.21,
            )
        if n > HARD_WARN_CAST_SIZE and not bool(project.get("cast_size_confirmed")):
            raise CastConfirmationRequiredError(
                proposed_size=n,
                estimated_cost_usd=n * 0.42,
            )

        # Per-character retry for too-short visual_description — capped
        # at one retry per character (RULE 3, task failure modes).
        await self._expand_short_visuals(project_id, characters_input)

        try:
            self._final_check_cast(characters_input)
        except ValueError as e:
            db.set_phase(project_id, "failed", error_message=str(e))
            raise

        # Insert character rows up-front so the IDs/ordinals are stable
        # before we start parallel image gen.
        inserted: list[dict] = []
        for entry in characters_input:
            row = db.create_character(
                project_id=project_id,
                ordinal=entry["ordinal"],
                name=entry["name"],
                role=entry.get("role"),
                persona=entry["persona"],
                visual_description=entry["visual_description"],
                voice_personality=entry.get("voice_personality"),
            )
            voice_id = elevenlabs_voices.pick_voice(entry.get("voice_personality"))
            if voice_id:
                db.update_character(row["id"], voice_id=voice_id)
                row["voice_id"] = voice_id
            inserted.append(row)
            logger.info(
                "character inserted project=%s ordinal=%d name=%s "
                "persona=%s visual=%s voice_id=%s",
                project_id,
                row["ordinal"],
                row["name"],
                _truncate_for_log(row["persona"]),
                _truncate_for_log(row["visual_description"]),
                voice_id,
            )

        cast_dir = PROJECTS_ROOT / project_id / "cast"
        cast_dir.mkdir(parents=True, exist_ok=True)

        # Project-level refs flow into every character's sheet generation,
        # locking the whole cast to the user's uploaded look. Per-character
        # ref attachments via /sprite_add_character refs= override per character.
        try:
            project_refs = json.loads(project.get("ref_image_paths") or "[]")
        except (TypeError, ValueError):
            project_refs = []
        if not isinstance(project_refs, list):
            project_refs = []

        gen_results: list[dict] = []
        errors: list[dict] = []

        async def _one(character: dict) -> tuple[dict, Optional[str], Optional[str]]:
            sheet_path, err = await self._generate_master_sheet(
                project_id=project_id,
                character=character,
                preset=preset,
                cast_dir=cast_dir,
                ref_image_paths=project_refs,
            )
            return character, sheet_path, err

        try:
            tasks = [asyncio.create_task(_one(ch)) for ch in inserted]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            await asyncio.shield(self._cleanup_after_cancel(project_id, cast_dir))
            raise

        for res in results:
            if isinstance(res, BaseException):
                err_msg = str(res)
                logger.warning("character task crashed: %s", err_msg)
                errors.append({"character_id": None, "error_msg": err_msg})
                continue
            character, sheet_path, err = res
            if sheet_path:
                gen_results.append({
                    "id": character["id"],
                    "ordinal": character["ordinal"],
                    "name": character["name"],
                    "role": character.get("role"),
                    "voice_id": character.get("voice_id"),
                    "sheet_path": sheet_path,
                })
            else:
                errors.append({
                    "character_id": character["id"],
                    "error_msg": err or "unknown error",
                })
                gen_results.append({
                    "id": character["id"],
                    "ordinal": character["ordinal"],
                    "name": character["name"],
                    "role": character.get("role"),
                    "voice_id": character.get("voice_id"),
                    "sheet_path": None,
                    "error_msg": err,
                })

        await asyncio.shield(self._finalize_cast_phase(project_id))

        return {
            "project_id": project_id,
            "phase": "cast",
            "characters": sorted(gen_results, key=lambda c: c["ordinal"]),
            "cast_dir": str(cast_dir),
            "errors": errors,
        }

    async def edit_character(
        self,
        *,
        character_id: str,
        user_text: str,
        ref_image_paths: Optional[list[str]] = None,
    ) -> dict:
        character = db.get_character(character_id)
        if character is None:
            raise CharacterNotFoundError(
                f"character not found: {character_id}",
            )
        project_id = character["project_id"]
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(
                f"parent project missing: {project_id}",
            )
        if project["phase"] != "cast":
            raise ProjectInWrongPhaseError(
                f"cannot edit: project is in phase {project['phase']!r}; "
                f"cast already approved or not yet generated. "
                f"approve or revert first.",
            )

        cleaned_text = _sanitize(user_text).strip()
        if not (1 <= len(cleaned_text) <= 500):
            raise ValueError(
                f"edit text must be 1..500 chars (got {len(cleaned_text)})",
            )

        try:
            preset = get_preset(project["style_preset_id"])
        except StylePresetLoadError:
            preset = get_preset(DEFAULT_PRESET_ID)

        decision = await self._decide_character_edit(
            project_id=project_id,
            character=character,
            user_text=cleaned_text,
        )

        decision_type = decision.get("type")
        if decision_type not in ("surgical", "regenerate"):
            decision_type = "regenerate"

        edit_prompt: Optional[str] = None
        if decision_type == "surgical":
            ep_raw = decision.get("edit_prompt")
            if isinstance(ep_raw, str) and ep_raw.strip():
                edit_prompt = ep_raw.strip()
            else:
                logger.info(
                    "edit fallback surgical→regenerate (missing edit_prompt) "
                    "char=%s",
                    character_id,
                )
                decision_type = "regenerate"

        cast_dir = PROJECTS_ROOT / project_id / "cast"
        char_dir = cast_dir / character_id
        char_dir.mkdir(parents=True, exist_ok=True)

        sheet_path_str = character.get("master_sheet_path")
        sheet_exists = bool(sheet_path_str) and Path(sheet_path_str).exists()
        if decision_type == "surgical" and not sheet_exists:
            logger.warning(
                "surgical edit requested but master_sheet missing on disk "
                "char=%s; falling back to regenerate",
                character_id,
            )
            decision_type = "regenerate"

        history_dir = char_dir / "history"
        history_ts = str(int(time.time()))

        # Newly-supplied refs are a re-anchor, not a tweak. Force the
        # regenerate path so the model rebuilds from the ref images
        # instead of editing the existing sheet in-place.
        if ref_image_paths:
            decision_type = "regenerate"
            db.update_character(
                character_id,
                reference_image_path=ref_image_paths[0],
                source="reference_image",
            )
            character["reference_image_path"] = ref_image_paths[0]
            character["source"] = "reference_image"

        if decision_type == "surgical":
            return await self._do_surgical_edit(
                project_id=project_id,
                character=character,
                user_text=cleaned_text,
                edit_prompt=edit_prompt or "",
                char_dir=char_dir,
                history_dir=history_dir,
                history_ts=history_ts,
                decision=decision,
            )
        return await self._do_regenerate_edit(
            project_id=project_id,
            character=character,
            user_text=cleaned_text,
            preset=preset,
            char_dir=char_dir,
            history_dir=history_dir,
            history_ts=history_ts,
            decision=decision,
            ref_image_paths=ref_image_paths,
        )

    async def add_character(
        self,
        *,
        project_id: str,
        description: str,
        ref_image_paths: Optional[list[str]] = None,
    ) -> dict:
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"unknown project: {project_id}")
        if project["phase"] != "cast":
            raise ProjectInWrongPhaseError(
                f"cannot add character: project is in phase "
                f"{project['phase']!r}; expected 'cast'",
            )

        cleaned = _sanitize(description).strip()
        if len(cleaned) < VISUAL_DESC_MIN:
            raise ValueError(
                f"character description too short "
                f"(need ≥{VISUAL_DESC_MIN} chars, got {len(cleaned)})",
            )
        if len(cleaned) > 1000:
            raise ValueError(
                f"character description too long ({len(cleaned)} chars; max 1000)",
            )

        existing = db.list_characters(project_id)
        if len(existing) >= MAX_CAST_SIZE:
            raise CastFullError(
                f"cast already has {len(existing)} characters "
                f"(max {MAX_CAST_SIZE}); "
                f"remove one with /sprite_remove_character first",
            )
        next_ordinal = max((c["ordinal"] for c in existing), default=0) + 1

        existing_names = {c["name"].lower() for c in existing}
        name = self._derive_character_name(cleaned, next_ordinal, existing_names)
        persona = self._derive_character_persona(cleaned)

        try:
            preset = get_preset(project["style_preset_id"])
        except StylePresetLoadError:
            preset = get_preset(DEFAULT_PRESET_ID)

        char_row = db.create_character(
            project_id=project_id,
            ordinal=next_ordinal,
            name=name,
            role="supporting",
            persona=persona,
            visual_description=cleaned,
        )
        voice_id = elevenlabs_voices.pick_voice(None)
        if voice_id:
            db.update_character(char_row["id"], voice_id=voice_id)
            char_row["voice_id"] = voice_id

        # Per-character refs override / merge with project refs. Persist the
        # primary ref on the character row so downstream regen + UI can show
        # the source. Schema only has one column, so first ref wins for the
        # display anchor; the full list still flows to image.edit.
        project_refs = []
        try:
            project_refs = json.loads(project.get("ref_image_paths") or "[]")
        except (TypeError, ValueError):
            project_refs = []

        char_refs = list(ref_image_paths or [])
        if char_refs:
            db.update_character(
                char_row["id"],
                reference_image_path=char_refs[0],
                source="reference_image",
            )
            char_row["reference_image_path"] = char_refs[0]
            char_row["source"] = "reference_image"

        merged_refs = char_refs or project_refs

        cast_dir = PROJECTS_ROOT / project_id / "cast"
        cast_dir.mkdir(parents=True, exist_ok=True)
        sheet_path, err = await self._generate_master_sheet(
            project_id=project_id,
            character=char_row,
            preset=preset,
            cast_dir=cast_dir,
            ref_image_paths=merged_refs,
        )

        logger.info(
            "character added project=%s ordinal=%d name=%s sheet=%s err=%s",
            project_id,
            char_row["ordinal"],
            char_row["name"],
            "ok" if sheet_path else "missing",
            err or "",
        )
        return {
            "character_id": char_row["id"],
            "ordinal": char_row["ordinal"],
            "name": char_row["name"],
            "role": char_row.get("role"),
            "persona": char_row["persona"],
            "visual_description": char_row["visual_description"],
            "master_sheet_path": sheet_path,
            "voice_id": char_row.get("voice_id"),
            "error": err,
            "total_count": len(existing) + 1,
        }

    async def remove_character(
        self,
        *,
        character_id: str,
    ) -> dict:
        character = db.get_character(character_id)
        if character is None:
            raise CharacterNotFoundError(
                f"character not found: {character_id}",
            )
        project_id = character["project_id"]
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(
                f"parent project missing: {project_id}",
            )
        if project["phase"] != "cast":
            raise ProjectInWrongPhaseError(
                f"cannot remove: project is in phase {project['phase']!r}; "
                f"expected 'cast'",
            )
        siblings = db.list_characters(project_id)
        if len(siblings) <= 1:
            raise CastTooSmallError(
                "cannot remove the only character; the cast must have ≥1",
            )

        cast_dir = PROJECTS_ROOT / project_id / "cast"
        char_dir = cast_dir / character_id
        trash_dest = (
            PROJECTS_ROOT / project_id / "_trash" / character_id
            / str(int(time.time()))
        )

        if char_dir.exists():
            try:
                trash_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(char_dir), str(trash_dest))
            except OSError as e:
                logger.warning(
                    "trash move failed char=%s err=%s; "
                    "continuing with DB delete",
                    character_id, e,
                )

        self._delete_and_repack_characters(
            project_id=project_id,
            character_id=character_id,
        )
        remaining = db.list_characters(project_id)
        logger.info(
            "character removed project=%s removed=%s remaining=%d",
            project_id, character_id, len(remaining),
        )
        return {
            "project_id": project_id,
            "removed_id": character_id,
            "remaining_count": len(remaining),
            "remaining": [
                {"id": c["id"], "ordinal": c["ordinal"], "name": c["name"]}
                for c in remaining
            ],
        }

    async def approve_cast(
        self,
        *,
        project_id: str,
    ) -> dict:
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"unknown project: {project_id}")

        if project["phase"] == "timeline":
            chars = db.list_characters(project_id)
            return {
                "project_id": project_id,
                "phase": "timeline",
                "character_count": len(chars),
                "already_approved": True,
            }
        if project["phase"] != "cast":
            raise ProjectInWrongPhaseError(
                f"cannot approve cast: project is in phase "
                f"{project['phase']!r}; expected 'cast'",
            )

        chars = db.list_characters(project_id)
        if not chars:
            raise OrchestratorError("cannot approve an empty cast")

        # Gate on disk before flipping to timeline. The DB-level
        # master_sheet_path-is-None check covers generation failures; the
        # on-disk audit covers the case where DB says ok but bytes have
        # vanished (silent provider response, external cleanup, etc.).
        # Without this, /sprite_approve_cast would advance to timeline
        # and the writer's reference_still loop would 404 on every shot.
        on_disk_missing = self._audit_cast_sheets_on_disk(project_id)
        if on_disk_missing:
            raise CastIncompleteError(project_id, on_disk_missing)

        ts = db.now_ts()
        with db.txn() as conn:
            for c in chars:
                conn.execute(
                    "UPDATE characters SET is_approved = 1, "
                    "updated_at = ? WHERE id = ?",
                    (ts, c["id"]),
                )
            conn.execute(
                "UPDATE projects SET phase = 'timeline', "
                "approved_cast_at = ?, updated_at = ? WHERE id = ?",
                (ts, ts, project_id),
            )

        logger.info(
            "cast approved project=%s characters=%d",
            project_id, len(chars),
        )
        return {
            "project_id": project_id,
            "phase": "timeline",
            "character_count": len(chars),
            "already_approved": False,
        }

    async def advance_to_timeline_phase(self, *, project_id: str) -> dict:
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"unknown project: {project_id}")
        if project["phase"] != "timeline":
            raise ProjectInWrongPhaseError(
                f"project {project_id} is in phase {project['phase']!r}, "
                f"expected 'timeline'",
            )

        chars = db.list_characters(project_id)
        if not chars:
            raise OrchestratorError(
                "cannot generate timeline: project has no characters",
            )
        unapproved = [c for c in chars if not c.get("is_approved")]
        if unapproved:
            names = ", ".join(c["name"] for c in unapproved)
            raise OrchestratorError(
                f"cannot generate timeline: cast not approved "
                f"(unapproved: {names}); run /sprite_approve_cast first",
            )

        try:
            preset = get_preset(project["style_preset_id"])
        except StylePresetLoadError:
            preset = get_preset(DEFAULT_PRESET_ID)

        duration = int(project["duration_seconds"])
        target_word_count = int(round(duration * 2.2))

        characters_payload = [self._character_for_writer(c) for c in chars]
        characters_json = json.dumps(characters_payload, indent=2)
        style_preset_full = json.dumps(
            {
                "id": preset.id,
                "name": preset.name,
                "descriptor": preset.descriptor,
                "render_notes": preset.render_notes,
                "motion_descriptor": preset.motion_descriptor,
            },
            indent=2,
        )

        prompt_body = load_prompt(
            "timeline_writer",
            brief=project["brief"],
            characters_json=characters_json,
            style_preset_full=style_preset_full,
            vibe=project.get("vibe") or "",
            duration_seconds=duration,
            target_word_count=target_word_count,
        )

        valid_char_ids = {c["id"] for c in chars}
        try:
            parsed = await self._call_timeline_writer(
                project_id=project_id,
                prompt_body=prompt_body,
                valid_char_ids=valid_char_ids,
                target_duration=duration,
            )
        except ProviderContentPolicyError as e:
            msg = (
                f"timeline_writer blocked by content policy: "
                f"{e.original_message or e}"
            )
            db.set_phase(project_id, "failed", error_message=msg)
            raise
        except TimelineGenerationFailedError as e:
            db.set_phase(project_id, "failed", error_message=str(e))
            raise
        except SpriteStudioError as e:
            db.set_phase(project_id, "failed", error_message=str(e))
            raise

        title = _sanitize(parsed.get("title") or "").strip()[:200] or "Untitled"
        use_narrator = bool(parsed.get("use_narrator", False))
        if use_narrator:
            narrator_script = _sanitize(parsed.get("narrator_script") or "").strip()
            self._check_narrator_word_count(narrator_script, target_word_count)
        else:
            narrator_script = None
        db.update_project(
            project_id,
            title=title,
            narrator_script=narrator_script,
            use_narrator=use_narrator,
        )
        logger.info(
            "timeline writer ok project=%s title=%r use_narrator=%s "
            "narrator_chars=%d narrator_head=%r",
            project_id, title, use_narrator,
            len(narrator_script) if narrator_script else 0,
            (narrator_script or "")[:100],
        )

        char_lookup = {c["id"]: c for c in chars}
        shot_rows = self._persist_shot_rows(
            project_id=project_id,
            shots_data=parsed["shots"],
        )

        ref_results, ref_errors = await self._generate_all_reference_stills(
            project_id=project_id,
            shot_rows=shot_rows,
            char_lookup=char_lookup,
            preset=preset,
        )

        total_duration = sum(s["duration_seconds"] for s in shot_rows)
        return {
            "project_id": project_id,
            "title": title,
            "shot_count": len(shot_rows),
            "total_duration": total_duration,
            "shots": ref_results,
            "errors": ref_errors,
        }

    async def _run_timeline_gen_safely(self, *, project_id: str) -> None:
        """Background-task entry point for advance_to_timeline_phase.

        advance_to_timeline_phase already catches its own typed errors and
        marks the project failed via db.set_phase. This wrapper exists to
        catch anything that escapes that handler (untyped exceptions, the
        ones added later by mistake, asyncio.CancelledError on shutdown)
        so the project never gets stuck at phase='timeline' with empty
        shots after a background failure.

        Per the asyncio docs, CancelledError must be re-raised to let
        cancellation propagate (https://docs.python.org/3/library/
        asyncio-exceptions.html#asyncio.CancelledError).
        """
        try:
            await self.advance_to_timeline_phase(project_id=project_id)
        except asyncio.CancelledError:
            # Cooperative cancellation (process shutdown or explicit
            # cancel). Mark failed, then re-raise so the loop sees the
            # cancellation as expected; never swallow it.
            try:
                db.set_phase(
                    project_id, "failed",
                    error_message="timeline generation cancelled",
                )
            finally:
                raise
        except SpriteStudioError as e:
            # Typed errors are normally caught inside advance_to_timeline_phase
            # itself; this branch is the safety net if a future code path
            # lets one escape.
            logger.exception("typed error in background timeline gen")
            db.set_phase(
                project_id, "failed",
                error_message=_sanitize_error(str(e)),
            )
        except Exception as e:
            logger.exception("untyped error in background timeline gen")
            db.set_phase(
                project_id, "failed",
                error_message=_sanitize_error(
                    f"unexpected: {type(e).__name__}: {e}",
                ),
            )

    async def edit_shot(
        self,
        *,
        shot_id: str,
        user_text: str,
    ) -> dict:
        shot = db.get_shot(shot_id)
        if shot is None:
            raise ShotNotFoundError(f"shot not found: {shot_id}")
        project_id = shot["project_id"]
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"parent project missing: {project_id}")
        if project["phase"] != "timeline":
            raise ProjectInWrongPhaseError(
                f"cannot edit shot: project is in phase "
                f"{project['phase']!r}; expected 'timeline'. "
                f"Timeline already approved; cancel render or wait for completion.",
            )

        cleaned_text = _sanitize(user_text).strip()
        if not (1 <= len(cleaned_text) <= 500):
            raise ValueError(
                f"edit text must be 1..500 chars (got {len(cleaned_text)})",
            )

        try:
            preset = get_preset(project["style_preset_id"])
        except StylePresetLoadError:
            preset = get_preset(DEFAULT_PRESET_ID)

        characters = db.list_characters(project_id)
        valid_char_ids = {c["id"] for c in characters}
        char_lookup = {c["id"]: c for c in characters}

        decision = await self._decide_shot_edit(
            project_id=project_id,
            shot=shot,
            user_text=cleaned_text,
        )

        if decision.get("regenerate_video"):
            logger.info(
                "shot_edit override regenerate_video→False shot=%s",
                shot_id,
            )
        regen_video = False

        updated_raw = decision.get("updated_shot")
        if not isinstance(updated_raw, dict):
            raise ValueError(
                "shot_edit decision missing 'updated_shot' object",
            )

        applied_updates, current_present = self._apply_shot_decision(
            shot=shot,
            updated_raw=updated_raw,
            valid_char_ids=valid_char_ids,
            char_lookup=char_lookup,
        )

        fields_changed_raw = decision.get("fields_changed") or []
        fields_changed = [
            f for f in fields_changed_raw if isinstance(f, str)
        ]

        if applied_updates:
            db.update_shot(shot_id, **applied_updates)

        # After persisting shot fields, decide whether to regenerate the
        # reference still. The LLM's `regenerate_reference_still` flag is
        # the source of truth.
        regen_still = bool(decision.get("regenerate_reference_still"))
        new_ref_path: Optional[str] = shot.get("reference_still_path")
        regen_error: Optional[str] = None
        regenerated = False

        if regen_still:
            fresh_shot = db.get_shot(shot_id) or shot
            chars_in_shot = [
                char_lookup[cid]
                for cid in current_present
                if cid in char_lookup
            ]
            try:
                new_ref_path_obj = await self._regenerate_reference_still(
                    project_id=project_id,
                    shot=fresh_shot,
                    chars_in_shot=chars_in_shot,
                    preset=preset,
                )
                new_ref_path = str(new_ref_path_obj)
                db.update_shot(shot_id, reference_still_path=new_ref_path)
                regenerated = True
            except ProviderContentPolicyError as e:
                regen_error = (
                    f"content_policy: {e.original_message or e}"
                )
                logger.warning(
                    "shot_edit ref-still blocked shot=%s: %s",
                    shot_id, regen_error,
                )
            except SpriteStudioError as e:
                regen_error = f"image_edit_failed: {e}"
                logger.warning(
                    "shot_edit ref-still failed shot=%s: %s",
                    shot_id, regen_error,
                )
            except OrchestratorError as e:
                regen_error = str(e)
                logger.warning(
                    "shot_edit ref-still skipped shot=%s: %s",
                    shot_id, regen_error,
                )

        logger.info(
            "shot edit ok project=%s shot=%s ord=%d fields=%s "
            "regen_still=%s regenerated=%s regen_video=%s",
            project_id, shot_id, shot["ordinal"], fields_changed,
            regen_still, regenerated, regen_video,
        )
        return {
            "shot_id": shot_id,
            "ordinal": shot["ordinal"],
            "fields_changed": fields_changed,
            "reference_still_path": new_ref_path,
            "regenerated": regenerated,
            "regen_error": regen_error,
        }

    async def regenerate_shot_reference(self, shot_id: str) -> Path:
        """Public wrapper around _regenerate_reference_still: looks up the
        shot, project, characters and style preset, regenerates the still
        and persists the new path. Raises ShotNotFoundError /
        ProjectInWrongPhaseError / OrchestratorError on hard failures;
        ProviderContentPolicyError / SpriteStudioError bubble up
        unchanged so callers can surface a precise error class.
        """
        shot = db.get_shot(shot_id)
        if shot is None:
            raise ShotNotFoundError(f"shot not found: {shot_id}")
        project_id = shot["project_id"]
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"parent project missing: {project_id}")
        if project["phase"] != "timeline":
            raise ProjectInWrongPhaseError(
                f"cannot regenerate reference: project is in phase "
                f"{project['phase']!r}; expected 'timeline'.",
            )

        try:
            preset = get_preset(project["style_preset_id"])
        except StylePresetLoadError:
            preset = get_preset(DEFAULT_PRESET_ID)

        characters = db.list_characters(project_id)
        char_lookup = {c["id"]: c for c in characters}
        present_ids = shot.get("characters_present") or []
        chars_in_shot = [
            char_lookup[cid] for cid in present_ids if cid in char_lookup
        ]

        new_path = await self._regenerate_reference_still(
            project_id=project_id,
            shot=shot,
            chars_in_shot=chars_in_shot,
            preset=preset,
        )
        db.update_shot(shot_id, reference_still_path=str(new_path))
        logger.info(
            "ref-still regen ok shot=%s ord=%d path=%s",
            shot_id, shot["ordinal"], new_path,
        )
        return new_path

    async def add_shot(
        self,
        *,
        project_id: str,
        ordinal: int,
        action: str,
        duration_seconds: int = 8,
        setting: str = "",
        characters_present: Optional[list[str]] = None,
        camera: Optional[str] = None,
        emotion: Optional[str] = None,
        narration_line: Optional[str] = None,
        generate_reference: bool = True,
    ) -> dict:
        """Insert a new shot at `ordinal`, shifting later shots up by 1.

        Phase-locked to 'timeline'; rejects with ProjectInWrongPhaseError
        otherwise. Caps at TIMELINE_MAX_SHOTS. Validates camera against
        ALLOWED_CAMERAS and characters_present against the project's cast.

        If `generate_reference=True` and the shot has at least one character,
        attempts to generate a reference still after the DB row commits. The
        still gen happens OUTSIDE the txn (it's a multi-second LLM call;
        running inside a SQLite txn would block writers). On failure the row
        survives with reference_still_path=NULL and the user can fix it via
        /sprite_edit_shot_field setting=...
        """
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"unknown project: {project_id}")
        if project["phase"] != "timeline":
            raise ProjectInWrongPhaseError(
                f"cannot add_shot: project is in phase "
                f"{project['phase']!r}; expected 'timeline'.",
            )

        existing_shots = db.list_shots(project_id)
        if len(existing_shots) >= TIMELINE_MAX_SHOTS:
            raise TimelineFullError(
                f"timeline already has {TIMELINE_MAX_SHOTS} shots "
                f"(the cap); /sprite_delete_shot one first."
            )

        if camera is not None and camera not in ALLOWED_CAMERAS:
            raise ValueError(
                f"camera must be one of {sorted(ALLOWED_CAMERAS)}, "
                f"got {camera!r}"
            )

        cleaned_action = _sanitize(action or "").strip()
        if not cleaned_action:
            raise ValueError("action cannot be empty")

        chars = list(characters_present or [])
        if chars:
            valid_ids = {c["id"] for c in db.list_characters(project_id)}
            unknown = [cid for cid in chars if cid not in valid_ids]
            if unknown:
                raise ValueError(
                    f"unknown character_ids for this project: {unknown}"
                )

        try:
            new_shot = db.create_shot_at_ordinal(
                project_id=project_id,
                ordinal=int(ordinal),
                duration_seconds=int(duration_seconds),
                setting=setting,
                action=cleaned_action,
                characters_present=chars,
                camera=camera,
                emotion=emotion,
                narration_line=narration_line,
                allowed_phases={"timeline"},
            )
        except db.WrongPhaseError as e:
            # Race: phase changed between get_project and the txn.
            raise ProjectInWrongPhaseError(str(e)) from e
        except db.ProjectNotFoundError as e:
            raise OrchestratorError(str(e)) from e

        reference_generated = False
        regen_error: Optional[str] = None
        if generate_reference and chars:
            try:
                preset = get_preset(project["style_preset_id"])
            except StylePresetLoadError:
                preset = get_preset(DEFAULT_PRESET_ID)
            char_lookup = {c["id"]: c for c in db.list_characters(project_id)}
            chars_in_shot = [
                char_lookup[cid] for cid in chars if cid in char_lookup
            ]
            try:
                ref_path = await self._generate_reference_still(
                    project_id=project_id,
                    shot=new_shot,
                    chars_in_shot=chars_in_shot,
                    preset=preset,
                )
                db.update_shot(new_shot["id"], reference_still_path=str(ref_path))
                reference_generated = True
            except ProviderContentPolicyError as e:
                regen_error = f"content_policy: {e.original_message or e}"
                logger.warning(
                    "add_shot reference still blocked shot=%s ord=%d: %s",
                    new_shot["id"], new_shot["ordinal"], regen_error,
                )
            except (OrchestratorError, SpriteStudioError) as e:
                regen_error = str(e)
                logger.warning(
                    "add_shot reference still failed shot=%s ord=%d: %s",
                    new_shot["id"], new_shot["ordinal"], regen_error,
                )

        fresh = db.get_shot(new_shot["id"]) or new_shot
        logger.info(
            "shot inserted project=%s shot=%s ord=%d reference=%s",
            project_id, new_shot["id"], new_shot["ordinal"],
            "ok" if reference_generated else "pending",
        )
        return {
            "shot_id": new_shot["id"],
            "project_id": project_id,
            "ordinal": new_shot["ordinal"],
            "reference_generated": reference_generated,
            "reference_still_path": fresh.get("reference_still_path"),
            "regen_error": regen_error,
            "shot": fresh,
        }

    async def remove_shot(self, *, shot_id: str) -> dict:
        """Delete a shot and pack the remaining ordinals down.

        Phase-locked to 'timeline'. Refuses to delete the project's only
        shot. Moves the shot's filesystem dir to
        projects/<project_id>/_trash/shots/<shot_id>/<timestamp>/ before
        deleting the DB row, so artifacts are recoverable; if the trash
        move fails (disk full, perm denied), aborts the delete entirely.
        """
        shot = db.get_shot(shot_id)
        if shot is None:
            raise ShotNotFoundError(f"shot not found: {shot_id}")
        project_id = shot["project_id"]
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"parent project missing: {project_id}")
        if project["phase"] != "timeline":
            raise ProjectInWrongPhaseError(
                f"cannot remove_shot: project is in phase "
                f"{project['phase']!r}; expected 'timeline'.",
            )

        siblings = db.list_shots(project_id)
        if len(siblings) <= 1:
            raise TimelineLastShotError(
                "cannot remove the only shot; the timeline must have >= 1"
            )

        shot_dir = PROJECTS_ROOT / project_id / "shots" / shot_id
        trash_dest = (
            PROJECTS_ROOT / project_id / "_trash" / "shots" / shot_id
            / str(int(time.time()))
        )
        if shot_dir.exists():
            try:
                trash_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(shot_dir), str(trash_dest))
            except OSError as e:
                # Disk full / permission denied: abort the delete so the DB
                # never references a missing artifact directory. The trash
                # move was non-destructive (shot_dir still exists on the
                # original path).
                logger.error(
                    "remove_shot trash move failed shot=%s err=%s",
                    shot_id, e,
                )
                raise OrchestratorError(
                    f"trash move failed for shot {shot_id}: {e}; "
                    f"aborting delete"
                ) from e
        else:
            logger.info(
                "remove_shot: no on-disk dir to trash shot=%s path=%s",
                shot_id, shot_dir,
            )

        try:
            result = db.delete_shot(shot_id, allowed_phases={"timeline"})
        except db.WrongPhaseError as e:
            # Race: phase flipped after our orchestrator check. The shot
            # dir is now in _trash/ and the DB row still exists; that's
            # acceptable (recoverable, no data loss).
            raise ProjectInWrongPhaseError(str(e)) from e
        except db.LastShotError as e:
            raise TimelineLastShotError(str(e)) from e
        except db.ShotNotFoundError as e:
            raise ShotNotFoundError(str(e)) from e

        remaining = db.list_shots(project_id)
        logger.info(
            "shot removed project=%s removed=%s ord_was=%d remaining=%d",
            project_id, shot_id, result["ordinal_was"], len(remaining),
        )
        return {
            "deleted_shot_id": shot_id,
            "project_id": project_id,
            "ordinal_was": result["ordinal_was"],
            "shots_remaining": len(remaining),
        }

    async def approve_timeline(
        self,
        *,
        project_id: str,
    ) -> dict:
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"unknown project: {project_id}")
        if project["phase"] != "timeline":
            raise ProjectInWrongPhaseError(
                f"cannot approve timeline: project is in phase "
                f"{project['phase']!r}; expected 'timeline'.",
            )

        shots = db.list_shots(project_id)
        if not shots:
            raise TimelineNotReadyError(
                "cannot approve: project has no shots. "
                "Run /sprite_timeline first.",
            )
        missing = [s for s in shots if not s.get("reference_still_path")]
        if missing:
            ords = ", ".join(str(s["ordinal"]) for s in missing)
            raise TimelineNotReadyError(
                f"cannot approve: shots missing reference stills: {ords}. "
                f"fix individually with /sprite_edit_shot before approving.",
            )

        ts = db.now_ts()
        with db.txn() as conn:
            conn.execute(
                "UPDATE projects SET phase = 'render', "
                "approved_timeline_at = ?, updated_at = ? WHERE id = ?",
                (ts, ts, project_id),
            )

        total_duration = sum(s["duration_seconds"] for s in shots)
        fresh = db.get_project(project_id) or project
        cost_so_far = float(fresh.get("total_cost_usd") or 0.0)
        logger.info(
            "timeline approved project=%s shots=%d total_dur=%ds cost=$%.4f",
            project_id, len(shots), total_duration, cost_so_far,
        )
        return {
            "project_id": project_id,
            "phase": "render",
            "shot_count": len(shots),
            "total_duration": total_duration,
            "total_cost_usd_so_far": cost_so_far,
        }

    # ---------------------- helpers ----------------------

    def _styles_for_prompt(self) -> str:
        presets = load_presets()
        preview: list[dict[str, str]] = []
        for pid in sorted(presets.keys()):
            p = presets[pid]
            preview.append({
                "id": p.id,
                "name": p.name,
                "descriptor": p.descriptor[:80],
            })
        return json.dumps(preview, indent=2)

    def _apply_auto_decisions(
        self,
        project_id: str,
        auto: Any,
    ) -> dict:
        if not isinstance(auto, dict):
            return {}
        applied: dict[str, Any] = {}
        update_fields: dict[str, Any] = {}

        candidate_style = auto.get("style_preset_id")
        if isinstance(candidate_style, str) and is_valid_preset_id(candidate_style):
            update_fields["style_preset_id"] = candidate_style
            applied["style_preset_id"] = candidate_style

        candidate_dur = auto.get("duration_seconds")
        if isinstance(candidate_dur, int) and candidate_dur in VALID_DURATIONS:
            update_fields["duration_seconds"] = candidate_dur
            applied["duration_seconds"] = candidate_dur

        candidate_vibe = auto.get("vibe")
        if isinstance(candidate_vibe, str) and candidate_vibe.strip():
            cleaned_vibe = _sanitize(candidate_vibe).strip()[:120]
            update_fields["vibe"] = cleaned_vibe
            applied["vibe"] = cleaned_vibe

        if update_fields:
            db.update_project(project_id, **update_fields)
        return applied

    async def _chat_json_with_retry(
        self,
        *,
        project_id: str,
        user_prompt: str,
        base_system: str,
        read_timeout_seconds: float | None = None,
    ) -> dict:
        """Call chat_json once with the base system prompt; if the result
        is a shape error, retry once with the strict JSON-only system.
        Forwards read_timeout_seconds so callers with known-slow prompts
        (cast designer, timeline) can override the tokenrouter default.
        """
        try:
            return await self._chat.chat_json(
                model=KIMI_MODEL,
                messages=[
                    {"role": "system", "content": base_system},
                    {"role": "user", "content": user_prompt},
                ],
                project_id=project_id,
                read_timeout_seconds=read_timeout_seconds,
            )
        except ProviderResponseShapeError as e:
            logger.warning(
                "chat_json shape error project=%s; retrying with strict system: %s",
                project_id, e,
            )
        return await self._chat.chat_json(
            model=KIMI_MODEL,
            messages=[
                {"role": "system", "content": _JSON_STRICT_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            project_id=project_id,
            read_timeout_seconds=read_timeout_seconds,
        )

    def _shape_check_cast(self, parsed: Any) -> list[dict]:
        """Validate cast shape (count, ordinals, name uniqueness, persona
        length). visual_description length is checked LATER in
        _final_check_cast so we can retry-expand short visuals first.
        """
        if not isinstance(parsed, dict):
            raise ValueError("cast_designer response was not a JSON object")
        chars = parsed.get("characters")
        if not isinstance(chars, list) or not chars:
            raise ValueError("cast_designer response missing non-empty 'characters' list")
        if not (1 <= len(chars) <= MAX_CAST_SIZE):
            raise ValueError(
                f"cast must have 1..{MAX_CAST_SIZE} characters (got {len(chars)})",
            )

        out: list[dict] = []
        seen_names: set[str] = set()
        seen_ordinals: set[int] = set()
        for raw in chars:
            if not isinstance(raw, dict):
                raise ValueError("each character entry must be a JSON object")
            name = (raw.get("name") or "").strip()
            persona = _sanitize((raw.get("persona") or "")).strip()
            visual = _sanitize((raw.get("visual_description") or "")).strip()
            role = (raw.get("role") or "").strip() or None
            voice_personality = (raw.get("voice_personality") or "").strip() or None
            ordinal_val = raw.get("ordinal")

            if not name:
                raise ValueError("character missing 'name'")
            if name.lower() in seen_names:
                raise ValueError(f"character names must be unique: {name!r} repeated")
            seen_names.add(name.lower())

            if not isinstance(ordinal_val, int) or ordinal_val < 1:
                raise ValueError(
                    f"character {name!r} has invalid ordinal: {ordinal_val!r}",
                )
            if ordinal_val in seen_ordinals:
                raise ValueError(f"duplicate ordinal {ordinal_val} in cast")
            seen_ordinals.add(ordinal_val)

            if len(persona) < 10:
                raise ValueError(
                    f"character {name!r} persona is too short "
                    f"(need ≥10 chars, got {len(persona)})",
                )

            out.append({
                "name": name[:80],
                "ordinal": ordinal_val,
                "role": role,
                "persona": persona,
                "visual_description": visual,
                "voice_personality": voice_personality,
            })

        out.sort(key=lambda c: c["ordinal"])
        for expected, entry in enumerate(out, start=1):
            if entry["ordinal"] != expected:
                raise ValueError(
                    f"ordinals must be sequential 1..N; got {[c['ordinal'] for c in out]}",
                )
        return out

    def _final_check_cast(self, characters: list[dict]) -> None:
        for entry in characters:
            if len(entry["visual_description"]) < VISUAL_DESC_MIN:
                raise ValueError(
                    f"character {entry['name']!r} visual_description is too short "
                    f"after retry (need ≥{VISUAL_DESC_MIN} chars, "
                    f"got {len(entry['visual_description'])})",
                )

    async def _expand_short_visuals(
        self,
        project_id: str,
        characters: list[dict],
    ) -> None:
        """For any character whose visual_description is shorter than the
        minimum, run one focused follow-up call asking for a longer
        description. Mutates `characters` in place. Best-effort: failures
        leave the original short text in place to be caught by the final
        check.
        """
        for entry in characters:
            if len(entry["visual_description"]) >= VISUAL_DESC_MIN:
                continue
            user_msg = (
                f"Character: {entry['name']}. Role: {entry.get('role') or 'unspecified'}. "
                f"Persona: {entry['persona']}. "
                f"Existing short visual description: {entry['visual_description']!r}. "
                f"Rewrite the visual_description so it is at least "
                f"{VISUAL_DESC_MIN} characters and includes species or age, "
                f"build, primary colors, and signature outfit. Keep present tense. "
                f"Reply with raw JSON only: "
                f'{{"visual_description": "<expanded text>"}}'
            )
            try:
                expanded = await self._chat.chat_json(
                    model=KIMI_MODEL,
                    messages=[
                        {"role": "system", "content": _JSON_STRICT_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    project_id=project_id,
                )
            except SpriteStudioError as e:
                logger.warning(
                    "visual expansion failed character=%s err=%s",
                    entry["name"], e,
                )
                continue
            new_text = expanded.get("visual_description") if isinstance(expanded, dict) else None
            if isinstance(new_text, str):
                cleaned = _sanitize(new_text).strip()
                if len(cleaned) >= VISUAL_DESC_MIN:
                    entry["visual_description"] = cleaned

    async def _generate_master_sheet(
        self,
        *,
        project_id: str,
        character: dict,
        preset: StylePreset,
        cast_dir: Path,
        ref_image_paths: Optional[list[str]] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        char_id = character["id"]
        char_dir = cast_dir / char_id
        char_dir.mkdir(parents=True, exist_ok=True)
        target_path = char_dir / "sheet.png"

        prompt = self._build_sheet_prompt(character, preset)
        ref_disk = _resolve_ref_paths(project_id, ref_image_paths or [])

        try:
            if ref_disk:
                # When refs are attached, route through image.edit so the
                # uploaded photos lock the character's appearance via
                # gpt-image-2's multi-reference input. .edit returns one
                # Path; wrap into a list to share the rename path below.
                edited = await self._image.edit(
                    prompt=prompt,
                    images=ref_disk,
                    size=SIZE_SQUARE,
                    quality=QUALITY_HIGH,
                    save_to=char_dir,
                    project_id=project_id,
                )
                generated = [edited]
            else:
                generated = await self._image.generate(
                    prompt=prompt,
                    size=SIZE_SQUARE,
                    quality=QUALITY_HIGH,
                    n=1,
                    save_to=char_dir,
                    project_id=project_id,
                )
        except ProviderContentPolicyError as e:
            err_msg = (
                f"moderation_block: {e.original_message or str(e)}"
            )
            logger.warning(
                "image moderation blocked character=%s name=%s reason=%s",
                char_id, character["name"], err_msg,
            )
            db.update_character(
                char_id,
                master_sheet_path=None,
                edit_history=[err_msg],
            )
            return None, err_msg
        except SpriteStudioError as e:
            err_msg = f"image_gen_failed: {e}"
            logger.warning(
                "sheet generation failed character=%s name=%s err=%s",
                char_id, character["name"], err_msg,
            )
            db.update_character(
                char_id,
                master_sheet_path=None,
                edit_history=[err_msg],
            )
            return None, err_msg
        except Exception as e:
            err_msg = f"unexpected_error: {e}"
            logger.exception(
                "sheet generation crashed character=%s name=%s",
                char_id, character["name"],
            )
            db.update_character(
                char_id,
                master_sheet_path=None,
                edit_history=[err_msg],
            )
            return None, err_msg

        if not generated:
            err_msg = "image_gen_returned_zero_paths"
            db.update_character(char_id, master_sheet_path=None, edit_history=[err_msg])
            return None, err_msg

        produced = generated[0]
        try:
            if target_path.exists():
                target_path.unlink()
            produced.replace(target_path)
        except OSError as e:
            err_msg = f"rename_failed: {e}"
            logger.warning(
                "could not rename sheet output character=%s err=%s",
                char_id, err_msg,
            )
            db.update_character(char_id, master_sheet_path=None, edit_history=[err_msg])
            return None, err_msg

        # Verify the file actually landed before persisting the path. Without
        # this, a silent provider failure or a race against an external
        # cleanup leaves the DB pointing at a missing file, and every
        # downstream reference still / approve-cast call has to rediscover
        # that fact via 404.
        if not target_path.exists() or target_path.stat().st_size == 0:
            err_msg = (
                f"post_write_verify_failed: target missing or empty at "
                f"{target_path}"
            )
            logger.warning(
                "sheet post-write verify failed character=%s path=%s",
                char_id, target_path,
            )
            db.update_character(char_id, master_sheet_path=None, edit_history=[err_msg])
            return None, err_msg

        db.update_character(char_id, master_sheet_path=str(target_path))
        return str(target_path), None

    def _build_sheet_prompt(
        self,
        character: dict,
        style_preset: StylePreset,
    ) -> str:
        # The .md template uses {style_preset.descriptor} and
        # {style_preset.render_notes}; passing the pydantic StylePreset
        # as a single kwarg lets string.Formatter resolve the dotted
        # access via attribute lookup.
        return load_prompt(
            "master_sheet",
            visual_description=character["visual_description"],
            style_preset=style_preset,
        )

    async def _finalize_cast_phase(self, project_id: str) -> None:
        # Set phase=cast first so cast-phase recovery commands
        # (/sprite_edit_character, /sprite_repair_cast) work even when the
        # audit below trips. Without that ordering, an audit failure would
        # strand the user with character rows inserted but phase=brief, and
        # /sprite_cast would re-run the cast designer and duplicate them.
        try:
            db.set_phase(project_id, "cast")
        except Exception:
            logger.exception("could not set project phase=cast project=%s", project_id)

        missing = self._audit_cast_sheets_on_disk(project_id)
        if missing:
            err_summary = (
                f"cast_incomplete: {len(missing)} character sheet(s) missing on "
                f"disk: " + "; ".join(f"{n}: {r}" for n, r in missing)
            )
            try:
                db.set_phase(project_id, "cast", error_message=err_summary)
            except Exception:
                logger.exception(
                    "could not record cast_incomplete error project=%s",
                    project_id,
                )
            logger.warning("cast advance gate blocked project=%s %s",
                           project_id, err_summary)
            raise CastIncompleteError(project_id, missing)

    async def regenerate_character_sheet(
        self,
        character: dict,
    ) -> Path:
        """Re-run master-sheet generation for a single character using its
        existing visual_description + project preset + project refs. Used
        by /sprite_repair_cast to fix sheets whose bytes vanished after
        the original cast advance succeeded.

        Raises OrchestratorError on resolution failure (project gone, refs
        invalid). Provider/SpriteStudio errors propagate so the caller can
        record per-character failure reasons.
        """
        char_id = character["id"]
        project_id = character["project_id"]
        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(
                f"parent project missing: {project_id}",
            )

        try:
            preset = get_preset(project["style_preset_id"])
        except StylePresetLoadError:
            preset = get_preset(DEFAULT_PRESET_ID)

        try:
            project_refs = json.loads(project.get("ref_image_paths") or "[]")
        except (TypeError, ValueError):
            project_refs = []
        if not isinstance(project_refs, list):
            project_refs = []

        cast_dir = PROJECTS_ROOT / project_id / "cast"
        cast_dir.mkdir(parents=True, exist_ok=True)

        sheet_path, err = await self._generate_master_sheet(
            project_id=project_id,
            character=character,
            preset=preset,
            cast_dir=cast_dir,
            ref_image_paths=project_refs,
        )
        if not sheet_path:
            raise OrchestratorError(
                f"sheet regeneration failed for {character['name']!r}: "
                f"{err or 'unknown error'}",
            )
        return Path(sheet_path)

    async def repair_cast(self, *, project_id: str) -> dict:
        """Regenerate any character sheets whose bytes are missing on disk.

        Idempotent: characters whose sheet exists at the persisted path are
        skipped. After regeneration, if the project is in 'timeline' phase
        and at least one character was repaired, also re-run the reference
        still pass for shots that include any repaired character so the
        downstream renderer is unblocked.
        """
        if not db._is_valid_ulid(project_id):
            raise ValueError(f"invalid project_id: {project_id!r}")

        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"unknown project: {project_id}")

        characters = db.list_characters(project_id)
        if not characters:
            raise OrchestratorError(
                f"project {project_id} has no characters to repair",
            )

        repaired: list[dict] = []
        skipped: list[dict] = []
        repaired_ids: set[str] = set()
        for c in characters:
            path_str = c.get("master_sheet_path")
            p = Path(path_str) if path_str else None
            if p is not None and p.exists() and p.stat().st_size > 0:
                skipped.append({
                    "id": c["id"],
                    "name": c["name"],
                    "reason": "sheet ok on disk",
                })
                continue
            try:
                new_path = await self.regenerate_character_sheet(c)
                repaired.append({
                    "id": c["id"],
                    "name": c["name"],
                    "path": str(new_path),
                })
                repaired_ids.add(c["id"])
            except (SpriteStudioError, OrchestratorError, ValueError) as e:
                skipped.append({
                    "id": c["id"],
                    "name": c["name"],
                    "reason": f"regeneration failed: {e}",
                })

        # If the broken project already advanced to timeline, the reference
        # stills point at sheet bytes we just rewrote; the writer's cached
        # path is stale (or was never produced). Regenerate stills for
        # shots that include any repaired character.
        regenerated_stills: list[dict] = []
        still_errors: list[dict] = []
        if project["phase"] == "timeline" and repaired_ids:
            try:
                preset = get_preset(project["style_preset_id"])
            except StylePresetLoadError:
                preset = get_preset(DEFAULT_PRESET_ID)
            char_lookup = {c["id"]: c for c in db.list_characters(project_id)}
            shots = db.list_shots(project_id)
            affected = [
                s for s in shots
                if any(cid in repaired_ids for cid in s.get("characters_present", []))
            ]
            for shot in affected:
                chars_in_shot = [
                    char_lookup[cid]
                    for cid in shot["characters_present"]
                    if cid in char_lookup
                ]
                try:
                    ref_path = await self._generate_reference_still(
                        project_id=project_id,
                        shot=shot,
                        chars_in_shot=chars_in_shot,
                        preset=preset,
                    )
                    db.update_shot(shot["id"], reference_still_path=str(ref_path))
                    regenerated_stills.append({
                        "shot_id": shot["id"],
                        "ordinal": shot["ordinal"],
                        "path": str(ref_path),
                    })
                except (SpriteStudioError, OrchestratorError) as e:
                    still_errors.append({
                        "shot_id": shot["id"],
                        "ordinal": shot["ordinal"],
                        "error_msg": str(e),
                    })

        # Clear the project-level error_message if the audit is now clean.
        # Otherwise /sprite_show keeps surfacing the stale "cast_incomplete"
        # banner even after a successful repair.
        post_audit = self._audit_cast_sheets_on_disk(project_id)
        if not post_audit:
            try:
                with db.txn() as conn:
                    conn.execute(
                        "UPDATE projects SET error_message = NULL, "
                        "updated_at = ? WHERE id = ?",
                        (db.now_ts(), project_id),
                    )
            except Exception:
                logger.exception(
                    "repair_cast: could not clear error_message project=%s",
                    project_id,
                )

        logger.info(
            "repair_cast project=%s repaired=%d skipped=%d stills=%d",
            project_id, len(repaired), len(skipped), len(regenerated_stills),
        )
        return {
            "project_id": project_id,
            "phase": project["phase"],
            "repaired": repaired,
            "skipped": skipped,
            "regenerated_stills": regenerated_stills,
            "still_errors": still_errors,
            "audit_clean": not post_audit,
        }

    def _audit_cast_sheets_on_disk(
        self,
        project_id: str,
    ) -> list[tuple[str, str]]:
        """Return (name, reason) for every character whose sheet is not
        usable on disk. A character with master_sheet_path=None means the
        underlying generation never produced a file. A character with a
        path set but no file (or zero-byte file) means the bytes vanished
        between generate-time and now (provider returned empty / external
        cleanup / disk full at write).
        """
        characters = db.list_characters(project_id)
        missing: list[tuple[str, str]] = []
        for c in characters:
            path_str = c.get("master_sheet_path")
            if not path_str:
                missing.append((c["name"], "no path persisted"))
                continue
            p = Path(path_str)
            if not p.exists():
                missing.append((c["name"], f"path missing: {p}"))
            elif p.stat().st_size == 0:
                missing.append((c["name"], f"zero-byte file: {p}"))
        return missing

    async def _cleanup_after_cancel(
        self,
        project_id: str,
        cast_dir: Path,
    ) -> None:
        """Mark all currently running jobs for this project as cancelled
        and remove any partial sheet files. Called from the
        `except asyncio.CancelledError` path; wrap in asyncio.shield at
        the call site so the cleanup itself is not cancelled.
        """
        logger.warning(
            "cleanup-after-cancel project=%s; marking running jobs cancelled",
            project_id,
        )
        try:
            conn = db.connect()
            try:
                rows = conn.execute(
                    "SELECT id FROM generation_jobs WHERE project_id = ? "
                    "AND status = 'running'",
                    (project_id,),
                ).fetchall()
            finally:
                conn.close()
            for row in rows:
                try:
                    db.mark_job_cancelled(row["id"])
                except Exception:
                    logger.debug("could not mark job %s cancelled", row["id"])
        except Exception:
            logger.exception("cleanup-after-cancel: db sweep failed")

        try:
            characters = db.list_characters(project_id)
            for ch in characters:
                if ch.get("master_sheet_path"):
                    continue
                char_dir = cast_dir / ch["id"]
                if not char_dir.exists():
                    continue
                for png in char_dir.glob("*.png"):
                    try:
                        png.unlink()
                    except OSError:
                        pass
        except Exception:
            logger.exception("cleanup-after-cancel: png sweep failed")

    # ---- character edit / add / remove helpers ----

    async def _decide_character_edit(
        self,
        *,
        project_id: str,
        character: dict,
        user_text: str,
    ) -> dict:
        char_payload = {
            "id": character["id"],
            "ordinal": character["ordinal"],
            "name": character["name"],
            "role": character.get("role"),
            "persona": character["persona"],
            "visual_description": character["visual_description"],
            "voice_personality": character.get("voice_personality"),
        }
        prompt_body = load_prompt(
            "character_edit",
            character_json=json.dumps(char_payload, indent=2),
            user_text=user_text,
        )
        parsed = await self._chat_json_with_retry(
            project_id=project_id,
            user_prompt=prompt_body,
            base_system="You are the Sprite Studio Character Edit Translator.",
        )
        if not isinstance(parsed, dict):
            raise ValueError("character_edit response was not a JSON object")
        return parsed

    async def _do_surgical_edit(
        self,
        *,
        project_id: str,
        character: dict,
        user_text: str,
        edit_prompt: str,
        char_dir: Path,
        history_dir: Path,
        history_ts: str,
        decision: dict,
    ) -> dict:
        target_path = char_dir / "sheet.png"
        history_path = history_dir / f"{history_ts}.png"

        self._snapshot_history(target_path, history_dir, history_path)

        try:
            edited_path = await self._image.edit(
                prompt=edit_prompt,
                images=[target_path],
                size=SIZE_SQUARE,
                quality=QUALITY_HIGH,
                save_to=char_dir,
                project_id=project_id,
            )
        except ProviderContentPolicyError as e:
            msg = e.original_message or str(e)
            logger.warning(
                "surgical edit blocked by content policy char=%s",
                character["id"],
            )
            raise OrchestratorError(
                f"edit blocked by content policy: {msg}",
            ) from e

        try:
            self._install_new_sheet(edited_path, target_path, history_path)
        except OSError as e:
            raise OrchestratorError(
                f"failed to install edited sheet: {e}",
            ) from e

        entry = {
            "timestamp": int(time.time()),
            "user_text": user_text,
            "type": "surgical",
            "edit_prompt": edit_prompt,
            "rationale": decision.get("rationale"),
            "changed_fields": decision.get("changed_fields") or [],
        }
        self._append_edit_history(character["id"], entry)
        db.update_character(
            character["id"], master_sheet_path=str(target_path),
        )
        edit_count = self._count_edits(character["id"])
        logger.info(
            "character edit ok type=surgical char=%s edits=%d",
            character["id"], edit_count,
        )
        return {
            "character_id": character["id"],
            "type": "surgical",
            "master_sheet_path": str(target_path),
            "edit_count": edit_count,
        }

    async def _do_regenerate_edit(
        self,
        *,
        project_id: str,
        character: dict,
        user_text: str,
        preset: StylePreset,
        char_dir: Path,
        history_dir: Path,
        history_ts: str,
        decision: dict,
        ref_image_paths: Optional[list[str]] = None,
    ) -> dict:
        new_visual_raw = decision.get("updated_visual_description")
        if isinstance(new_visual_raw, str):
            new_visual = _sanitize(new_visual_raw).strip()
        else:
            new_visual = ""
        # Allow regenerate-from-refs when the LLM didn't return a fresh
        # visual_description: fall back to the existing one. Without this,
        # ref-only re-anchors (no visual tweak text) would error.
        if len(new_visual) < VISUAL_DESC_MIN:
            if ref_image_paths:
                new_visual = character.get("visual_description") or ""
            if len(new_visual) < VISUAL_DESC_MIN:
                raise ValueError(
                    f"regenerate decision missing valid "
                    f"updated_visual_description (need ≥{VISUAL_DESC_MIN} chars, "
                    f"got {len(new_visual)})",
                )

        target_path = char_dir / "sheet.png"
        history_path = history_dir / f"{history_ts}.png"
        self._snapshot_history(target_path, history_dir, history_path)

        proxy = dict(character)
        proxy["visual_description"] = new_visual

        ref_disk = _resolve_ref_paths(project_id, ref_image_paths or [])
        prompt_text = self._build_sheet_prompt(proxy, preset)

        try:
            if ref_disk:
                edited = await self._image.edit(
                    prompt=prompt_text,
                    images=ref_disk,
                    size=SIZE_SQUARE,
                    quality=QUALITY_HIGH,
                    save_to=char_dir,
                    project_id=project_id,
                )
                generated = [edited]
            else:
                generated = await self._image.generate(
                    prompt=prompt_text,
                    size=SIZE_SQUARE,
                    quality=QUALITY_HIGH,
                    n=1,
                    save_to=char_dir,
                    project_id=project_id,
                )
        except ProviderContentPolicyError as e:
            msg = e.original_message or str(e)
            logger.warning(
                "regenerate blocked by content policy char=%s",
                character["id"],
            )
            raise OrchestratorError(
                f"regenerate blocked by content policy: {msg}",
            ) from e

        if not generated:
            raise OrchestratorError("regenerate returned no images")
        new_file = generated[0]

        try:
            self._install_new_sheet(new_file, target_path, history_path)
        except OSError as e:
            raise OrchestratorError(
                f"failed to install regenerated sheet: {e}",
            ) from e

        entry = {
            "timestamp": int(time.time()),
            "user_text": user_text,
            "type": "regenerate",
            "previous_visual_description": character["visual_description"],
            "rationale": decision.get("rationale"),
            "changed_fields": decision.get("changed_fields") or [],
        }
        self._append_edit_history(character["id"], entry)
        db.update_character(
            character["id"],
            master_sheet_path=str(target_path),
            visual_description=new_visual,
        )
        edit_count = self._count_edits(character["id"])
        logger.info(
            "character edit ok type=regenerate char=%s edits=%d",
            character["id"], edit_count,
        )
        return {
            "character_id": character["id"],
            "type": "regenerate",
            "master_sheet_path": str(target_path),
            "edit_count": edit_count,
        }

    def _snapshot_history(
        self,
        source: Path,
        history_dir: Path,
        history_path: Path,
    ) -> None:
        if not source.exists():
            return
        try:
            history_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, history_path)
        except OSError as e:
            logger.warning(
                "history snapshot skipped (OS error): %s", e,
            )

    def _install_new_sheet(
        self,
        produced: Path,
        target: Path,
        history_path: Path,
    ) -> None:
        try:
            if target.exists():
                target.unlink()
            produced.replace(target)
        except OSError:
            if history_path.exists():
                try:
                    shutil.copy2(history_path, target)
                except OSError:
                    pass
            raise

    def _append_edit_history(
        self,
        character_id: str,
        entry: dict,
    ) -> None:
        ts = db.now_ts()
        with db.txn() as conn:
            row = conn.execute(
                "SELECT edit_history FROM characters WHERE id = ?",
                (character_id,),
            ).fetchone()
            if row is None:
                return
            raw = row["edit_history"]
            history: list = []
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        history = parsed
                except json.JSONDecodeError:
                    history = []
            history.append(entry)
            conn.execute(
                "UPDATE characters SET edit_history = ?, "
                "updated_at = ? WHERE id = ?",
                (json.dumps(history), ts, character_id),
            )

    def _count_edits(self, character_id: str) -> int:
        fresh = db.get_character(character_id)
        if not fresh:
            return 0
        history = fresh.get("edit_history") or []
        return sum(
            1 for e in history
            if isinstance(e, dict) and e.get("type") in ("surgical", "regenerate")
        )

    def _delete_and_repack_characters(
        self,
        *,
        project_id: str,
        character_id: str,
    ) -> None:
        ts = db.now_ts()
        with db.txn() as conn:
            conn.execute(
                "DELETE FROM characters WHERE id = ?",
                (character_id,),
            )
            rows = conn.execute(
                "SELECT id FROM characters WHERE project_id = ? "
                "ORDER BY ordinal ASC, created_at ASC",
                (project_id,),
            ).fetchall()
            # Two-pass to avoid UNIQUE(project_id, ordinal) collisions.
            # Use large positive temporaries so the CHECK(ordinal>=1) holds.
            for idx, r in enumerate(rows):
                conn.execute(
                    "UPDATE characters SET ordinal = ?, "
                    "updated_at = ? WHERE id = ?",
                    (1_000_000 + idx, ts, r["id"]),
                )
            for idx, r in enumerate(rows, start=1):
                conn.execute(
                    "UPDATE characters SET ordinal = ?, "
                    "updated_at = ? WHERE id = ?",
                    (idx, ts, r["id"]),
                )

    _NAME_STOPWORDS = frozenset({
        "a", "an", "the", "in", "with", "on", "at", "of",
        "and", "or", "but", "to", "for", "by",
    })

    def _derive_character_name(
        self,
        description: str,
        ordinal: int,
        existing_names: set[str],
    ) -> str:
        head = description.split(",", 1)[0].strip()
        head = re.sub(r"^(a|an|the)\s+", "", head, flags=re.IGNORECASE)
        words = [
            w for w in head.split()[:6]
            if w.lower().strip(",.;:'\"") not in self._NAME_STOPWORDS
        ]
        pick = words[:2]
        if not pick:
            base = f"Character {ordinal}"
        else:
            base = " ".join(w.capitalize() for w in pick)[:80]
            if not base:
                base = f"Character {ordinal}"
        candidate = base
        suffix = 2
        while candidate.lower() in existing_names:
            candidate = f"{base} {suffix}"[:80]
            suffix += 1
        return candidate

    def _derive_character_persona(self, description: str) -> str:
        if len(description) >= 10:
            return description
        return f"a character described as: {description}"

    # ---- timeline helpers ----

    def _character_for_writer(self, character: dict) -> dict:
        return {
            "id": character["id"],
            "ordinal": character["ordinal"],
            "name": character["name"],
            "role": character.get("role"),
            "persona": character["persona"],
            "visual_description": character["visual_description"],
            "voice_personality": character.get("voice_personality"),
        }

    def _check_narrator_word_count(self, script: str, target: int) -> None:
        if not script or target <= 0:
            return
        wc = len(script.split())
        # Soft check: log a warning if more than 50% off the target.
        if wc < target * 0.5 or wc > target * 1.5:
            logger.warning(
                "narrator_script word count off target=%d got=%d (>50%% deviation)",
                target, wc,
            )

    async def _call_timeline_writer(
        self,
        *,
        project_id: str,
        prompt_body: str,
        valid_char_ids: set[str],
        target_duration: int,
    ) -> dict:
        base_system = (
            "You are the Sprite Studio Timeline Writer. Reply ONLY with raw "
            "JSON matching the schema in the user message. No prose, no "
            "markdown, no code fences. Be decisive: do not deliberate at "
            "length, just emit the JSON."
        )
        try:
            parsed = await self._chat.chat_json(
                model=KIMI_MODEL,
                messages=[
                    {"role": "system", "content": base_system},
                    {"role": "user", "content": prompt_body},
                ],
                project_id=project_id,
                max_tokens=TIMELINE_MAX_TOKENS,
                read_timeout_seconds=TIMELINE_READ_TIMEOUT,
            )
        except ProviderResponseShapeError as e:
            logger.warning(
                "timeline_writer shape error project=%s; retrying strict: %s",
                project_id, e,
            )
            parsed = await self._chat.chat_json(
                model=KIMI_MODEL,
                messages=[
                    {"role": "system", "content": _JSON_STRICT_SYSTEM},
                    {"role": "user", "content": prompt_body},
                ],
                project_id=project_id,
                max_tokens=TIMELINE_MAX_TOKENS,
                read_timeout_seconds=TIMELINE_READ_TIMEOUT,
            )

        # Bind first_err in the function scope. Python 3 deletes the
        # `except ValueError as e` binding when the block exits (PEP 3134),
        # so the feedback prompt below cannot reference the exception
        # variable directly; copy the message into a stable local first.
        first_err: str = ""
        try:
            self._validate_timeline(
                parsed, valid_char_ids=valid_char_ids,
                target_duration=target_duration,
            )
            return parsed
        except ValueError as exc:
            first_err = _sanitize_error(str(exc))
            logger.warning(
                "timeline validation failed (attempt 1) project=%s reason=%s",
                project_id, first_err,
            )

        try:
            assistant_echo = json.dumps(parsed) if isinstance(parsed, (dict, list)) else str(parsed)
        except (TypeError, ValueError):
            assistant_echo = "{}"

        feedback = (
            f"Your previous output was invalid because: {first_err}\n\n"
            f"Regenerate the FULL timeline JSON, fixing the issue above. "
            f"Hard reminders: use only the character IDs from the cast in "
            f"INPUT; total duration_seconds across shots must sum to "
            f"{target_duration}±{TIMELINE_DURATION_TOLERANCE}s; camera must "
            f"be one of the listed enum values; produce "
            f"1..{TIMELINE_MAX_SHOTS} shots total; every shot must have "
            f"either characters_present or dialog_speakers non-empty; if "
            f"character_dialog is non-empty, every line MUST be embedded "
            f"verbatim in the action text as a quoted phrase; set "
            f"use_narrator=true ONLY if the brief explicitly asks for a "
            f"narrator/voice-over."
        )
        logger.info(
            "timeline retry attempt 2: feeding back error project=%s reason=%r",
            project_id, first_err,
        )

        try:
            parsed_retry = await self._chat.chat_json(
                model=KIMI_MODEL,
                messages=[
                    {"role": "system", "content": base_system},
                    {"role": "user", "content": prompt_body},
                    {"role": "assistant", "content": assistant_echo},
                    {"role": "user", "content": feedback},
                ],
                project_id=project_id,
                max_tokens=TIMELINE_MAX_TOKENS,
                read_timeout_seconds=TIMELINE_READ_TIMEOUT,
            )
        except ProviderResponseShapeError as e:
            raise TimelineGenerationFailedError(
                f"timeline writer returned non-JSON on retry: {e}",
            ) from e

        try:
            self._validate_timeline(
                parsed_retry, valid_char_ids=valid_char_ids,
                target_duration=target_duration,
            )
        except ValueError as exc:
            second_err = _sanitize_error(str(exc))
            raise TimelineGenerationFailedError(
                f"timeline writer failed validation twice. "
                f"first: {first_err} | second: {second_err}",
            ) from exc
        return parsed_retry

    def _validate_timeline(
        self,
        parsed: Any,
        *,
        valid_char_ids: set[str],
        target_duration: int,
    ) -> None:
        if not isinstance(parsed, dict):
            raise ValueError("response was not a JSON object")
        title = parsed.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("missing or empty 'title'")

        use_narrator = bool(parsed.get("use_narrator", False))
        narrator_script = parsed.get("narrator_script")
        if use_narrator:
            if not isinstance(narrator_script, str) or not narrator_script.strip():
                raise ValueError(
                    "use_narrator=true but narrator_script is empty or missing",
                )
            wc = len(narrator_script.split())
            expected = int(round(target_duration * 2.2))
            if expected > 0 and not (
                expected * 0.7 <= wc <= expected * 1.3
            ):
                raise ValueError(
                    f"narrator_script word count {wc} out of band; "
                    f"expected ~{expected} (±30%) for {target_duration}s "
                    f"at 2.2 wps",
                )
        else:
            if narrator_script not in (None, ""):
                logger.warning(
                    "use_narrator=false but narrator_script populated; "
                    "clearing it",
                )
                parsed["narrator_script"] = None

        shots = parsed.get("shots")
        if not isinstance(shots, list):
            raise ValueError("'shots' must be a JSON array")
        if not (TIMELINE_MIN_SHOTS <= len(shots) <= TIMELINE_MAX_SHOTS):
            raise ValueError(
                f"need {TIMELINE_MIN_SHOTS}..{TIMELINE_MAX_SHOTS} shots "
                f"(got {len(shots)})",
            )

        seen_ordinals: set[int] = set()
        total_duration = 0
        for idx, shot in enumerate(shots):
            if not isinstance(shot, dict):
                raise ValueError(f"shot at index {idx} is not a JSON object")
            ordinal_val = shot.get("ordinal")
            if not isinstance(ordinal_val, int) or ordinal_val < 1:
                raise ValueError(
                    f"shot at index {idx} has invalid ordinal: {ordinal_val!r}",
                )
            if ordinal_val in seen_ordinals:
                raise ValueError(
                    f"duplicate shot ordinal: {ordinal_val}",
                )
            seen_ordinals.add(ordinal_val)

            dur = shot.get("duration_seconds")
            if not isinstance(dur, int) or not (5 <= dur <= 15):
                raise ValueError(
                    f"shot {ordinal_val} duration_seconds must be int in 5..15 "
                    f"(got {dur!r})",
                )
            total_duration += dur

            setting = shot.get("setting")
            if not isinstance(setting, str) or len(setting.strip()) < 5:
                raise ValueError(
                    f"shot {ordinal_val} 'setting' is missing or too short",
                )
            action = shot.get("action")
            if not isinstance(action, str) or len(action.strip()) < 5:
                raise ValueError(
                    f"shot {ordinal_val} 'action' is missing or too short",
                )

            camera = shot.get("camera")
            if not isinstance(camera, str) or camera.strip() not in ALLOWED_CAMERAS:
                raise ValueError(
                    f"shot {ordinal_val} camera {camera!r} not in allowed enum: "
                    f"{sorted(ALLOWED_CAMERAS)}",
                )

            present_raw = shot.get("characters_present")
            chars_present: list[str] = []
            if isinstance(present_raw, list):
                for cid in present_raw:
                    if not isinstance(cid, str):
                        continue
                    if cid not in valid_char_ids:
                        raise ValueError(
                            f"shot {ordinal_val} characters_present references "
                            f"unknown character id {cid!r}; valid ids are "
                            f"{sorted(valid_char_ids)}",
                        )
                    chars_present.append(cid)

            speakers_raw = shot.get("dialog_speakers")
            dialog_speakers: list[str] = []
            if isinstance(speakers_raw, list):
                for sid in speakers_raw:
                    if not isinstance(sid, str):
                        continue
                    if sid not in valid_char_ids:
                        raise ValueError(
                            f"shot {ordinal_val} dialog_speakers contains "
                            f"unknown char_id {sid!r}; valid ids are "
                            f"{sorted(valid_char_ids)}",
                        )
                    dialog_speakers.append(sid)

            if not chars_present and not dialog_speakers:
                raise ValueError(
                    f"shot {ordinal_val}: both characters_present and "
                    f"dialog_speakers are empty. At least one character must "
                    f"be on-screen or speaking.",
                )

            char_dialog = shot.get("character_dialog")
            has_dialog_derived = (
                isinstance(char_dialog, list) and len(char_dialog) > 0
            )
            has_dialog_claimed = shot.get("has_dialog")
            if (
                has_dialog_claimed is not None
                and bool(has_dialog_claimed) != has_dialog_derived
            ):
                logger.warning(
                    "shot %d: has_dialog=%s contradicts character_dialog=%s; "
                    "using derived",
                    ordinal_val, has_dialog_claimed, char_dialog,
                )

            if has_dialog_derived:
                # Every dialog line's speaker must be in dialog_speakers OR
                # characters_present (legacy fallback for LLMs that omit
                # dialog_speakers for on-screen lines).
                union_speakers = set(dialog_speakers) | set(chars_present)
                action_lower = action.lower()
                for entry in char_dialog:
                    if not isinstance(entry, dict):
                        raise ValueError(
                            f"shot {ordinal_val}: character_dialog entry is "
                            f"not a JSON object",
                        )
                    cid = entry.get("char_id")
                    line = entry.get("line")
                    if not isinstance(line, str) or not line.strip():
                        raise ValueError(
                            f"shot {ordinal_val}: character_dialog entry "
                            f"missing 'line'",
                        )
                    if cid not in union_speakers:
                        raise ValueError(
                            f"shot {ordinal_val}: dialog line by {cid!r} but "
                            f"they are neither in characters_present nor "
                            f"dialog_speakers",
                        )
                    line_core = (
                        line.strip()
                            .rstrip(".!?\"”")
                            .lstrip("\"“")
                            .lower()
                    )
                    if line_core and line_core not in action_lower:
                        raise ValueError(
                            f"shot {ordinal_val}: dialog line {line!r} not "
                            f"embedded in action text. Action MUST contain "
                            f"the spoken line as a quoted phrase for Seedance "
                            f"to generate speech. Use format: "
                            f"'{{Name}} says: \"{{line}}\"' (on-screen) or "
                            f"'Off-screen, {{Name}} says: \"{{line}}\"' "
                            f"(off-screen).",
                        )

            # Persist normalized fields back onto the parsed dict so the
            # downstream persistence helpers see clean values.
            shot["characters_present"] = chars_present
            shot["dialog_speakers"] = dialog_speakers
            shot["has_dialog"] = has_dialog_derived

            narration_excerpt = shot.get("narration_excerpt")
            if use_narrator:
                if (
                    not isinstance(narration_excerpt, str)
                    or not narration_excerpt.strip()
                ):
                    raise ValueError(
                        f"shot {ordinal_val}: use_narrator=true but "
                        f"narration_excerpt is missing or empty",
                    )
            else:
                if narration_excerpt:
                    logger.warning(
                        "shot %d: narration_excerpt set but "
                        "use_narrator=false; clearing",
                        ordinal_val,
                    )
                    shot["narration_excerpt"] = None

        sorted_ords = sorted(seen_ordinals)
        if sorted_ords != list(range(1, len(shots) + 1)):
            raise ValueError(
                f"shot ordinals must be sequential 1..N (got {sorted_ords})",
            )

        lo = target_duration - TIMELINE_DURATION_TOLERANCE
        hi = target_duration + TIMELINE_DURATION_TOLERANCE
        if not (lo <= total_duration <= hi):
            raise ValueError(
                f"shot durations sum to {total_duration}s; "
                f"target {target_duration}s ±{TIMELINE_DURATION_TOLERANCE}s "
                f"(allowed range {lo}..{hi})",
            )

    def _filter_character_dialog(
        self,
        raw: Any,
        *,
        characters_present: set[str],
        shot_ordinal: int,
    ) -> Optional[list[dict]]:
        """Filter raw dialog entries against the union of on-screen and
        off-screen speakers for this shot. The ``characters_present`` arg
        is the speaker pool (may include off-screen char_ids that aren't
        visually present)."""
        if raw is None:
            return None
        if not isinstance(raw, list):
            logger.warning(
                "shot %d character_dialog dropped (not a list): %r",
                shot_ordinal, type(raw).__name__,
            )
            return None
        cleaned: list[dict] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            cid = entry.get("char_id")
            line = entry.get("line")
            if not isinstance(line, str) or not line.strip():
                continue
            if cid not in characters_present:
                logger.warning(
                    "shot %d dropping dialog line: char_id %r is neither "
                    "in characters_present nor in dialog_speakers",
                    shot_ordinal, cid,
                )
                continue
            cleaned.append({"char_id": cid, "line": line.strip()})
        return cleaned or None

    def _persist_shot_rows(
        self,
        *,
        project_id: str,
        shots_data: list[dict],
    ) -> list[dict]:
        ordered = sorted(shots_data, key=lambda s: s["ordinal"])
        rows: list[dict] = []
        for shot in ordered:
            present = list(shot.get("characters_present") or [])
            speakers = list(shot.get("dialog_speakers") or [])
            # Allow off-screen-only speakers (in dialog_speakers but not
            # characters_present) to attach dialog lines.
            speaker_pool = set(present) | set(speakers)
            cd = self._filter_character_dialog(
                shot.get("character_dialog"),
                characters_present=speaker_pool,
                shot_ordinal=shot["ordinal"],
            )
            narration_excerpt = shot.get("narration_excerpt")
            narration_line = (
                _sanitize(narration_excerpt).strip()
                if isinstance(narration_excerpt, str) and narration_excerpt.strip()
                else None
            )
            has_dialog = bool(cd) and len(cd) > 0
            # transition_to_next is optional in the LLM schema; defensive
            # default + validation keeps a single bad value from failing
            # the whole timeline persist (db.create_shot would raise).
            raw_trans = shot.get("transition_to_next")
            if isinstance(raw_trans, str):
                raw_trans = raw_trans.strip().lower()
            if raw_trans not in db.VALID_SHOT_TRANSITIONS:
                if raw_trans is not None:
                    logger.warning(
                        "shot %d: invalid transition_to_next=%r; defaulting to 'cut'",
                        shot.get("ordinal"), raw_trans,
                    )
                raw_trans = "cut"
            row = db.create_shot(
                project_id=project_id,
                ordinal=shot["ordinal"],
                duration_seconds=shot["duration_seconds"],
                setting=_sanitize(shot["setting"]).strip(),
                action=_sanitize(shot["action"]).strip(),
                camera=_sanitize(shot["camera"]).strip(),
                emotion=_sanitize(shot.get("emotion") or "").strip() or None,
                characters_present=present,
                dialog_speakers=speakers,
                narration_line=narration_line,
                character_dialog=cd,
                has_dialog=has_dialog,
                transition_to_next=raw_trans,
            )
            rows.append(row)
        logger.info(
            "shots persisted project=%s count=%d total_dur=%ds",
            project_id, len(rows),
            sum(r["duration_seconds"] for r in rows),
        )
        return rows

    async def _generate_all_reference_stills(
        self,
        *,
        project_id: str,
        shot_rows: list[dict],
        char_lookup: dict[str, dict],
        preset: StylePreset,
    ) -> tuple[list[dict], list[dict]]:
        async def _one(shot: dict) -> tuple[dict, Optional[str], Optional[str]]:
            chars_in_shot = [
                char_lookup[cid]
                for cid in shot["characters_present"]
                if cid in char_lookup
            ]
            try:
                ref_path = await self._generate_reference_still(
                    project_id=project_id,
                    shot=shot,
                    chars_in_shot=chars_in_shot,
                    preset=preset,
                )
                db.update_shot(shot["id"], reference_still_path=str(ref_path))
                return shot, str(ref_path), None
            except ProviderContentPolicyError as e:
                err_msg = f"content_policy: {e.original_message or e}"
                logger.warning(
                    "reference_still blocked by content policy shot=%s ord=%d: %s",
                    shot["id"], shot["ordinal"], err_msg,
                )
                return shot, None, err_msg
            except SpriteStudioError as e:
                err_msg = f"image_edit_failed: {e}"
                logger.warning(
                    "reference_still failed shot=%s ord=%d: %s",
                    shot["id"], shot["ordinal"], err_msg,
                )
                return shot, None, err_msg
            except OrchestratorError as e:
                err_msg = str(e)
                logger.warning(
                    "reference_still skipped shot=%s ord=%d: %s",
                    shot["id"], shot["ordinal"], err_msg,
                )
                return shot, None, err_msg

        tasks = [asyncio.create_task(_one(s)) for s in shot_rows]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[dict] = []
        errors: list[dict] = []
        for outcome in gathered:
            if isinstance(outcome, BaseException):
                err_msg = f"unexpected_error: {outcome}"
                logger.exception(
                    "reference_still task crashed: %s", err_msg,
                )
                errors.append({"shot_id": None, "ordinal": None, "error_msg": err_msg})
                continue
            shot, ref_path, err = outcome
            char_names = [
                char_lookup[cid]["name"]
                for cid in shot["characters_present"]
                if cid in char_lookup
            ]
            results.append({
                "id": shot["id"],
                "ordinal": shot["ordinal"],
                "duration_seconds": shot["duration_seconds"],
                "setting": shot["setting"],
                "action": shot["action"],
                "narration_excerpt": shot.get("narration_line"),
                "reference_still_path": ref_path,
                "character_names": char_names,
            })
            if err:
                errors.append({
                    "shot_id": shot["id"],
                    "ordinal": shot["ordinal"],
                    "error_msg": err,
                })
        results.sort(key=lambda r: r["ordinal"])
        return results, errors

    async def _generate_reference_still(
        self,
        *,
        project_id: str,
        shot: dict,
        chars_in_shot: list[dict],
        preset: StylePreset,
    ) -> Path:
        if not chars_in_shot:
            raise OrchestratorError(
                f"shot {shot['ordinal']} has no resolvable characters",
            )

        sheet_paths: list[Path] = []
        for c in chars_in_shot:
            sheet_str = c.get("master_sheet_path")
            if not sheet_str:
                raise OrchestratorError(
                    f"shot {shot['ordinal']}: character {c['name']!r} "
                    f"has no master_sheet_path",
                )
            sheet_path = Path(sheet_str)
            if not sheet_path.exists():
                raise OrchestratorError(
                    f"shot {shot['ordinal']}: master sheet missing on disk "
                    f"for {c['name']!r} at {sheet_str}",
                )
            sheet_paths.append(sheet_path)

        prompt = self._build_shot_reference_prompt(shot, chars_in_shot, preset)

        shot_dir = PROJECTS_ROOT / project_id / "shots" / shot["id"]
        shot_dir.mkdir(parents=True, exist_ok=True)

        produced = await self._image.edit(
            prompt=prompt,
            images=sheet_paths,
            size=SIZE_PORTRAIT,
            quality=QUALITY_HIGH,
            save_to=shot_dir,
            project_id=project_id,
        )

        target = shot_dir / "reference.png"
        try:
            if target.exists():
                target.unlink()
            produced.replace(target)
        except OSError as e:
            raise OrchestratorError(
                f"failed to install reference still for shot {shot['ordinal']}: {e}",
            ) from e
        return target

    async def _decide_shot_edit(
        self,
        *,
        project_id: str,
        shot: dict,
        user_text: str,
    ) -> dict:
        shot_payload = {
            "shot_id": shot["id"],
            "ordinal": shot["ordinal"],
            "duration_seconds": shot["duration_seconds"],
            "setting": shot["setting"],
            "action": shot["action"],
            "camera": shot.get("camera"),
            "emotion": shot.get("emotion"),
            "characters_present": shot.get("characters_present") or [],
            "dialog_speakers": shot.get("dialog_speakers") or [],
            "has_dialog": bool(shot.get("has_dialog")),
            "narration_excerpt": shot.get("narration_line"),
            "character_dialog": shot.get("character_dialog"),
        }
        prompt_body = load_prompt(
            "shot_edit",
            shot_json=json.dumps(shot_payload, indent=2),
            user_text=user_text,
        )
        parsed = await self._chat_json_with_retry(
            project_id=project_id,
            user_prompt=prompt_body,
            base_system="You are the Sprite Studio Shot Edit Translator.",
        )
        if not isinstance(parsed, dict):
            raise ValueError("shot_edit response was not a JSON object")
        return parsed

    def _apply_shot_decision(
        self,
        *,
        shot: dict,
        updated_raw: dict,
        valid_char_ids: set[str],
        char_lookup: dict[str, dict],
    ) -> tuple[dict, list[str]]:
        """Translate the LLM's updated_shot into DB-column updates.

        Validates characters_present against the cast (refuses on unknown
        ids), clips duration_seconds to 5..15, drops invalid camera values,
        and filters character_dialog to only lines for characters in the
        final present set. Returns (update_fields, current_characters_present).
        """
        updates: dict[str, Any] = {}

        if "setting" in updated_raw:
            new_setting = _sanitize(str(updated_raw["setting"] or "")).strip()
            if new_setting and new_setting != shot["setting"]:
                updates["setting"] = new_setting

        if "action" in updated_raw:
            new_action = _sanitize(str(updated_raw["action"] or "")).strip()
            if new_action and new_action != shot["action"]:
                updates["action"] = new_action

        if "camera" in updated_raw:
            cam_raw = updated_raw["camera"]
            if cam_raw is None:
                pass
            else:
                cam = _sanitize(str(cam_raw)).strip()
                if cam in ALLOWED_CAMERAS and cam != (shot.get("camera") or ""):
                    updates["camera"] = cam
                elif cam not in ALLOWED_CAMERAS:
                    logger.warning(
                        "shot_edit dropping invalid camera %r (not in %s)",
                        cam, sorted(ALLOWED_CAMERAS),
                    )

        if "emotion" in updated_raw:
            em_raw = updated_raw["emotion"]
            new_emotion = (
                _sanitize(str(em_raw)).strip() if em_raw is not None else None
            ) or None
            if new_emotion != shot.get("emotion"):
                updates["emotion"] = new_emotion

        if "duration_seconds" in updated_raw:
            d_raw = updated_raw["duration_seconds"]
            try:
                d_int = int(d_raw)
            except (TypeError, ValueError):
                d_int = shot["duration_seconds"]
            clipped = max(5, min(15, d_int))
            if clipped != d_int:
                logger.warning(
                    "shot_edit clipped duration_seconds %d→%d (allowed 5..15)",
                    d_int, clipped,
                )
            if clipped != shot["duration_seconds"]:
                updates["duration_seconds"] = clipped

        # narration_excerpt → narration_line column
        if "narration_excerpt" in updated_raw:
            narr_raw = updated_raw["narration_excerpt"]
            new_narr = (
                _sanitize(str(narr_raw)).strip()
                if narr_raw is not None else None
            ) or None
            if new_narr != shot.get("narration_line"):
                updates["narration_line"] = new_narr

        if "characters_present" in updated_raw:
            present_raw = updated_raw["characters_present"]
            if not isinstance(present_raw, list) or not present_raw:
                raise ValueError(
                    "characters_present must be a non-empty list",
                )
            cleaned_present: list[str] = []
            seen: set[str] = set()
            for cid in present_raw:
                if not isinstance(cid, str):
                    continue
                if cid in seen:
                    continue
                if cid not in valid_char_ids:
                    valid_listing = ", ".join(
                        f"{c['ordinal']}={c['name']}({c['id']})"
                        for c in sorted(
                            char_lookup.values(),
                            key=lambda c: c["ordinal"],
                        )
                    )
                    raise ValueError(
                        f"characters_present references unknown id {cid!r}; "
                        f"valid characters: {valid_listing}",
                    )
                seen.add(cid)
                cleaned_present.append(cid)
            if not cleaned_present:
                raise ValueError(
                    "characters_present resolved to empty list after cleaning",
                )
            current_present = cleaned_present
            if current_present != (shot.get("characters_present") or []):
                updates["characters_present"] = current_present
        else:
            current_present = list(shot.get("characters_present") or [])

        # dialog_speakers (off-screen voices). Filter unknown ids; allow
        # empty list (a shot with no speakers is fine if characters_present
        # is non-empty).
        current_speakers = list(shot.get("dialog_speakers") or [])
        if "dialog_speakers" in updated_raw:
            speakers_raw = updated_raw["dialog_speakers"] or []
            cleaned_speakers: list[str] = []
            seen_speakers: set[str] = set()
            if isinstance(speakers_raw, list):
                for sid in speakers_raw:
                    if not isinstance(sid, str):
                        continue
                    if sid in seen_speakers:
                        continue
                    if sid not in valid_char_ids:
                        logger.warning(
                            "shot_edit dropping unknown dialog_speaker id %r",
                            sid,
                        )
                        continue
                    seen_speakers.add(sid)
                    cleaned_speakers.append(sid)
            if cleaned_speakers != current_speakers:
                updates["dialog_speakers"] = cleaned_speakers
            current_speakers = cleaned_speakers

        if "character_dialog" in updated_raw:
            cd_raw = updated_raw["character_dialog"]
            if cd_raw is None:
                if shot.get("character_dialog") is not None:
                    updates["character_dialog"] = None
            else:
                # Off-screen-only speakers attach dialog too: validate against
                # the union of characters_present and dialog_speakers.
                speaker_pool = set(current_present) | set(current_speakers)
                filtered = self._filter_character_dialog(
                    cd_raw,
                    characters_present=speaker_pool,
                    shot_ordinal=shot["ordinal"],
                )
                if filtered != shot.get("character_dialog"):
                    updates["character_dialog"] = filtered

        # Recompute has_dialog from whatever character_dialog ends up being.
        if (
            "character_dialog" in updated_raw
            or "characters_present" in updated_raw
            or "dialog_speakers" in updated_raw
        ):
            new_dialog = updates.get(
                "character_dialog", shot.get("character_dialog"),
            )
            new_has_dialog = (
                isinstance(new_dialog, list) and len(new_dialog) > 0
            )
            if new_has_dialog != bool(shot.get("has_dialog")):
                updates["has_dialog"] = new_has_dialog

            # Soft warning when dialog lines are no longer embedded in the
            # action text. We do not error here — a user can intentionally
            # decouple action and structured dialog (e.g. when dropping speech
            # in favour of narrator-only). The render path will still pick
            # up audio from whichever signal is present.
            if new_has_dialog:
                action_for_check = (
                    updates.get("action") or shot.get("action") or ""
                ).lower()
                missing_lines: list[str] = []
                for entry in (new_dialog or []):
                    if not isinstance(entry, dict):
                        continue
                    line = entry.get("line") or ""
                    line_core = (
                        line.strip()
                            .rstrip(".!?\"”")
                            .lstrip("\"“")
                            .lower()
                    )
                    if line_core and line_core not in action_for_check:
                        missing_lines.append(line)
                if missing_lines:
                    logger.warning(
                        "shot_edit shot=%d: dialog line(s) not embedded in "
                        "action text after edit: %s. Seedance will not voice "
                        "these unless the action text contains the quoted line.",
                        shot["ordinal"], missing_lines,
                    )

        return updates, current_present

    async def _regenerate_reference_still(
        self,
        *,
        project_id: str,
        shot: dict,
        chars_in_shot: list[dict],
        preset: StylePreset,
    ) -> Path:
        """Regenerate a shot's reference still after an edit. Snapshots
        the previous reference.png to history/<timestamp>.png before
        installing the new file. If image gen fails, the prior reference
        remains untouched (by raising before the install step).
        """
        if not chars_in_shot:
            raise OrchestratorError(
                f"shot {shot['ordinal']} has no resolvable characters",
            )

        sheet_paths: list[Path] = []
        for c in chars_in_shot:
            sheet_str = c.get("master_sheet_path")
            if not sheet_str:
                raise OrchestratorError(
                    f"shot {shot['ordinal']}: character {c['name']!r} "
                    f"has no master_sheet_path",
                )
            sheet_path = Path(sheet_str)
            if not sheet_path.exists():
                raise OrchestratorError(
                    f"shot {shot['ordinal']}: master sheet missing on disk "
                    f"for {c['name']!r} at {sheet_str}",
                )
            sheet_paths.append(sheet_path)

        prompt = self._build_shot_reference_prompt(shot, chars_in_shot, preset)

        shot_dir = PROJECTS_ROOT / project_id / "shots" / shot["id"]
        shot_dir.mkdir(parents=True, exist_ok=True)
        target = shot_dir / "reference.png"
        history_dir = shot_dir / "history"
        history_path = history_dir / f"{int(time.time())}.png"

        # Snapshot the existing reference into history BEFORE the call.
        # If the call fails, target stays put and history just has a copy.
        self._snapshot_history(target, history_dir, history_path)

        produced = await self._image.edit(
            prompt=prompt,
            images=sheet_paths,
            size=SIZE_PORTRAIT,
            quality=QUALITY_HIGH,
            save_to=shot_dir,
            project_id=project_id,
        )

        try:
            self._install_new_sheet(produced, target, history_path)
        except OSError as e:
            raise OrchestratorError(
                f"failed to install regenerated reference still "
                f"for shot {shot['ordinal']}: {e}",
            ) from e
        return target

    def _build_shot_reference_prompt(
        self,
        shot: dict,
        characters_in_shot: list[dict],
        style_preset: StylePreset,
    ) -> str:
        char_labels = []
        for i, char in enumerate(characters_in_shot, 1):
            char_labels.append(
                f"Image {i}: {char['name']} character model sheet (reference)",
            )
        char_refs = "\n".join(char_labels)

        camera = shot.get("camera") or "static wide"
        emotion = shot.get("emotion") or "neutral"
        prompt = (
            f"You are given {len(characters_in_shot)} character reference image(s):\n\n"
            f"{char_refs}\n\n"
            f"Create a single scene reference frame:\n\n"
            f"Scene: {shot['setting']}\n"
            f"Action: {shot['action']}\n"
            f"Camera: {camera}\n"
            f"Emotion: {emotion}\n"
            f"Aspect ratio: 9:16 portrait\n\n"
            f"Style: {style_preset.descriptor}\n"
            f"Render notes: {style_preset.render_notes}\n\n"
            f"Hard rules:\n"
            f"- The character(s) in the scene MUST look identical to their "
            f"reference model sheet(s).\n"
            f"- Same fur/skin/hair color, same clothing, same proportions.\n"
            f"- Use only the provided character(s); do not introduce additional "
            f"characters.\n"
            f"- No on-screen text, captions, watermarks, logos, or signage.\n"
            f"- Composition matches the camera direction.\n"
            f"- One coherent moment, not a sequence.\n"
        )
        return prompt

    # ---------------------- render (P11) ----------------------

    async def start_render(
        self,
        *,
        project_id: str,
        budget_hard_limit_usd: Optional[float] = None,
    ) -> dict:
        """Kick off a render. Returns immediately with a queued status; the
        actual render runs as a background task. Callers poll /sprite_status
        or read the workers.PROGRESS_BUS for updates.
        """
        from .workers import (
            BUDGET_HARD_LIMIT_USD_DEFAULT,
            RenderWorker,
        )

        project = db.get_project(project_id)
        if project is None:
            raise OrchestratorError(f"unknown project: {project_id}")
        if project["phase"] not in ("render", "failed"):
            raise ProjectInWrongPhaseError(
                f"start_render requires phase 'render' or 'failed', got "
                f"{project['phase']!r}. Run /sprite_approve_timeline first.",
            )

        existing = self._render_tasks.get(project_id)
        if existing is not None and not existing.done():
            raise RenderInProgressError(
                f"render already running for project {project_id}",
            )

        worker = RenderWorker(
            video_client=self._video,
            voice_client=self._voice,
            image_client=self._image,
            budget_hard_limit_usd=(
                budget_hard_limit_usd
                if budget_hard_limit_usd is not None
                else BUDGET_HARD_LIMIT_USD_DEFAULT
            ),
        )
        task = asyncio.create_task(
            worker.render_project(project_id),
            name=f"render_{project_id}",
        )
        self._render_tasks[project_id] = task

        return {
            "project_id": project_id,
            "status": "queued",
            "phase": "render",
        }

    async def cancel_render(self, project_id: str) -> dict:
        from .workers import cancel_render as _cancel
        await _cancel(project_id)
        return {
            "project_id": project_id,
            "status": "cancellation_requested",
        }

    def get_render_task(self, project_id: str) -> Optional[asyncio.Task]:
        """Return the in-flight render task for project_id, if any. Used by
        the smoke test and /sprite_status to await completion when the
        caller wants to block on the result.
        """
        return self._render_tasks.get(project_id)

    async def delete_project(
        self,
        project_id: str,
        *,
        cancel_timeout_s: float = 10.0,
    ) -> dict:
        """Cancel any in-flight work, cascade-delete DB rows, remove asset dir.

        Three-step teardown:
          1. Signal cooperative cancel + hard-cancel any tracked asyncio.Task
             whose name ends with this project_id (timeline_gen, render).
             Wait briefly for the task to acknowledge; raise ProjectBusyError
             if it doesn't.
          2. Cascade-delete all DB rows in a single txn (db.delete_project_cascade,
             which re-validates the ULID before any SQL).
          3. shutil.rmtree the project's asset directory. A failed rmtree is
             logged but does NOT roll back the DB delete: orphan dirs are
             cheaper to sweep at next startup than a half-deleted DB.

        Path-traversal defense: ULID validated here AND in db.delete_project_cascade.

        Raises:
            ValueError: project_id is not a valid ULID.
            db.ProjectNotFoundError: no project row matches project_id.
            ProjectBusyError: cancellation did not complete in cancel_timeout_s.
        """
        if not db._is_valid_ulid(project_id):
            raise ValueError(f"invalid project_id: {project_id!r}")

        project = db.get_project(project_id)
        if project is None:
            raise db.ProjectNotFoundError(f"project not found: {project_id}")

        try:
            await self._cancel_all_tasks_for_project(
                project_id, timeout_s=cancel_timeout_s,
            )
        except asyncio.TimeoutError as e:
            raise ProjectBusyError(
                project_id,
                f"in-flight tasks did not cancel within {cancel_timeout_s}s; "
                f"retry shortly",
            ) from e

        # Defensive: tasks that crashed mid-flight may have left jobs in
        # 'queued' or 'running'. Mark them cancelled before the cascade so
        # the row history reflects the user-driven exit reason.
        for status in ("queued", "running"):
            for job in db.list_jobs(project_id=project_id, status=status):
                try:
                    db.mark_job_cancelled(job["id"], reason="project deleted")
                except Exception:
                    logger.debug(
                        "delete_project: could not mark job %s cancelled",
                        job["id"],
                    )

        project_dir = PROJECTS_ROOT / project_id
        freed_bytes = _project_dir_size_bytes(project_dir)

        result = db.delete_project_cascade(project_id)

        if project_dir.exists():
            try:
                shutil.rmtree(project_dir)
            except OSError as e:
                # DB rows are gone; the assets dir is now orphaned. Logging
                # is enough; the next startup sweep can reclaim the disk.
                logger.warning(
                    "delete_project: rmtree failed pid=%s err=%s "
                    "(DB row gone, asset dir orphaned)",
                    project_id, e,
                )

        # Free the per-project render-task slot so a future project with the
        # same id (impossible with ULIDs, but defensive) gets a clean slot.
        self._render_tasks.pop(project_id, None)

        logger.info(
            "deleted project pid=%s freed=%dB rows=%s",
            project_id, freed_bytes, result["rows_removed"],
        )
        return {
            "deleted": True,
            "id": project_id,
            "freed_bytes": freed_bytes,
            "rows_removed": result["rows_removed"],
        }

    async def _cancel_all_tasks_for_project(
        self,
        project_id: str,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        """Cancel render + background tasks for this project, then await them.

        Render: cooperative signal via CANCELLATION_REGISTRY (lets the worker
        finish its current shot's ffmpeg cleanly), THEN hard-cancel the
        asyncio.Task to short-circuit the wait between shots.

        Background tasks: name pattern is "<kind>_<project_id>" (e.g.
        timeline_gen_01KQ..., render_01KQ...). Match by suffix; a ULID is
        globally unique so cross-project collision is impossible.

        Raises asyncio.TimeoutError if any task is still pending after
        timeout_s (the caller wraps this in ProjectBusyError).
        """
        from . import workers as _workers

        tasks_to_wait: list[asyncio.Task] = []

        render_task = self._render_tasks.get(project_id)
        if render_task is not None and not render_task.done():
            await _workers.cancel_render(project_id)
            render_task.cancel()
            tasks_to_wait.append(render_task)

        suffix = f"_{project_id}"
        for t in list(_BACKGROUND_TASKS):
            if t.done():
                continue
            if t.get_name().endswith(suffix):
                t.cancel()
                tasks_to_wait.append(t)

        if not tasks_to_wait:
            return

        logger.info(
            "delete_project: cancelling %d task(s) for pid=%s",
            len(tasks_to_wait), project_id,
        )
        _done, pending = await asyncio.wait(
            tasks_to_wait, timeout=timeout_s,
        )
        if pending:
            for t in pending:
                t.cancel()
            raise asyncio.TimeoutError(
                f"{len(pending)} task(s) did not cancel within {timeout_s}s"
            )

"""Slash-command handlers for the sprite-studio plugin."""
from __future__ import annotations

import json
import logging
import re
import threading
import time as _time
from pathlib import Path
from typing import Any, Optional

import yaml as _yaml

from . import db, env
from .orchestrator import (
    CastConfirmationRequiredError,
    CastFullError,
    CastTooSmallError,
    CharacterNotFoundError,
    OrchestratorError,
    ProjectInWrongPhaseError,
    ProjectOrchestrator,
    RenderInProgressError,
    ShotNotFoundError,
    TimelineFullError,
    TimelineGenerationFailedError,
    TimelineLastShotError,
    TimelineNotReadyError,
    has_background_task,
    spawn_background,
)
from .services import (
    MODEL_FAST,
    MODEL_STANDARD,
    seedance_cost_from_tokens,
    seedance_token_count,
)
from .services.errors import (
    ProviderContentPolicyError,
    SpriteStudioError,
)
from .workers import (
    BUDGET_HARD_LIMIT_USD_DEFAULT,
    latest_progress,
)


logger = logging.getLogger("sprite_studio.commands")


_PLUGIN_VERSION = "0.1.0"

# Wall-clock estimates for /sprite_render preview and /sprite_status ETA.
# Cost estimates live in _estimate_shot_cost / _estimate_render_cost below
# and reuse services._pricing helpers (single source of truth with actuals).
_SECONDS_PER_SHOT_ESTIMATE = 120
_NARRATION_SECONDS_ESTIMATE = 30
_STITCH_SECONDS_ESTIMATE = 30
_NARRATOR_PER_SCRIPT_ESTIMATE = 0.30
_SHOT_CONCURRENCY_ESTIMATE = 4

# --- Telegram / Discord surface helpers -------------------------------------
#
# Hermes' chat platforms parse `MEDIA:<absolute_path>` lines out of a slash
# command's response string and deliver them as native photo/video messages
# (gateway/platforms/base.py:1750 extract_media + run.py:7757
# _deliver_media_from_response).  The remaining text is sent as the message
# body.  These helpers centralise:
#   * surface detection from the dispatch kwargs (gateway sets `surface=` to
#     the platform name; the project-local bridge sets `surface="api"`),
#   * MEDIA: token formatting with size guards,
#   * markdown formatting for /sprite_status — kept here so per-surface
#     branching never leaks beyond commands.py.

TELEGRAM_PHOTO_LIMIT = 10 * 1024 * 1024     # 10 MB photo upload cap
TELEGRAM_VIDEO_LIMIT = 50 * 1024 * 1024     # 50 MB sendVideo cap

_CHAT_SURFACES = ("telegram", "discord")

_PROGRESS_EMOJI = {
    "queued": "⏳",
    "rendering shots": "🎬",
    "synthesizing narration": "🎙️",
    "picking music": "🎵",
    "stitching final video": "🧵",
    "validating output": "🔍",
    "done": "✅",
    "failed": "❌",
    "cancelled": "🚫",
    "idle": "💤",
}


def _surface(kwargs: dict) -> str:
    """Resolve the dispatch surface from handler kwargs.

    Gateway plumbs `surface=<platform.value>` (e.g. "telegram", "discord").
    The web bridge plumbs `surface="api"`.  CLI dispatch passes nothing —
    the empty kwargs default to "cli".  Lower-cased so equality checks
    are case-insensitive.
    """
    return (kwargs.get("surface") or kwargs.get("platform") or "cli").lower()


def _is_chat_surface(surface: str) -> bool:
    return surface in _CHAT_SURFACES


def _media_line(path: str | Path | None, max_bytes: Optional[int] = None) -> Optional[str]:
    """Return a `MEDIA:<absolute>` line if `path` exists and fits within `max_bytes`.

    Returns None when the path is missing, points to a non-file, or exceeds
    the size limit — so Telegram never receives a MEDIA tag the gateway
    can't actually deliver.  The caller is responsible for emitting a
    fallback note when the guard rejects.
    """
    if not path:
        return None
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    if max_bytes is not None and p.stat().st_size > max_bytes:
        return None
    return f"MEDIA:{p.absolute()}"


def _format_status_for_telegram(plugin_status: dict, project_summary: Optional[dict]) -> str:
    """Markdown status response for Telegram. Plain ASCII + emoji only."""
    lines = ["*Sprite Studio*"]
    env_label = "ok" if plugin_status["env_ok"] else "missing"
    lines.append(f"plugin: {plugin_status['status']} | env: {env_label}")

    if project_summary is None:
        lines.append("")
        lines.append("_no active project_")
        lines.append('send `/sprite_new "your idea"` to start')
        return "\n".join(lines)

    p = project_summary
    step = p["current_step"]
    emoji = _PROGRESS_EMOJI.get(step, "•")
    lines.append("")
    lines.append(f"*{p['title'] or '(untitled)'}*")
    lines.append(f"id: `{p['project_id']}`")
    lines.append(f"phase: *{p['phase']}* {emoji} {step}")
    if p["shots_total"] > 0:
        lines.append(f"shots: {p['shots_done']}/{p['shots_total']}")
    lines.append(f"cost: ${p['total_cost_usd']:.4f}")
    if p.get("eta_seconds"):
        lines.append(f"eta: ~{round(p['eta_seconds'] / 60)} min")
    if p.get("progress_detail"):
        lines.append(f"_{p['progress_detail']}_")
    if p.get("error_message"):
        lines.append(f"⚠️ {p['error_message']}")
    return "\n".join(lines)


async def sprite_status_handler(raw_args: str = "", **kwargs) -> str:
    args = (_strip_brief_quotes(raw_args) or "").strip()

    db_path = db.DB_PATH
    db_size = db_path.stat().st_size if db_path.exists() else 0
    env_status = env.check_required_env(
        ["TOKENROUTER_API_KEY", "ELEVENLABS_API_KEY"]
    )
    plugin_status = {
        "plugin": "sprite-studio",
        "version": _PLUGIN_VERSION,
        "status": "ok",
        "db_path": str(db_path),
        "db_size_bytes": db_size,
        "env_ok": all(env_status.values()),
        "env_present": env_status,
    }

    surface = _surface(kwargs)

    project: Optional[dict] = None
    if args:
        candidate = db.get_project(args)
        if candidate is None:
            return _err_json(
                f"project not found: {args!r}",
                error_class="project_not_found",
            )
        if candidate.get("user_id") != _USER_ID:
            return _err_json(
                "project does not belong to current user",
                project_id=args,
                error_class="forbidden",
            )
        project = candidate
    else:
        project = db.latest_project_for_user(_USER_ID)

    if project is None:
        if _is_chat_surface(surface):
            return _format_status_for_telegram(plugin_status, None)
        return json.dumps({**plugin_status, "project": None})

    project_id = project["id"]
    phase = project["phase"]

    shots = db.list_shots(project_id)
    shots_done = sum(1 for s in shots if s.get("render_status") == "done")
    shots_total = len(shots)

    live = latest_progress(project_id)
    current_step = "idle"
    progress_detail = None
    progress_error = None
    if live:
        current_step = _stage_to_user_step(
            live["stage"], live.get("detail") or "",
        )
        progress_detail = live.get("detail")
        progress_error = live.get("error")
    elif phase == "done":
        current_step = "done"
    elif phase == "failed":
        current_step = "failed"

    eta_seconds: Optional[int] = None
    if live and live.get("stage") == "rendering_shots" and shots_total > 0:
        remaining = max(0, shots_total - shots_done)
        eta_seconds = int(
            remaining * _SECONDS_PER_SHOT_ESTIMATE / _SHOT_CONCURRENCY_ESTIMATE
            + _NARRATION_SECONDS_ESTIMATE
            + _STITCH_SECONDS_ESTIMATE
        )

    project_summary = {
        "project_id": project_id,
        "phase": phase,
        "title": project.get("title"),
        "shots_done": shots_done,
        "shots_total": shots_total,
        "current_step": current_step,
        "progress_detail": progress_detail,
        "progress_error": progress_error,
        "total_cost_usd": float(project.get("total_cost_usd") or 0),
        "eta_seconds": eta_seconds,
        "final_video_path": project.get("final_video_path"),
        "error_message": project.get("error_message"),
    }

    if _is_chat_surface(surface):
        return _format_status_for_telegram(plugin_status, project_summary)
    return json.dumps({**plugin_status, "project": project_summary})


def _stage_to_user_step(stage: str, detail: str) -> str:
    """Translate ProgressBus stages into user-facing /sprite_status text."""
    return {
        "queued": "queued",
        "rendering_shots": "rendering shots",
        "synthesizing_narration": "synthesizing narration",
        "picking_music": "picking music",
        "stitching": "stitching final video",
        "validating": "validating output",
        "done": "done",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(stage, stage)


def _estimate_render_minutes(shot_count: int) -> float:
    return (
        shot_count * _SECONDS_PER_SHOT_ESTIMATE / _SHOT_CONCURRENCY_ESTIMATE
        + _NARRATION_SECONDS_ESTIMATE
        + _STITCH_SECONDS_ESTIMATE
    ) / 60.0


def _estimate_shot_cost(
    duration_seconds: int,
    *,
    resolution: str = "720p",
    ratio: str = "9:16",
    model: str = MODEL_FAST,
) -> float:
    """Estimate Seedance cost for one shot.

    Reuses services._pricing helpers so /sprite_render's budget preview and
    the post-render actuals share one source of truth. Defaults match the
    plugin's render-pipeline defaults (720p, 9:16, FAST tier).

    Returns 0.0 for invalid duration / unknown resolution / unknown model;
    callers treat 0 as "estimator can't price this" rather than free.
    """
    if not isinstance(duration_seconds, int) or duration_seconds <= 0:
        return 0.0
    try:
        tokens = seedance_token_count(
            resolution=resolution,
            ratio=ratio,
            duration_seconds=duration_seconds,
        )
    except ValueError:
        return 0.0
    return seedance_cost_from_tokens(model=model, tokens=tokens)


def _estimate_render_cost(shots: list[dict], use_narrator: bool) -> float:
    """Sum per-shot Seedance estimates plus optional narrator cost.

    Tier resolved from env.get_video_tier() so /sprite_render --confirm-budget
    matches what RenderWorker will actually charge.
    """
    cost = 0.0
    if shots:
        tier = env.get_video_tier()
        model = MODEL_STANDARD if tier == "standard" else MODEL_FAST
        for shot in shots:
            cost += _estimate_shot_cost(shot.get("duration_seconds"), model=model)
    if use_narrator:
        cost += _NARRATOR_PER_SCRIPT_ESTIMATE
    return cost


# --- shared orchestrator instance (lazy, threadsafe) ---

_orchestrator: Optional[ProjectOrchestrator] = None
_orchestrator_lock = threading.Lock()


def _get_orchestrator() -> ProjectOrchestrator:
    global _orchestrator
    if _orchestrator is not None:
        return _orchestrator
    with _orchestrator_lock:
        if _orchestrator is None:
            _orchestrator = ProjectOrchestrator()
        return _orchestrator


def _strip_brief_quotes(raw_args: str) -> str:
    s = (raw_args or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {'"', "'"}:
        s = s[1:-1]
    return s.strip()


_KV_RE = re.compile(r"^([a-z][a-z0-9_]*)=(.+)$", re.IGNORECASE)


def _split_brief_and_kvs(raw_args: str) -> tuple[str, dict[str, str]]:
    """Split args into (brief_text, kv_pairs).

    Format examples:
      '"my brief"'                       -> ('my brief', {})
      '"my brief" defer_cast=true'       -> ('my brief', {'defer_cast': 'true'})
      '"my brief"\\ndefer_cast=true'      -> ('my brief', {'defer_cast': 'true'})
      '"my brief" refs=a/b.png,c/d.png'  -> ('my brief', {'refs': 'a/b.png,c/d.png'})
      'no quotes here'                   -> ('no quotes here', {})

    The brief text is whatever sits inside the leading matched quote pair.
    Anything after the closing quote is parsed as whitespace-separated
    `key=value` tokens. Backwards-compatible with the existing
    `_strip_brief_quotes` callers: a quoted brief with no trailing kvs
    yields the same brief text and an empty kvs dict.
    """
    s = (raw_args or "").strip()
    if not s:
        return "", {}

    if s[0] not in {'"', "'"}:
        return s, {}

    quote = s[0]
    i = 1
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            i += 2
            continue
        if s[i] == quote:
            break
        i += 1

    if i >= len(s):
        return s[1:].strip(), {}

    brief = s[1:i].replace(f"\\{quote}", quote).strip()
    rest = s[i + 1:].strip()

    kvs: dict[str, str] = {}
    if rest:
        for token in rest.split():
            m = _KV_RE.match(token)
            if m:
                kvs[m.group(1).lower()] = m.group(2)
    return brief, kvs


def _parse_refs_kv(refs_arg: str) -> tuple[list[str], Optional[str]]:
    """Validate a comma-separated refs= kv value.

    Each path must look like a relative asset-server path:
      /<project_id>/refs/<ulid>.<ext>

    Returns (paths, error_msg). On any rejection, error_msg is non-None
    and paths is empty. Empty input returns ([], None).
    """
    if not refs_arg:
        return [], None
    paths = [p.strip() for p in refs_arg.split(",") if p.strip()]
    if not paths:
        return [], None
    for p in paths:
        if ".." in p or "\x00" in p or "\\" in p:
            return [], f"invalid ref path (traversal): {p!r}"
        if not p.startswith("/"):
            return [], f"invalid ref path (must start with /): {p!r}"
        if "/refs/" not in p:
            return [], f"invalid ref path (missing /refs/): {p!r}"
    return paths, None


def _err_json(message: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"status": "error", "message": message}
    payload.update(extra)
    return json.dumps(payload)


def _format_cast_response(start_result: dict, advance_result: dict) -> dict:
    return {
        "status": "ok",
        "project_id": advance_result["project_id"],
        "phase": advance_result["phase"],
        "auto_decisions": start_result.get("auto_decisions", {}),
        "characters": [
            {
                "id": ch["id"],
                "ordinal": ch["ordinal"],
                "name": ch["name"],
                "role": ch.get("role"),
                "voice_id": ch.get("voice_id"),
                "sheet_path": ch.get("sheet_path"),
                "error": ch.get("error_msg"),
            }
            for ch in advance_result.get("characters", [])
        ],
        "cast_dir": advance_result.get("cast_dir"),
        "errors": advance_result.get("errors", []),
    }


def _coerce_json_list(raw: Any) -> list:
    """Tolerantly read a SQLite shot column that may be a list, a JSON
    string, an empty string (legacy data), or NULL. Malformed JSON strings
    log a warning and degrade to []; the response stays well-formed so the
    UI never crashes on a single corrupt row.
    """
    if isinstance(raw, list):
        return raw
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("malformed JSON column in shots row: %r", raw[:120])
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _shot_to_response_dict(shot: dict[str, Any]) -> dict[str, Any]:
    """Single source of truth for the shot dict shape the web client reads.

    Both /sprite_show and the /sprite_timeline already-generated path hit
    this helper so the TS Shot contract at web/src/types/sprite.ts stays
    consistent. Defensive defaults on transition_to_next ('cut') and
    JSON-list columns ([]) prevent UI breakage on legacy or partial rows.
    """
    return {
        "id": shot["id"],
        "project_id": shot.get("project_id"),
        "ordinal": shot["ordinal"],
        "duration_seconds": shot["duration_seconds"],
        "setting": shot.get("setting"),
        "action": shot.get("action"),
        "camera": shot.get("camera"),
        "emotion": shot.get("emotion"),
        "narration_line": shot.get("narration_line"),
        "transition_to_next": shot.get("transition_to_next") or "cut",
        "characters_present": _coerce_json_list(shot.get("characters_present")),
        "character_dialog": _coerce_json_list(shot.get("character_dialog")),
        "dialog_speakers": _coerce_json_list(shot.get("dialog_speakers")),
        "has_dialog": bool(shot.get("has_dialog")),
        "render_status": shot.get("render_status") or "pending",
        "render_error": shot.get("render_error"),
        "reference_still_path": shot.get("reference_still_path"),
        "rendered_video_path": shot.get("rendered_video_path"),
        "cost_usd": (
            float(shot["cost_usd"]) if shot.get("cost_usd") is not None else None
        ),
        "updated_at": shot.get("updated_at"),
    }


# ---- /start ----

_TELEGRAM_WELCOME = (
    "*Sprite Studio* — AI video creation\n"
    "\n"
    '1. `/sprite_new "your idea"` — describe the video\n'
    "2. `/sprite_approve_cast` once characters look right\n"
    "3. `/sprite_timeline` to plan shots\n"
    "4. `/sprite_approve_timeline`\n"
    "5. `/sprite_render` — kicks off the render (~5-10 min)\n"
    "6. `/sprite_show` — receive your final video\n"
    "\n"
    "edit anytime: `/sprite_edit_character N | changes`\n"
    "status: `/sprite_status`"
)


async def start_handler(raw_args: str = "", **kwargs) -> str:
    """Telegram convention: `/start` returns a welcome on first contact.

    On CLI / API surfaces /start is not the entry point — return an empty
    string so it's a graceful no-op.  The gateway treats falsy returns as
    "no message"; an empty CLI return prints nothing.
    """
    if _is_chat_surface(_surface(kwargs)):
        return _TELEGRAM_WELCOME
    return ""


# ---- /sprite_new ----

async def sprite_new_handler(raw_args: str = "", **kwargs) -> str:
    brief, kvs = _split_brief_and_kvs(raw_args)
    if len(brief) < 5:
        return _err_json(
            'usage: /sprite_new "<a one-line brief, 5-4000 chars>" [defer_cast=true]',
        )
    if len(brief) > 4000:
        return _err_json(
            f"brief too long ({len(brief)} chars; max 4000)",
        )

    defer_cast = kvs.get("defer_cast", "").lower() in {"1", "true", "yes"}

    orchestrator = _get_orchestrator()

    try:
        start_result = await orchestrator.start_project(
            brief=brief, surface="cli", user_id="cli",
        )
    except RenderInProgressError as e:
        return _err_json(str(e), error_class="render_in_progress")
    except ValueError as e:
        return _err_json(f"invalid brief: {e}")
    except SpriteStudioError as e:
        return _err_json(
            f"brief clarifier failed: {e}",
            error_class=e.__class__.__name__,
            project_id=getattr(e, "project_id", None),
        )

    if start_result.get("needs_clarification"):
        return json.dumps({
            "status": "needs_clarification",
            "project_id": start_result["project_id"],
            "phase": "brief",
            "questions": start_result.get("questions", []),
            "auto_decisions": start_result.get("auto_decisions", {}),
            "next_steps": (
                'Reply with /sprite_new "<answers>" to refine the brief, '
                "or run /sprite_cast to accept the auto-decisions and "
                "generate the cast immediately."
            ),
        })

    project_id = start_result["project_id"]

    if defer_cast:
        # Caller (typically the web BriefScreen with pending ref uploads)
        # wants the project created and auto_decisions applied, but NOT the
        # cast generated yet. They will upload refs, call
        # /sprite_set_project_refs, then /sprite_cast.
        return json.dumps({
            "status": "draft_ready",
            "project_id": project_id,
            "phase": "brief",
            "auto_decisions": start_result.get("auto_decisions", {}),
            "next_steps": (
                "Upload refs to /<project_id>/refs/upload, then call "
                "/sprite_set_project_refs <paths> followed by /sprite_cast."
            ),
        })

    try:
        advance_result = await orchestrator.advance_to_cast_phase(
            project_id=project_id,
        )
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project_id)
    except SpriteStudioError as e:
        return _err_json(
            f"cast generation failed: {e}",
            project_id=project_id,
            error_class=e.__class__.__name__,
        )
    except ValueError as e:
        return _err_json(
            f"cast validation failed: {e}",
            project_id=project_id,
        )

    return json.dumps(_format_cast_response(start_result, advance_result))


# ---- /sprite_cast ----

async def sprite_cast_handler(raw_args: str = "", **kwargs) -> str:
    project = db.latest_project_for_user("cli", phase="brief")
    if project is None:
        return _err_json(
            "no project in brief phase for user 'cli'. "
            'Run /sprite_new "<brief>" first.',
        )
    project_id = project["id"]
    orchestrator = _get_orchestrator()

    try:
        advance_result = await orchestrator.advance_to_cast_phase(
            project_id=project_id,
        )
    except CastConfirmationRequiredError as e:
        return _err_json(
            f"cast designer proposed {e.proposed_size} characters. "
            f"Estimated cast phase cost ~${e.estimated_cost_usd:.2f}. "
            f"Reply /sprite_approve_cast_size to proceed, "
            f"or edit the brief to reduce the cast.",
            project_id=project_id,
            error_class="cast_confirmation_required",
        )
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project_id)
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project_id)
    except ProviderContentPolicyError as e:
        return _err_json(
            f"content policy block: {e.original_message or e}",
            project_id=project_id,
            error_class="content_policy",
        )
    except SpriteStudioError as e:
        return _err_json(
            f"cast generation failed: {e}",
            project_id=project_id,
            error_class=e.__class__.__name__,
        )
    except ValueError as e:
        return _err_json(
            f"cast validation failed: {e}",
            project_id=project_id,
        )

    return json.dumps({
        "status": "ok",
        "project_id": advance_result["project_id"],
        "phase": advance_result["phase"],
        "characters": [
            {
                "id": ch["id"],
                "ordinal": ch["ordinal"],
                "name": ch["name"],
                "role": ch.get("role"),
                "voice_id": ch.get("voice_id"),
                "sheet_path": ch.get("sheet_path"),
                "error": ch.get("error_msg"),
            }
            for ch in advance_result.get("characters", [])
        ],
        "cast_dir": advance_result.get("cast_dir"),
        "errors": advance_result.get("errors", []),
    })


_USER_ID = "cli"
_HELP_EDIT = (
    'usage: /sprite_edit_character "<ordinal_or_id> | <changes>" '
    '(example: /sprite_edit_character "1 | make her trench coat dark navy")'
)
_HELP_ADD = (
    'usage: /sprite_add_character "<description ≥ 40 chars>"'
)
_HELP_REMOVE = (
    'usage: /sprite_remove_character "<ordinal_or_id>"'
)


def _resolve_active_cast_project() -> tuple[Optional[dict], Optional[str]]:
    """Resolve the user's most-recent project across all phases. If it's
    in 'cast' phase, return (project, None). Otherwise return
    (None, error_message). This ensures multi-project state stays
    coherent: the user's last touched project is the one cast-edit
    commands operate on, even if older cast-phase projects exist.
    """
    latest = db.latest_project_for_user(_USER_ID)
    if latest is None:
        return None, (
            "no project for user 'cli'. "
            'Run /sprite_new "<brief>" first.'
        )
    if latest["phase"] == "cast":
        return latest, None
    if latest["phase"] == "timeline":
        return None, (
            f"cast already approved for project {latest['id']}; "
            f"revert with /sprite_revert_cast first."
        )
    return None, (
        f"latest project is in phase {latest['phase']!r}; "
        f"cannot perform cast operations."
    )


def _resolve_character(
    project: dict,
    token: str,
) -> Optional[dict]:
    """Resolve a character by ordinal (digits) or by id. The character
    must belong to the supplied project; returns None otherwise.
    """
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        try:
            ordinal = int(token)
        except ValueError:
            return None
        for c in db.list_characters(project["id"]):
            if c["ordinal"] == ordinal:
                return c
        return None
    char = db.get_character(token)
    if char is None or char["project_id"] != project["id"]:
        return None
    return char


# ---- /sprite_edit_character ----

async def sprite_edit_character_handler(raw_args: str = "", **kwargs) -> str:
    raw_brief, raw_kvs = _split_brief_and_kvs(raw_args)
    raw = raw_brief if raw_brief else _strip_brief_quotes(raw_args)
    if not raw or "|" not in raw:
        return _err_json(_HELP_EDIT)
    ident, changes = raw.split("|", 1)
    ident = ident.strip()
    changes = changes.strip()
    if not ident or not changes:
        return _err_json(_HELP_EDIT)

    ref_paths, ref_err = _parse_refs_kv(raw_kvs.get("refs", ""))
    if ref_err:
        return _err_json(ref_err)

    project, err = _resolve_active_cast_project()
    if project is None:
        return _err_json(err or "no active cast project")

    character = _resolve_character(project, ident)
    if character is None:
        char_count = len(db.list_characters(project["id"]))
        return _err_json(
            f"character not found in your active project: {ident!r}. "
            f"valid ordinals: 1..{char_count}.",
            project_id=project["id"],
        )

    if ref_paths:
        expected_prefix = f"/{project['id']}/refs/"
        for p in ref_paths:
            if not p.startswith(expected_prefix):
                return _err_json(
                    f"ref path not under active project: {p!r}",
                    project_id=project["id"],
                )

    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.edit_character(
            character_id=character["id"],
            user_text=changes,
            ref_image_paths=ref_paths,
        )
    except CharacterNotFoundError as e:
        return _err_json(str(e), project_id=project["id"])
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project["id"])
    except ValueError as e:
        return _err_json(f"invalid edit request: {e}", project_id=project["id"])
    except ProviderContentPolicyError as e:
        return _err_json(
            f"content policy block: {e.original_message or e}",
            project_id=project["id"],
            error_class="content_policy",
        )
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project["id"])
    except SpriteStudioError as e:
        return _err_json(
            f"edit failed: {e}",
            project_id=project["id"],
            error_class=e.__class__.__name__,
        )

    surface = _surface(kwargs)
    if _is_chat_surface(surface):
        sheet_path = result.get("master_sheet_path")
        lines = [f"*{character['name']}* updated. ({result['type']})"]
        tag = _media_line(sheet_path, max_bytes=TELEGRAM_PHOTO_LIMIT)
        if tag:
            lines.append(tag)
        elif sheet_path:
            lines.append(f"_sheet not deliverable:_ `{sheet_path}`")
        return "\n".join(lines)

    return json.dumps({
        "status": "ok",
        "project_id": project["id"],
        "character_id": result["character_id"],
        "ordinal": character["ordinal"],
        "name": character["name"],
        "type": result["type"],
        "master_sheet_path": result["master_sheet_path"],
        "edit_count": result["edit_count"],
    })


# ---- /sprite_add_character ----

async def sprite_add_character_handler(raw_args: str = "", **kwargs) -> str:
    description, kvs = _split_brief_and_kvs(raw_args)
    if not description:
        return _err_json(_HELP_ADD)

    ref_paths, ref_err = _parse_refs_kv(kvs.get("refs", ""))
    if ref_err:
        return _err_json(ref_err)

    project, err = _resolve_active_cast_project()
    if project is None:
        return _err_json(err or "no active cast project")

    if ref_paths:
        expected_prefix = f"/{project['id']}/refs/"
        for p in ref_paths:
            if not p.startswith(expected_prefix):
                return _err_json(
                    f"ref path not under active project: {p!r}",
                    project_id=project["id"],
                )

    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.add_character(
            project_id=project["id"],
            description=description,
            ref_image_paths=ref_paths,
        )
    except CastFullError as e:
        return _err_json(str(e), project_id=project["id"], error_class="cast_full")
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project["id"])
    except ValueError as e:
        return _err_json(f"invalid description: {e}", project_id=project["id"])
    except ProviderContentPolicyError as e:
        return _err_json(
            f"content policy block: {e.original_message or e}",
            project_id=project["id"],
            error_class="content_policy",
        )
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project["id"])
    except SpriteStudioError as e:
        return _err_json(
            f"add failed: {e}",
            project_id=project["id"],
            error_class=e.__class__.__name__,
        )

    return json.dumps({
        "status": "ok",
        "project_id": project["id"],
        "character_id": result["character_id"],
        "ordinal": result["ordinal"],
        "name": result["name"],
        "role": result.get("role"),
        "persona": result["persona"],
        "master_sheet_path": result["master_sheet_path"],
        "voice_id": result.get("voice_id"),
        "error": result.get("error"),
        "total_count": result["total_count"],
    })


# ---- /sprite_remove_character ----

async def sprite_remove_character_handler(raw_args: str = "", **kwargs) -> str:
    ident = _strip_brief_quotes(raw_args)
    if not ident:
        return _err_json(_HELP_REMOVE)

    project, err = _resolve_active_cast_project()
    if project is None:
        return _err_json(err or "no active cast project")

    character = _resolve_character(project, ident)
    if character is None:
        char_count = len(db.list_characters(project["id"]))
        return _err_json(
            f"character not found in your active project: {ident!r}. "
            f"valid ordinals: 1..{char_count}.",
            project_id=project["id"],
        )

    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.remove_character(
            character_id=character["id"],
        )
    except CharacterNotFoundError as e:
        return _err_json(str(e), project_id=project["id"])
    except CastTooSmallError as e:
        return _err_json(str(e), project_id=project["id"], error_class="cast_too_small")
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project["id"])
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project["id"])

    return json.dumps({
        "status": "ok",
        "project_id": project["id"],
        "removed_id": result["removed_id"],
        "removed_ordinal": character["ordinal"],
        "removed_name": character["name"],
        "remaining_count": result["remaining_count"],
        "remaining": result["remaining"],
    })


# ---- /sprite_approve_cast ----

async def sprite_approve_cast_handler(raw_args: str = "", **kwargs) -> str:
    # Idempotency: scope decisions to the SINGLE most-recent project, not
    # "the latest project happening to be in 'cast' phase". Otherwise a
    # second /sprite_approve_cast call would silently advance a different
    # cast-phase project that the user did not just approve.
    latest = db.latest_project_for_user(_USER_ID)
    if latest is None:
        return _err_json(
            "no project for user 'cli'. "
            'Run /sprite_new "<brief>" first.',
        )
    surface = _surface(kwargs)
    if latest["phase"] == "timeline":
        chars = db.list_characters(latest["id"])
        existing_shots = db.list_shots(latest["id"])
        if _is_chat_surface(surface):
            return _format_cast_approved_for_telegram(
                len(chars), chars, already_approved=True,
            )
        return json.dumps({
            "status": "ok",
            "project_id": latest["id"],
            "phase": "timeline",
            "character_count": len(chars),
            "already_approved": True,
            "timeline_status": _derive_timeline_status(latest, existing_shots),
        })
    if latest["phase"] != "cast":
        return _err_json(
            f"latest project is in phase {latest['phase']!r}; "
            f"cannot approve cast.",
            project_id=latest["id"],
        )
    project = latest
    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.approve_cast(project_id=project["id"])
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project["id"])
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project["id"])

    # Approval just transitioned brief→cast→timeline. Fire-and-forget the
    # timeline generation in the background so the HTTP response returns
    # in <1s; the frontend polls /sprite_show until shots appear.
    timeline_status = "generating"
    if not result.get("already_approved"):
        spawn_background(
            orchestrator._run_timeline_gen_safely(
                project_id=result["project_id"],
            ),
            name=f"timeline_gen_{result['project_id']}",
        )
    else:
        # Defensive: approve_cast() can return already_approved=True if a
        # racing call beat us here. Don't double-fire.
        existing_shots = db.list_shots(result["project_id"])
        timeline_status = _derive_timeline_status(
            db.get_project(result["project_id"]) or {"phase": "timeline"},
            existing_shots,
        )

    if _is_chat_surface(surface):
        chars = db.list_characters(result["project_id"])
        return _format_cast_approved_for_telegram(
            result["character_count"],
            chars,
            already_approved=result.get("already_approved", False),
        )

    return json.dumps({
        "status": "ok",
        "project_id": result["project_id"],
        "phase": result["phase"],
        "character_count": result["character_count"],
        "already_approved": result.get("already_approved", False),
        "timeline_status": timeline_status,
    })


# ---- /sprite_approve_cast_size ----

async def sprite_approve_cast_size_handler(raw_args: str = "", **kwargs) -> str:
    """Flip cast_size_confirmed=1 for the user's latest brief-phase project,
    so a follow-up /sprite_cast bypasses the cost-confirmation gate.

    The gate is only consulted on the cast designer's proposed size during
    /sprite_cast, so the project must still be in 'brief' phase to be
    approvable. If the cast has already advanced (or never proposed a
    large size), reply with a no-op message.
    """
    latest = db.latest_project_for_user(_USER_ID)
    if latest is None:
        return _err_json(
            "no project for user 'cli'. "
            'Run /sprite_new "<brief>" first.',
        )
    if latest["phase"] != "brief":
        return _err_json(
            f"latest project is in phase {latest['phase']!r}; "
            f"cast size can only be approved while a brief is pending. "
            f"Edit the brief or start a new project.",
            project_id=latest["id"],
        )
    db.update_project(latest["id"], cast_size_confirmed=True)
    return json.dumps({
        "status": "ok",
        "project_id": latest["id"],
        "cast_size_confirmed": True,
        "next_step": "/sprite_cast",
    })


def _format_cast_approved_for_telegram(
    char_count: int,
    characters: list[dict],
    *,
    already_approved: bool,
) -> str:
    """Chat reply for /sprite_approve_cast: confirmation + sheet MEDIA tags."""
    if already_approved:
        header = f"*Cast already approved.* {char_count} characters."
        body = "Send `/sprite_show` to check progress."
    else:
        header = f"*Cast approved.* {char_count} characters."
        body = (
            "Generating the timeline in the background; this usually takes "
            "30-90 seconds. Send `/sprite_show` to check progress."
        )
    lines = [header, body]
    for c in characters:
        tag = _media_line(c.get("master_sheet_path"), max_bytes=TELEGRAM_PHOTO_LIMIT)
        if tag:
            lines.append(tag)
    return "\n".join(lines)


def _derive_timeline_status(project: dict, shots: list) -> str:
    """Single-source-of-truth state machine the frontend reads to decide
    whether to poll, render shots, or show a failure message.

    Frontend contract values:
      not_started: project hasn't been advanced to timeline yet
      generating: phase=timeline but shots haven't landed
      ready: phase=timeline (or later) with shots available
      failed: phase=failed (orphan recovery or background-task crash)
      unknown: any state not enumerated above (defensive fallback)
    """
    phase = project.get("phase")
    if phase == "failed":
        return "failed"
    if phase in ("brief", "cast"):
        return "not_started"
    if phase == "timeline":
        return "ready" if shots else "generating"
    if phase in ("render", "done"):
        return "ready"
    return "unknown"


# ---- /sprite_timeline ----

def _format_existing_timeline(project: dict) -> dict:
    project_id = project["id"]
    shots = db.list_shots(project_id)
    formatted = [_shot_to_response_dict(s) for s in shots]
    return {
        "status": "ok",
        "project_id": project_id,
        "title": project.get("title") or "(untitled)",
        "shot_count": len(formatted),
        "total_duration": sum(s["duration_seconds"] for s in formatted),
        "shots": formatted,
        "errors": [],
        "already_generated": True,
    }


async def sprite_timeline_handler(raw_args: str = "", **kwargs) -> str:
    project = db.latest_project_for_user(_USER_ID, phase="timeline")
    if project is None:
        # Failed-orphan retry path: a project that the startup orphan-recovery
        # marked failed (phase='failed', no shots, approved_cast_at set) is
        # eligible for re-running timeline gen. Reset phase to 'timeline' and
        # let the rest of the handler kick off a fresh generation.
        latest = db.latest_project_for_user(_USER_ID)
        if (
            latest is not None
            and latest["phase"] == "failed"
            and latest.get("approved_cast_at")
            and not db.list_shots(latest["id"])
        ):
            db.set_phase(latest["id"], "timeline")
            project = db.get_project(latest["id"]) or latest
        else:
            return _err_json(
                "no project in timeline phase for user 'cli'. "
                "Run /sprite_approve_cast first.",
            )
    project_id = project["id"]

    # Idempotency: if shots already exist, return them without regenerating.
    if db.list_shots(project_id):
        # Re-read project for latest title.
        fresh = db.get_project(project_id) or project
        return json.dumps(_format_existing_timeline(fresh))

    # Guard against the race where the user fires /sprite_timeline manually
    # while the background timeline-gen kicked off by /sprite_approve_cast
    # is still running. Two concurrent advance_to_timeline_phase calls
    # would each try to insert shots and double-charge the LLM.
    if has_background_task(f"timeline_gen_{project_id}"):
        return json.dumps({
            "status": "in_progress",
            "project_id": project_id,
            "phase": project["phase"],
            "timeline_status": "generating",
            "message": (
                "timeline generation already running in the background; "
                "poll /sprite_show for completion"
            ),
        })

    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.advance_to_timeline_phase(
            project_id=project_id,
        )
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project_id)
    except TimelineGenerationFailedError as e:
        return _err_json(
            f"timeline generation failed: {e}",
            project_id=project_id,
            error_class="timeline_failed",
        )
    except ProviderContentPolicyError as e:
        return _err_json(
            f"timeline writer blocked by content policy: "
            f"{e.original_message or e}",
            project_id=project_id,
            error_class="content_policy",
        )
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project_id)
    except SpriteStudioError as e:
        return _err_json(
            f"timeline generation failed: {e}",
            project_id=project_id,
            error_class=e.__class__.__name__,
        )
    except ValueError as e:
        return _err_json(
            f"timeline validation failed: {e}",
            project_id=project_id,
        )

    return json.dumps({
        "status": "ok",
        "project_id": result["project_id"],
        "title": result["title"],
        "shot_count": result["shot_count"],
        "total_duration": result["total_duration"],
        "shots": result["shots"],
        "errors": result["errors"],
        "already_generated": False,
    })


_HELP_EDIT_SHOT = (
    'usage: /sprite_edit_shot "<ordinal_or_id> | <changes>" '
    '(example: /sprite_edit_shot "3 | warm afternoon light, more cheerful")'
)


def _resolve_active_timeline_project() -> tuple[Optional[dict], Optional[str]]:
    """Resolve the user's most-recent project; if it's in 'timeline' phase
    return it, otherwise return an error. Mirrors _resolve_active_cast_project
    so multi-project state stays coherent."""
    latest = db.latest_project_for_user(_USER_ID)
    if latest is None:
        return None, (
            "no project for user 'cli'. "
            'Run /sprite_new "<brief>" first.'
        )
    if latest["phase"] == "timeline":
        return latest, None
    if latest["phase"] == "render":
        return None, (
            f"timeline already approved for project {latest['id']}; "
            f"shot edits are locked. cancel render or wait for completion."
        )
    return None, (
        f"latest project is in phase {latest['phase']!r}; "
        f"cannot perform timeline operations. Run /sprite_timeline first."
    )


def _resolve_shot(project: dict, token: str) -> Optional[dict]:
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        try:
            ordinal = int(token)
        except ValueError:
            return None
        for s in db.list_shots(project["id"]):
            if s["ordinal"] == ordinal:
                return s
        return None
    shot = db.get_shot(token)
    if shot is None or shot["project_id"] != project["id"]:
        return None
    return shot


# ---- /sprite_edit_shot ----

async def sprite_edit_shot_handler(raw_args: str = "", **kwargs) -> str:
    raw = _strip_brief_quotes(raw_args)
    if not raw or "|" not in raw:
        return _err_json(_HELP_EDIT_SHOT)
    ident, changes = raw.split("|", 1)
    ident = ident.strip()
    changes = changes.strip()
    if not ident or not changes:
        return _err_json(_HELP_EDIT_SHOT)

    project, err = _resolve_active_timeline_project()
    if project is None:
        return _err_json(err or "no active timeline project")

    shot = _resolve_shot(project, ident)
    if shot is None:
        shot_count = len(db.list_shots(project["id"]))
        return _err_json(
            f"shot not found in your active project: {ident!r}. "
            f"valid ordinals: 1..{shot_count}.",
            project_id=project["id"],
        )

    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.edit_shot(
            shot_id=shot["id"],
            user_text=changes,
        )
    except ShotNotFoundError as e:
        return _err_json(str(e), project_id=project["id"])
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project["id"])
    except ValueError as e:
        return _err_json(f"invalid edit request: {e}", project_id=project["id"])
    except ProviderContentPolicyError as e:
        return _err_json(
            f"content policy block: {e.original_message or e}",
            project_id=project["id"],
            error_class="content_policy",
        )
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project["id"])
    except SpriteStudioError as e:
        return _err_json(
            f"shot edit failed: {e}",
            project_id=project["id"],
            error_class=e.__class__.__name__,
        )

    return json.dumps({
        "status": "ok",
        "project_id": project["id"],
        "shot_id": result["shot_id"],
        "ordinal": result["ordinal"],
        "fields_changed": result["fields_changed"],
        "reference_still_path": result["reference_still_path"],
        "regenerated": result["regenerated"],
        "regen_error": result.get("regen_error"),
    })


# ---- /sprite_approve_timeline ----

async def sprite_approve_timeline_handler(raw_args: str = "", **kwargs) -> str:
    latest = db.latest_project_for_user(_USER_ID)
    if latest is None:
        return _err_json(
            "no project for user 'cli'. "
            'Run /sprite_new "<brief>" first.',
        )
    if latest["phase"] == "render":
        shots = db.list_shots(latest["id"])
        return json.dumps({
            "status": "ok",
            "project_id": latest["id"],
            "phase": "render",
            "shot_count": len(shots),
            "total_duration": sum(s["duration_seconds"] for s in shots),
            "total_cost_usd_so_far": float(latest.get("total_cost_usd") or 0.0),
            "already_approved": True,
        })
    if latest["phase"] != "timeline":
        return _err_json(
            f"latest project is in phase {latest['phase']!r}; "
            f"cannot approve timeline.",
            project_id=latest["id"],
        )

    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.approve_timeline(project_id=latest["id"])
    except TimelineNotReadyError as e:
        return _err_json(
            str(e),
            project_id=latest["id"],
            error_class="timeline_not_ready",
        )
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=latest["id"])
    except OrchestratorError as e:
        return _err_json(str(e), project_id=latest["id"])

    return json.dumps({
        "status": "ok",
        "project_id": result["project_id"],
        "phase": result["phase"],
        "shot_count": result["shot_count"],
        "total_duration": result["total_duration"],
        "total_cost_usd_so_far": result["total_cost_usd_so_far"],
        "already_approved": False,
    })


# ---- render lifecycle helpers ----

def _resolve_render_target_project() -> tuple[Optional[dict], Optional[str]]:
    """Resolve the user's project for render-lifecycle commands. Returns
    the most recent project regardless of phase; the caller decides
    whether the phase is acceptable.
    """
    latest = db.latest_project_for_user(_USER_ID)
    if latest is None:
        return None, (
            "no project for user 'cli'. "
            'Run /sprite_new "<brief>" first.'
        )
    return latest, None


# ---- /sprite_render ----

async def sprite_render_handler(raw_args: str = "", **kwargs) -> str:
    args = (_strip_brief_quotes(raw_args) or "").strip()
    confirm_budget = "--confirm-budget" in args

    project, err = _resolve_render_target_project()
    if project is None:
        return _err_json(err or "no active project")

    project_id = project["id"]
    phase = project["phase"]

    if phase == "done":
        if _is_chat_surface(_surface(kwargs)):
            return (
                "✅ *Already rendered.*\n"
                "Send `/sprite_show` to receive the video."
            )
        return json.dumps({
            "status": "already_done",
            "project_id": project_id,
            "phase": "done",
            "final_video_path": project.get("final_video_path"),
            "total_cost_usd": float(project.get("total_cost_usd") or 0),
        })

    if phase not in ("render", "failed"):
        return _err_json(
            f"cannot render: project {project_id} is in phase {phase!r}. "
            f"Run /sprite_approve_timeline first.",
            project_id=project_id,
        )

    shots = db.list_shots(project_id)
    if not shots:
        return _err_json(
            "no shots found; cannot render",
            project_id=project_id,
        )

    use_narrator = bool(project.get("use_narrator"))
    cost_est = _estimate_render_cost(shots, use_narrator)
    already_spent = float(project.get("total_cost_usd") or 0)
    projected_total = already_spent + cost_est

    if projected_total > BUDGET_HARD_LIMIT_USD_DEFAULT and not confirm_budget:
        return _err_json(
            f"projected total ${projected_total:.2f} exceeds hard limit "
            f"${BUDGET_HARD_LIMIT_USD_DEFAULT:.2f}. "
            f"Re-run with /sprite_render --confirm-budget to proceed.",
            project_id=project_id,
            error_class="budget_preview_exceeded",
        )

    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.start_render(project_id=project_id)
    except RenderInProgressError as e:
        return _err_json(
            str(e),
            project_id=project_id,
            error_class="render_in_progress",
        )
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project_id)
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project_id)

    surface = _surface(kwargs)
    eta_min = round(_estimate_render_minutes(len(shots)), 1)
    if _is_chat_surface(surface):
        return (
            "🎬 *Render started*\n"
            f"shots: {len(shots)}\n"
            f"estimate: ~{eta_min:g} min, ${cost_est:.2f}\n"
            "\n"
            "`/sprite_status` to check progress\n"
            "`/sprite_show` when done to receive the video"
        )

    return json.dumps({
        "status": "started",
        "project_id": project_id,
        "phase": result.get("phase", "render"),
        "total_shots": len(shots),
        "use_narrator": use_narrator,
        "estimated_minutes": eta_min,
        "estimated_cost_usd": round(cost_est, 4),
        "already_spent_usd": round(already_spent, 4),
    })


# ---- /sprite_cancel ----

async def sprite_cancel_handler(raw_args: str = "", **kwargs) -> str:
    project, err = _resolve_render_target_project()
    if project is None:
        return _err_json(err or "no active project")

    project_id = project["id"]
    orchestrator = _get_orchestrator()

    task = orchestrator.get_render_task(project_id)
    if task is None or task.done():
        return json.dumps({
            "status": "no_active_render",
            "project_id": project_id,
            "phase": project["phase"],
        })

    await orchestrator.cancel_render(project_id)
    return json.dumps({
        "status": "cancelling",
        "project_id": project_id,
        "phase": project["phase"],
        "note": (
            "cancellation flag set. The current shot will finish; "
            "subsequent shots will be skipped. Re-run /sprite_render to resume."
        ),
    })


# ---- /sprite_show ----

async def sprite_show_handler(raw_args: str = "", **kwargs) -> str:
    args = (_strip_brief_quotes(raw_args) or "").strip()

    if args:
        project = db.get_project(args)
        if project is None:
            return _err_json(f"project not found: {args!r}")
        if project.get("user_id") != _USER_ID:
            return _err_json(
                "project does not belong to current user",
                project_id=args,
                error_class="forbidden",
            )
    else:
        project = db.latest_project_for_user(_USER_ID)
        if project is None:
            return _err_json("no project for user")

    project_id = project["id"]
    surface = _surface(kwargs)

    shots = db.list_shots(project_id)
    characters = db.list_characters(project_id)

    if _is_chat_surface(surface):
        return _format_show_for_telegram(project, characters)

    response: dict[str, Any] = {
        "project_id": project_id,
        "phase": project["phase"],
        "title": project.get("title"),
        "brief": project.get("brief"),
        "use_narrator": bool(project.get("use_narrator")),
        "narrator_script": project.get("narrator_script"),
        "total_cost_usd": float(project.get("total_cost_usd") or 0),
        "characters": [
            {
                "id": c["id"],
                "ordinal": c["ordinal"],
                "name": c["name"],
                "role": c.get("role"),
                "persona": c.get("persona"),
                "visual_description": c.get("visual_description"),
                "master_sheet_path": c.get("master_sheet_path"),
                "voice_id": c.get("voice_id"),
                "voice_personality": c.get("voice_personality"),
                "source": c.get("source"),
                "reference_image_path": c.get("reference_image_path"),
                "is_approved": int(c.get("is_approved") or 0),
                "updated_at": c.get("updated_at"),
            }
            for c in characters
        ],
        "shots": [_shot_to_response_dict(s) for s in shots],
        "final_video_path": project.get("final_video_path"),
        "music_track_path": project.get("music_track_path"),
        "error_message": project.get("error_message"),
        "timeline_status": _derive_timeline_status(project, shots),
    }

    final = project.get("final_video_path")
    if final and Path(final).exists():
        size = Path(final).stat().st_size
        response["final_video_size_bytes"] = size
        response["xdg_open_hint"] = f'xdg-open "{final}"'

    return json.dumps(response)


def _format_show_for_telegram(project: dict, characters: list[dict]) -> str:
    """Build a chat reply for /sprite_show: text summary + MEDIA: lines.

    Final video MEDIA: line precedes per-character sheet MEDIA: lines so
    the most-important asset arrives first in the chat.  Files exceeding
    the Telegram size limit are reported as a path note instead of
    silently dropped.
    """
    lines: list[str] = [f"*{project.get('title') or '(untitled)'}*"]
    lines.append(f"phase: {project['phase']}")
    lines.append(f"cost: ${float(project.get('total_cost_usd') or 0):.4f}")
    if project.get("error_message"):
        lines.append(f"⚠️ {project['error_message']}")
    lines.append("")

    if characters:
        lines.append(f"*Cast ({len(characters)})*")
        for c in characters:
            lines.append(f"{c['ordinal']}. {c['name']} — {c.get('role') or '?'}")
        lines.append("")

    final = project.get("final_video_path")
    if final:
        tag = _media_line(final, max_bytes=TELEGRAM_VIDEO_LIMIT)
        if tag:
            lines.append(tag)
        else:
            fp = Path(final)
            if fp.exists():
                size_mb = fp.stat().st_size / 1024 / 1024
                lines.append(
                    f"_final video {size_mb:.1f} MB exceeds Telegram 50 MB limit._"
                )
                lines.append(f"`{final}`")
            else:
                lines.append("_final video is missing on disk._")

    for c in characters:
        tag = _media_line(c.get("master_sheet_path"), max_bytes=TELEGRAM_PHOTO_LIMIT)
        if tag:
            lines.append(tag)

    return "\n".join(lines).rstrip()


# ---- /sprite_purge ----

async def sprite_purge_handler(raw_args: str = "", **kwargs) -> str:
    args = (_strip_brief_quotes(raw_args) or "").strip()

    keep_final = "--keep-final-video" in args
    confirm = "--confirm" in args

    pid_arg = (
        args.replace("--keep-final-video", "")
            .replace("--confirm", "")
            .strip()
    )

    if pid_arg:
        project = db.get_project(pid_arg)
        if project is None:
            return _err_json(f"project not found: {pid_arg!r}")
        if project.get("user_id") != _USER_ID:
            return _err_json(
                "project does not belong to current user",
                project_id=pid_arg,
                error_class="forbidden",
            )
    else:
        project = db.latest_project_for_user(_USER_ID)
        if project is None:
            return _err_json("no project for user")

    project_id = project["id"]

    if not confirm:
        note = (
            "this will permanently delete project, characters, shots, jobs, "
            "and filesystem artifacts. Re-run with --confirm to proceed."
        )
        if not keep_final:
            note += " --keep-final-video preserves the final MP4."
        return json.dumps({
            "status": "confirmation_required",
            "project_id": project_id,
            "phase": project["phase"],
            "note": note,
        })

    orch = _get_orchestrator()
    task = orch.get_render_task(project_id)
    if task is not None and not task.done():
        return _err_json(
            "render is in flight; /sprite_cancel first then re-run /sprite_purge",
            project_id=project_id,
            error_class="render_in_progress",
        )

    result = db.delete_project(project_id, keep_final_video=keep_final)
    return json.dumps({"status": "purged", **result})


# ---- /sprite_list ----

async def sprite_list_handler(raw_args: str = "", **kwargs) -> str:
    args = (_strip_brief_quotes(raw_args) or "").strip()
    phase: Optional[str] = None
    limit = 20

    tokens = args.split()
    i = 0
    while i < len(tokens):
        if tokens[i] == "--phase" and i + 1 < len(tokens):
            phase = tokens[i + 1]
            i += 2
        elif tokens[i] == "--limit" and i + 1 < len(tokens):
            try:
                limit = max(1, min(100, int(tokens[i + 1])))
            except ValueError:
                pass
            i += 2
        else:
            i += 1

    projects = db.list_projects(
        user_id=_USER_ID, limit=limit, phase=phase, with_thumbnail=True,
    )
    return json.dumps({
        "count": len(projects),
        "phase_filter": phase,
        "projects": [
            {
                "id": p["id"],
                "title": p.get("title") or "(untitled)",
                "phase": p["phase"],
                "brief": (p.get("brief") or "")[:80],
                "total_cost_usd": float(p.get("total_cost_usd") or 0),
                "updated_at": p["updated_at"],
                "final_video_path": p.get("final_video_path"),
                "thumb_path": p.get("thumb_path"),
            }
            for p in projects
        ],
    })


# ---- /sprite_cost_summary ----

async def sprite_cost_summary_handler(raw_args: str = "", **kwargs) -> str:
    args = (_strip_brief_quotes(raw_args) or "").strip()
    days = 30

    tokens = args.split()
    i = 0
    while i < len(tokens):
        if tokens[i] == "--days" and i + 1 < len(tokens):
            try:
                days = max(1, min(365, int(tokens[i + 1])))
            except ValueError:
                pass
            i += 2
        else:
            i += 1

    cutoff = int(_time.time()) - days * 86400
    summary = db.sum_costs(user_id=_USER_ID, since_ts=cutoff)

    return json.dumps({
        "user_id": _USER_ID,
        "window_days": days,
        "total_usd": round(summary["total_usd"], 4),
        "project_count": summary["project_count"],
        "by_phase": summary["by_phase"],
    })


# ---- web-canvas helpers (P15) ----

# Style/vibe/duration are project-shape decisions. Locking them once a
# render is running protects the cost meter and the artifacts already on
# disk; allowing edits in 'brief'/'cast'/'timeline' lets the canvas tweak
# the project without a /sprite_revert dance.
_FIELD_EDITABLE_PHASES = {"brief", "cast", "timeline"}
_REORDER_EDITABLE_PHASES = {"cast", "timeline"}
_VALID_DURATIONS = (15, 30, 45, 60, 75, 90)


def _load_style_presets() -> tuple[Optional[list[dict]], Optional[str]]:
    presets_path = Path(__file__).resolve().parent / "style_presets.yaml"
    if not presets_path.exists():
        return None, "style_presets.yaml not found"
    try:
        with presets_path.open("r", encoding="utf-8") as f:
            data = _yaml.safe_load(f) or []
    except _yaml.YAMLError as e:
        return None, f"failed to parse style_presets.yaml: {e}"
    if not isinstance(data, list):
        return None, "style_presets.yaml must be a list"
    return data, None


# ---- /sprite_list_styles ----

async def sprite_list_styles_handler(raw_args: str = "", **kwargs) -> str:
    presets, err = _load_style_presets()
    if presets is None:
        return _err_json(err or "load failed", error_class="missing_file")
    return json.dumps({"presets": presets, "count": len(presets)})


# ---- /sprite_set_style ----

async def sprite_set_style_handler(raw_args: str = "", **kwargs) -> str:
    preset_id = (_strip_brief_quotes(raw_args) or "").strip()
    if not preset_id:
        return _err_json('usage: /sprite_set_style "<preset_id>"')

    presets, err = _load_style_presets()
    if presets is None:
        return _err_json(err or "load failed", error_class="missing_file")
    valid_ids = {p["id"] for p in presets if isinstance(p, dict) and "id" in p}
    if preset_id not in valid_ids:
        return _err_json(
            f"unknown style preset {preset_id!r}",
            error_class="unknown_preset",
            valid=sorted(valid_ids),
        )

    project, err = _resolve_render_target_project()
    if project is None:
        return _err_json(err or "no active project")

    result = db.update_project_fields(
        project["id"],
        allowed_phases=_FIELD_EDITABLE_PHASES,
        style_preset_id=preset_id,
    )
    return json.dumps({
        "command": "sprite_set_style",
        "project_id": project["id"],
        **result,
    })


# ---- /sprite_set_vibe ----

async def sprite_set_vibe_handler(raw_args: str = "", **kwargs) -> str:
    vibe = (_strip_brief_quotes(raw_args) or "").strip()
    if not vibe:
        return _err_json('usage: /sprite_set_vibe "<vibe>"')
    if len(vibe) > 100:
        return _err_json(
            f"vibe must be ≤100 chars (got {len(vibe)})",
            error_class="invalid_args",
        )

    project, err = _resolve_render_target_project()
    if project is None:
        return _err_json(err or "no active project")

    result = db.update_project_fields(
        project["id"],
        allowed_phases=_FIELD_EDITABLE_PHASES,
        vibe=vibe,
    )
    return json.dumps({
        "command": "sprite_set_vibe",
        "project_id": project["id"],
        **result,
    })


# ---- /sprite_set_duration ----

async def sprite_set_duration_handler(raw_args: str = "", **kwargs) -> str:
    raw = (_strip_brief_quotes(raw_args) or "").strip()
    if not raw:
        return _err_json(
            'usage: /sprite_set_duration <seconds>',
            error_class="invalid_args",
        )
    try:
        duration = int(raw)
    except ValueError:
        return _err_json(
            f"duration must be an integer, got {raw!r}",
            error_class="invalid_args",
        )
    if duration not in _VALID_DURATIONS:
        return _err_json(
            f"duration must be one of "
            f"{'/'.join(str(d) for d in _VALID_DURATIONS)}, got {duration}",
            error_class="invalid_args",
            valid=list(_VALID_DURATIONS),
        )

    project, err = _resolve_render_target_project()
    if project is None:
        return _err_json(err or "no active project")

    result = db.update_project_fields(
        project["id"],
        allowed_phases=_FIELD_EDITABLE_PHASES,
        duration_seconds=duration,
    )
    return json.dumps({
        "command": "sprite_set_duration",
        "project_id": project["id"],
        **result,
    })


# ---- /sprite_reorder_cast ----

async def sprite_reorder_cast_handler(raw_args: str = "", **kwargs) -> str:
    raw = (_strip_brief_quotes(raw_args) or "").strip()
    if not raw:
        return _err_json(
            'usage: /sprite_reorder_cast "<id1>,<id2>,..."',
            error_class="invalid_args",
        )

    char_ids = [s.strip() for s in raw.split(",") if s.strip()]
    if not char_ids:
        return _err_json(
            "no character ids supplied",
            error_class="invalid_args",
        )

    project, err = _resolve_render_target_project()
    if project is None:
        return _err_json(err or "no active project")

    if project["phase"] not in _REORDER_EDITABLE_PHASES:
        return _err_json(
            f"cannot reorder cast in phase {project['phase']!r}",
            error_class="phase_locked",
            project_id=project["id"],
            allowed=sorted(_REORDER_EDITABLE_PHASES),
        )

    result = db.reorder_characters(project["id"], char_ids)
    return json.dumps({
        "command": "sprite_reorder_cast",
        "project_id": project["id"],
        **result,
    })


# ---- /sprite_reorder_shots ----

# Shot reorder is only safe in 'timeline' phase. In 'render' phase the
# RenderWorker iterates shots by ordinal and a mid-flight reorder would
# rendezvous shots with the wrong rendered_video_path.
_TIMELINE_EDITABLE_PHASES = {"timeline"}

# Visual fields force a reference-still regen because the rendered shot
# inherits the still as its first frame. Non-visual fields (duration,
# narration) can be tweaked without rebuilding the still.
_VISUAL_SHOT_FIELDS = {"setting", "action", "camera"}


async def sprite_reorder_shots_handler(raw_args: str = "", **kwargs) -> str:
    raw = (_strip_brief_quotes(raw_args) or "").strip()
    if not raw:
        return _err_json(
            'usage: /sprite_reorder_shots "<id1>,<id2>,..."',
            error_class="invalid_args",
        )

    shot_ids = [s.strip() for s in raw.split(",") if s.strip()]
    if not shot_ids:
        return _err_json(
            "no shot ids supplied",
            error_class="invalid_args",
        )

    project, err = _resolve_render_target_project()
    if project is None:
        return _err_json(err or "no active project")

    if project["phase"] not in _TIMELINE_EDITABLE_PHASES:
        return _err_json(
            f"cannot reorder shots in phase {project['phase']!r}",
            error_class="phase_locked",
            project_id=project["id"],
            allowed=sorted(_TIMELINE_EDITABLE_PHASES),
        )

    result = db.reorder_shots(project["id"], shot_ids)
    return json.dumps({
        "command": "sprite_reorder_shots",
        "project_id": project["id"],
        **result,
    })


# ---- /sprite_edit_shot_field ----

_HELP_EDIT_SHOT_FIELD = (
    'usage: /sprite_edit_shot_field "<ordinal_or_id> | <field>=<value>" '
    '(example: /sprite_edit_shot_field "3 | duration_seconds=8")'
)

# Mirrors the schema's CHECK (duration_seconds BETWEEN 5 AND 15).
_DURATION_MIN = 5
_DURATION_MAX = 15


async def sprite_edit_shot_field_handler(raw_args: str = "", **kwargs) -> str:
    """Surgical single-field shot edit. Bypasses the LLM shot_edit
    translator. Visual-field changes (setting/action/camera) trigger a
    reference-still regeneration; other fields just write the column.
    """
    raw = (_strip_brief_quotes(raw_args) or "").strip()
    if "|" not in raw:
        return _err_json(_HELP_EDIT_SHOT_FIELD)

    target, kv = raw.split("|", 1)
    target = target.strip()
    kv = kv.strip()
    if not target or "=" not in kv:
        return _err_json(_HELP_EDIT_SHOT_FIELD)

    field, value = kv.split("=", 1)
    field = field.strip()
    value = value.strip()
    if not field:
        return _err_json(_HELP_EDIT_SHOT_FIELD)

    project, err = _resolve_render_target_project()
    if project is None:
        return _err_json(err or "no active project")

    shot = _resolve_shot(project, target)
    if shot is None:
        shot_count = len(db.list_shots(project["id"]))
        return _err_json(
            f"shot not found in your active project: {target!r}. "
            f"valid ordinals: 1..{shot_count}.",
            project_id=project["id"],
        )

    # Coerce duration_seconds to int; everything else stays str.
    typed_value: Any = value
    if field == "duration_seconds":
        try:
            typed_value = int(value)
        except ValueError:
            return _err_json(
                f"duration_seconds must be int, got {value!r}",
                error_class="invalid_args",
                project_id=project["id"],
            )
        if not (_DURATION_MIN <= typed_value <= _DURATION_MAX):
            return _err_json(
                f"duration_seconds must be {_DURATION_MIN}-{_DURATION_MAX} "
                f"(Seedance limits)",
                error_class="invalid_args",
                project_id=project["id"],
            )

    result = db.update_shot_fields(
        shot["id"],
        allowed_phases=_TIMELINE_EDITABLE_PHASES,
        **{field: typed_value},
    )

    regenerated = False
    regen_error: Optional[str] = None
    if result.get("updated") and field in _VISUAL_SHOT_FIELDS:
        try:
            orchestrator = _get_orchestrator()
            await orchestrator.regenerate_shot_reference(shot["id"])
            regenerated = True
        except ProviderContentPolicyError as e:
            regen_error = f"content_policy: {e.original_message or e}"
            logger.warning(
                "edit_shot_field regen blocked shot=%s: %s",
                shot["id"], regen_error,
            )
        except (OrchestratorError, SpriteStudioError) as e:
            regen_error = str(e)
            logger.warning(
                "edit_shot_field regen failed shot=%s: %s",
                shot["id"], regen_error,
            )

    fresh = db.get_shot(shot["id"]) or shot
    return json.dumps({
        "command": "sprite_edit_shot_field",
        "project_id": project["id"],
        "shot_id": shot["id"],
        "ordinal": shot["ordinal"],
        "regenerated_reference": regenerated,
        "regen_error": regen_error,
        "reference_still_path": fresh.get("reference_still_path"),
        **result,
    })


# ---- /sprite_set_shot_transition ----

_HELP_SET_SHOT_TRANSITION = (
    'usage: /sprite_set_shot_transition "<ordinal_or_id> | <kind>" '
    f'(kind ∈ {sorted(db.VALID_SHOT_TRANSITIONS)})'
)


async def sprite_set_shot_transition_handler(raw_args: str = "", **kwargs) -> str:
    """Set the transition INTO the next shot.

    Stored on shot N as transition_to_next; ffmpeg stitch_final consumes it
    to apply xfade between shot N and shot N+1 ('cut'/'match_cut' render
    as hard cuts). The last shot's value is structurally ignored at render
    time. Phase-locked to 'timeline' because the render worker reads
    shots once at start.
    """
    raw = (_strip_brief_quotes(raw_args) or "").strip()
    if "|" not in raw:
        return _err_json(_HELP_SET_SHOT_TRANSITION)

    target, kind = raw.split("|", 1)
    target = target.strip()
    kind = kind.strip().lower()
    if not target or not kind:
        return _err_json(_HELP_SET_SHOT_TRANSITION)

    if kind not in db.VALID_SHOT_TRANSITIONS:
        return _err_json(
            f"transition must be one of {sorted(db.VALID_SHOT_TRANSITIONS)}, "
            f"got {kind!r}",
            error_class="invalid_args",
        )

    project, err = _resolve_render_target_project()
    if project is None:
        return _err_json(err or "no active project")

    shot = _resolve_shot(project, target)
    if shot is None:
        shot_count = len(db.list_shots(project["id"]))
        return _err_json(
            f"shot not found in your active project: {target!r}. "
            f"valid ordinals: 1..{shot_count}.",
            project_id=project["id"],
        )

    result = db.update_shot_fields(
        shot["id"],
        allowed_phases=_TIMELINE_EDITABLE_PHASES,
        transition_to_next=kind,
    )
    return json.dumps({
        "command": "sprite_set_shot_transition",
        "project_id": project["id"],
        "shot_id": shot["id"],
        "ordinal": shot["ordinal"],
        **result,
    })


# ---- /sprite_add_shot ----

_HELP_ADD_SHOT = (
    'usage: /sprite_add_shot "<ordinal> | <action>" or '
    '"<ordinal> | <action> | duration=8, camera=static wide, '
    'characters=1+2+3, emotion=tense, narration_line=..."'
)
_ADD_SHOT_VALID_KEYS = {
    "duration", "camera", "emotion", "narration_line", "characters",
}

# Split kv pairs on commas that precede `\w+=` so a comma inside the
# `characters=` value does not split mid-list. (The `+` separator is
# canonical for character ids/ordinals; the regex is defensive against
# users who type commas instead.)
_KV_SEP_RE = re.compile(r",\s*(?=\w+=)")


def _resolve_character_token(project_id: str, token: str) -> Optional[str]:
    """Resolve a single ordinal-or-ULID token to a character id within
    the project. Returns None if not found.
    """
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        try:
            ord_num = int(token)
        except ValueError:
            return None
        for c in db.list_characters(project_id):
            if c["ordinal"] == ord_num:
                return c["id"]
        return None
    char = db.get_character(token)
    if char is None or char["project_id"] != project_id:
        return None
    return token


async def sprite_add_shot_handler(raw_args: str = "", **kwargs) -> str:
    """Insert a new shot at the requested ordinal.

    Phase-locked to 'timeline'. Active project resolved via
    _resolve_active_timeline_project (mirrors /sprite_edit_shot_field).
    """
    raw = (_strip_brief_quotes(raw_args) or "").strip()
    if not raw:
        return _err_json(_HELP_ADD_SHOT, error_class="invalid_args")
    if "|" not in raw:
        return _err_json(_HELP_ADD_SHOT, error_class="invalid_args")

    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2 or len(parts) > 3 or not parts[0] or not parts[1]:
        return _err_json(_HELP_ADD_SHOT, error_class="invalid_args")

    ordinal_str = parts[0]
    action = parts[1]
    kv_str = parts[2] if len(parts) == 3 else ""

    try:
        ordinal = int(ordinal_str)
    except ValueError:
        return _err_json(
            f"ordinal must be an integer (got {ordinal_str!r})",
            error_class="invalid_args",
        )

    project, err = _resolve_active_timeline_project()
    if project is None:
        return _err_json(err or "no active timeline project")
    project_id = project["id"]

    # Parse optional kv pairs.
    kv: dict[str, str] = {}
    if kv_str:
        for chunk in _KV_SEP_RE.split(kv_str):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                return _err_json(
                    f"malformed kv pair {chunk!r}: missing '='",
                    error_class="invalid_args",
                    project_id=project_id,
                )
            k, v = chunk.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k not in _ADD_SHOT_VALID_KEYS:
                return _err_json(
                    f"unknown kv key {k!r}; valid keys: "
                    f"{sorted(_ADD_SHOT_VALID_KEYS)}",
                    error_class="invalid_args",
                    project_id=project_id,
                )
            kv[k] = v

    duration_seconds = 8
    if "duration" in kv:
        try:
            duration_seconds = int(kv["duration"])
        except ValueError:
            return _err_json(
                f"duration must be int, got {kv['duration']!r}",
                error_class="invalid_args",
                project_id=project_id,
            )

    camera = kv.get("camera") or None
    emotion = kv.get("emotion") or None
    narration_line = kv.get("narration_line") or None

    # Resolve characters: split on '+' OR ',' (both supported); '+' is
    # canonical because the outer kv parser uses ',' as separator.
    characters_present: list[str] = []
    if "characters" in kv:
        chars_raw = kv["characters"]
        for sep in ("+", ","):
            if sep in chars_raw:
                tokens = [t.strip() for t in chars_raw.split(sep) if t.strip()]
                break
        else:
            tokens = [chars_raw.strip()] if chars_raw.strip() else []
        for tok in tokens:
            cid = _resolve_character_token(project_id, tok)
            if cid is None:
                return _err_json(
                    f"character not found in this project: {tok!r}",
                    error_class="invalid_args",
                    project_id=project_id,
                )
            characters_present.append(cid)

    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.add_shot(
            project_id=project_id,
            ordinal=ordinal,
            action=action,
            duration_seconds=duration_seconds,
            characters_present=characters_present,
            camera=camera,
            emotion=emotion,
            narration_line=narration_line,
        )
    except TimelineFullError as e:
        return _err_json(
            str(e), error_class="timeline_full", project_id=project_id,
        )
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project_id)
    except ValueError as e:
        return _err_json(
            f"invalid args: {e}",
            error_class="invalid_args",
            project_id=project_id,
        )
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project_id)
    except SpriteStudioError as e:
        return _err_json(
            f"add_shot failed: {e}",
            project_id=project_id,
            error_class=e.__class__.__name__,
        )

    surface = _surface(kwargs)
    if _is_chat_surface(surface):
        lines = [
            f"*Shot {result['ordinal']} added.*",
            f"action: {action[:120]}",
        ]
        if result.get("regen_error"):
            lines.append(f"_reference still: {result['regen_error']}_")
        ref_path = result.get("reference_still_path")
        if ref_path:
            tag = _media_line(ref_path, max_bytes=TELEGRAM_PHOTO_LIMIT)
            if tag:
                lines.append(tag)
        return "\n".join(lines)

    return json.dumps({
        "status": "ok",
        "command": "sprite_add_shot",
        "project_id": project_id,
        "shot_id": result["shot_id"],
        "ordinal": result["ordinal"],
        "reference_generated": result["reference_generated"],
        "reference_still_path": result.get("reference_still_path"),
        "regen_error": result.get("regen_error"),
    })


# ---- /sprite_delete_shot ----

_HELP_DELETE_SHOT = (
    'usage: /sprite_delete_shot "<ordinal_or_id>" '
    '(timeline phase only; refuses to delete the last shot)'
)


async def sprite_delete_shot_handler(raw_args: str = "", **kwargs) -> str:
    """Delete a shot from the active timeline project."""
    ident = (_strip_brief_quotes(raw_args) or "").strip()
    if not ident:
        return _err_json(_HELP_DELETE_SHOT, error_class="invalid_args")

    project, err = _resolve_active_timeline_project()
    if project is None:
        return _err_json(err or "no active timeline project")
    project_id = project["id"]

    shot = _resolve_shot(project, ident)
    if shot is None:
        shot_count = len(db.list_shots(project_id))
        return _err_json(
            f"shot not found in your active project: {ident!r}. "
            f"valid ordinals: 1..{shot_count}.",
            project_id=project_id,
            error_class="not_found",
        )

    orchestrator = _get_orchestrator()
    try:
        result = await orchestrator.remove_shot(shot_id=shot["id"])
    except ShotNotFoundError as e:
        return _err_json(
            str(e), error_class="not_found", project_id=project_id,
        )
    except TimelineLastShotError as e:
        return _err_json(
            str(e), error_class="timeline_last_shot",
            project_id=project_id,
        )
    except ProjectInWrongPhaseError as e:
        return _err_json(str(e), project_id=project_id)
    except OrchestratorError as e:
        return _err_json(str(e), project_id=project_id)

    surface = _surface(kwargs)
    if _is_chat_surface(surface):
        return (
            f"*Shot {result['ordinal_was']} removed.* "
            f"{result['shots_remaining']} shot(s) remaining."
        )

    return json.dumps({
        "status": "ok",
        "command": "sprite_delete_shot",
        "project_id": project_id,
        "deleted_shot_id": result["deleted_shot_id"],
        "ordinal_was": result["ordinal_was"],
        "shots_remaining": result["shots_remaining"],
    })


async def sprite_set_project_refs_handler(raw_args: str = "", **kwargs) -> str:
    """Record uploaded reference image paths against the latest brief-phase
    project for the current user.

    Args: comma-separated asset-server paths, e.g.
      /sprite_set_project_refs /<pid>/refs/<ulid>.png,/<pid>/refs/<ulid>.jpg

    Each path must live under the active project's refs/ directory and pass
    the same traversal guards as the upload endpoint. The paths are stored
    as a JSON array on projects.ref_image_paths and read back by
    advance_to_cast_phase + the per-character sheet generator so the cast
    is visually anchored to whatever the user dropped in.
    """
    raw = (raw_args or "").strip()
    if not raw:
        return _err_json(
            "usage: /sprite_set_project_refs path1,path2,...",
        )

    project = db.latest_project_for_user(_USER_ID, phase="brief")
    if project is None:
        return _err_json(
            "no project in brief phase for user 'cli'. "
            'Run /sprite_new "<brief>" defer_cast=true first.',
        )

    paths, err = _parse_refs_kv(raw)
    if err:
        return _err_json(err, project_id=project["id"])

    project_id = project["id"]
    expected_prefix = f"/{project_id}/refs/"
    refs_dir = (
        Path("~/.hermes/plugins/sprite-studio/projects").expanduser()
        / project_id / "refs"
    )

    for p in paths:
        if not p.startswith(expected_prefix):
            return _err_json(
                f"ref path not under active project: {p!r}",
                project_id=project_id,
            )
        # Defense in depth: confirm the file exists on disk before
        # binding it to the project. Catches a typo'd path or an
        # interrupted upload.
        rel = p[len(expected_prefix):]
        on_disk = refs_dir / rel
        if not on_disk.is_file():
            return _err_json(
                f"ref file missing on disk: {p!r}",
                project_id=project_id,
            )

    db.update_project(project_id, ref_image_paths=paths)
    logger.info(
        "project refs set project=%s count=%d",
        project_id, len(paths),
    )
    return json.dumps({
        "status": "ok",
        "project_id": project_id,
        "refs": paths,
        "count": len(paths),
    })


SLASH_COMMANDS: dict[str, dict] = {
    "start": {
        "description": "Welcome / quick-start guide (Telegram convention)",
        "handler": start_handler,
    },
    "sprite_status": {
        "description": "Show plugin status and the latest project's render progress",
        "handler": sprite_status_handler,
    },
    "sprite_new": {
        "description": "Start a new project from a brief",
        "handler": sprite_new_handler,
    },
    "sprite_cast": {
        "description": "Generate the character cast for the current project",
        "handler": sprite_cast_handler,
    },
    "sprite_edit_character": {
        "description": "Edit a character: /sprite_edit_character \"<ord_or_id> | <changes>\"",
        "handler": sprite_edit_character_handler,
    },
    "sprite_add_character": {
        "description": "Add a new character to the cast",
        "handler": sprite_add_character_handler,
    },
    "sprite_remove_character": {
        "description": "Remove a character from the cast",
        "handler": sprite_remove_character_handler,
    },
    "sprite_approve_cast": {
        "description": "Approve the cast and advance to timeline planning",
        "handler": sprite_approve_cast_handler,
    },
    "sprite_approve_cast_size": {
        "description": (
            "Confirm a large cast (>12 characters) before /sprite_cast "
            "spends image-gen budget on it"
        ),
        "handler": sprite_approve_cast_size_handler,
    },
    "sprite_timeline": {
        "description": "Generate the shot timeline for the current project",
        "handler": sprite_timeline_handler,
    },
    "sprite_edit_shot": {
        "description": "Edit a shot: /sprite_edit_shot \"<ord_or_id> | <changes>\"",
        "handler": sprite_edit_shot_handler,
    },
    "sprite_approve_timeline": {
        "description": "Approve the timeline and advance to render",
        "handler": sprite_approve_timeline_handler,
    },
    "sprite_render": {
        "description": "Start render. Use --confirm-budget to override budget check.",
        "handler": sprite_render_handler,
    },
    "sprite_cancel": {
        "description": "Cancel an in-flight render (cooperative; finishes current shot)",
        "handler": sprite_cancel_handler,
    },
    "sprite_show": {
        "description": "Show full project state (characters, shots, final video path)",
        "handler": sprite_show_handler,
    },
    "sprite_purge": {
        "description": "Delete a project and all its data. Use --confirm to proceed.",
        "handler": sprite_purge_handler,
    },
    "sprite_list": {
        "description": "List recent projects. --phase <phase> --limit <n>",
        "handler": sprite_list_handler,
    },
    "sprite_cost_summary": {
        "description": "Sum project costs in the last N days (--days, default 30)",
        "handler": sprite_cost_summary_handler,
    },
    "sprite_list_styles": {
        "description": "Return style_presets.yaml entries as JSON",
        "handler": sprite_list_styles_handler,
    },
    "sprite_set_style": {
        "description": "Set the project's style preset (brief/cast/timeline phases only)",
        "handler": sprite_set_style_handler,
    },
    "sprite_set_vibe": {
        "description": "Set the project's vibe (brief/cast/timeline phases only)",
        "handler": sprite_set_vibe_handler,
    },
    "sprite_set_duration": {
        "description": "Set the target duration in seconds (15/30/45/60/75/90)",
        "handler": sprite_set_duration_handler,
    },
    "sprite_reorder_cast": {
        "description": "Reorder cast characters by id list (cast/timeline phases only)",
        "handler": sprite_reorder_cast_handler,
    },
    "sprite_reorder_shots": {
        "description": "Reorder shots by id list (timeline phase only)",
        "handler": sprite_reorder_shots_handler,
    },
    "sprite_edit_shot_field": {
        "description": "Edit a single shot field; visual fields trigger reference-still regen",
        "handler": sprite_edit_shot_field_handler,
    },
    "sprite_set_shot_transition": {
        "description": "Set how a shot transitions into the next (cut/fade/dissolve/match_cut)",
        "handler": sprite_set_shot_transition_handler,
    },
    "sprite_add_shot": {
        "description": "Insert a new shot at the given ordinal (timeline phase only)",
        "handler": sprite_add_shot_handler,
    },
    "sprite_delete_shot": {
        "description": "Delete a shot by ordinal or id (timeline phase only)",
        "handler": sprite_delete_shot_handler,
    },
    "sprite_set_project_refs": {
        "description": "Bind uploaded ref image paths to the active brief-phase project",
        "handler": sprite_set_project_refs_handler,
    },
}

"""Sprite Studio plugin — registration entry point."""
from __future__ import annotations

import logging
from pathlib import Path

from . import commands, db, env  # noqa: F401  (db import triggers schema migration)
from . import style_presets  # noqa: F401  (eager-load + validate presets)
from .orchestrator import PROJECTS_ROOT
from .services import elevenlabs_voices


logger = logging.getLogger("sprite_studio")


def _backfill_project_dirs(projects_root: Path) -> int:
    """Create on-disk project root directories for projects whose DB row
    pre-dates the start_project mkdir from P19a-13. Idempotent: skips
    dirs that already exist. Returns the count of dirs created.
    """
    created = 0
    for pid in db.list_all_project_ids():
        project_dir = projects_root / pid
        if not project_dir.exists():
            try:
                project_dir.mkdir(parents=True, exist_ok=True)
                created += 1
            except OSError:
                logger.exception("backfill mkdir failed for %s", pid)
    return created


def register(ctx) -> None:
    try:
        env_status = env.check_required_env(["TOKENROUTER_API_KEY", "ELEVENLABS_API_KEY"])
        if not all(env_status.values()):
            missing = [k for k, v in env_status.items() if not v]
            logger.warning("sprite-studio: missing env vars at register time: %s", missing)

        # Best-effort, non-blocking. Failures inside the helper are logged
        # at warning and pick_voice() falls back to the default voice id.
        try:
            elevenlabs_voices.schedule_initial_load()
        except Exception:
            logger.exception("sprite-studio: voice catalog scheduling raised")

        # Recover orphans from a previous process that crashed during
        # background timeline generation: phase='timeline' with no shots
        # and approved_cast_at older than the threshold.
        try:
            orphans = db.recover_orphan_timeline_jobs()
            if orphans:
                logger.warning(
                    "sprite-studio: recovered %d orphan timeline jobs: %s",
                    len(orphans), orphans,
                )
        except Exception:
            logger.exception("sprite-studio: orphan recovery raised")

        # Backfill on-disk project dirs for rows created before P19a-13's
        # start_project mkdir. Without this, pre-fix projects 404 on
        # /<pid>/refs/upload because asset_server's project_dir.is_dir()
        # check fails. Idempotent and isolated from registration failure.
        try:
            backfilled = _backfill_project_dirs(PROJECTS_ROOT)
            if backfilled:
                logger.info(
                    "sprite-studio: backfilled %d project dirs", backfilled,
                )
        except Exception:
            logger.exception("sprite-studio: project-dir backfill raised")

        for name, meta in commands.SLASH_COMMANDS.items():
            ctx.register_command(name, meta["handler"], description=meta["description"])
            logger.debug("registered slash command: /%s", name)

        logger.info(
            "sprite-studio plugin registered: %d commands", len(commands.SLASH_COMMANDS)
        )
    except Exception:
        logger.exception("sprite-studio: register() failed; plugin will be skipped")

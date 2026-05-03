"""SQLite persistence layer for the sprite-studio plugin."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

try:
    from ulid import ULID

    def _new_ulid() -> str:
        return str(ULID())
except Exception:
    import secrets

    def _new_ulid() -> str:
        return f"{time.time_ns():016x}{secrets.token_hex(5)}".upper()


# Crockford base32: 0-9 plus A-Z minus I, L, O, U. ULIDs are exactly 26 chars.
# Used as the path-traversal guard wherever a project_id flows into an FS or
# destructive DB operation: rejecting "../etc/passwd" or "01KQ/..." keeps
# delete_project_cascade and the bridge DELETE route from touching anything
# outside the projects/<ulid>/ tree.
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _is_valid_ulid(s: Any) -> bool:
    return isinstance(s, str) and bool(_ULID_RE.match(s))


logger = logging.getLogger("sprite_studio.db")

DB_PATH = Path("~/.hermes/plugins/sprite-studio/state.db").expanduser()
SCHEMA_VERSION = 6

VALID_SHOT_TRANSITIONS = ("cut", "fade", "dissolve", "match_cut")

# Two-pass UNIQUE-collision-safe ordinal updates park rows here mid-write.
# Used by create_shot_at_ordinal and delete_shot below; reorder_shots /
# reorder_characters predate the constant and use a local 100000.
# Production max ordinal is TIMELINE_MAX_SHOTS=12 (cast cap is
# models.MAX_CAST_SIZE=30), so 100_000 is safely above any real ordinal
# while still satisfying CHECK(ordinal >= 1).
_ORDINAL_PARK_OFFSET = 100_000


class DBError(RuntimeError):
    """Base class for db-layer typed errors raised to the orchestrator."""


class ProjectNotFoundError(DBError):
    pass


class WrongPhaseError(DBError):
    pass


class LastShotError(DBError):
    pass


class ShotNotFoundError(DBError):
    pass


_PROJECT_COLUMNS = {
    "user_id", "surface", "brief", "style_preset_id", "vibe",
    "duration_seconds", "phase", "title", "narrator_script",
    "use_narrator",
    "music_track_path", "final_video_path", "total_cost_usd",
    "approved_cast_at", "approved_timeline_at", "rendered_at",
    "error_message", "ref_image_paths", "cast_size_confirmed",
}
_CHARACTER_COLUMNS = {
    "ordinal", "name", "role", "persona", "visual_description",
    "master_sheet_path", "voice_id", "voice_personality", "source",
    "reference_image_path", "edit_history", "is_approved",
}
_SHOT_COLUMNS = {
    "ordinal", "duration_seconds", "setting", "action", "camera",
    "emotion", "characters_present", "narration_line", "character_dialog",
    "dialog_speakers", "has_dialog", "transition_to_next",
    "reference_still_path", "rendered_video_path", "render_status",
    "render_error", "cost_usd", "audio_safety_fallback",
}

_PROJECT_JSON_COLUMNS: set[str] = {"ref_image_paths"}
_CHARACTER_JSON_COLUMNS = {"edit_history"}
_SHOT_JSON_COLUMNS = {"characters_present", "character_dialog", "dialog_speakers"}
_JOB_JSON_COLUMNS = {"input_payload", "output_payload"}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
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
  error_message TEXT,
  ref_image_paths TEXT NOT NULL DEFAULT '[]',
  cast_size_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (cast_size_confirmed IN (0,1))
);

CREATE TABLE IF NOT EXISTS characters (
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

CREATE TABLE IF NOT EXISTS shots (
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
  transition_to_next TEXT NOT NULL DEFAULT 'cut'
    CHECK (transition_to_next IN ('cut','fade','dissolve','match_cut')),
  reference_still_path TEXT,
  rendered_video_path TEXT,
  render_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (render_status IN ('pending','rendering','done','failed')),
  render_error TEXT,
  cost_usd REAL NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
  audio_safety_fallback INTEGER NOT NULL DEFAULT 0
    CHECK (audio_safety_fallback IN (0,1)),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (project_id, ordinal)
);

CREATE TABLE IF NOT EXISTS generation_jobs (
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

CREATE INDEX IF NOT EXISTS idx_characters_project ON characters(project_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_shots_project ON shots(project_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_jobs_project_status ON generation_jobs(project_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, updated_at DESC);
"""


def now_ts() -> int:
    return int(time.time())


def new_id() -> str:
    return _new_ulid()


def _short(project_id: str) -> str:
    return project_id[-8:] if project_id else "?"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        DB_PATH,
        timeout=10,
        isolation_level=None,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _execute_with_retry(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Cursor:
    last_exc: Optional[sqlite3.OperationalError] = None
    for _ in range(5):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_exc = exc
            time.sleep(0.1)
    assert last_exc is not None
    raise last_exc


@contextmanager
def txn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.close()


def _migration_v2_dialog_flags(conn: sqlite3.Connection) -> None:
    """v1 → v2: add has_dialog + dialog_speakers to shots; use_narrator to projects.

    Idempotent — every change is guarded by a PRAGMA table_info check, so
    re-running this migration on an already-v2 DB is a no-op.
    """
    shots_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(shots)").fetchall()
    }

    if "has_dialog" not in shots_cols:
        conn.execute(
            "ALTER TABLE shots "
            "ADD COLUMN has_dialog INTEGER NOT NULL DEFAULT 0",
        )
        # Backfill from existing character_dialog content.
        conn.execute(
            "UPDATE shots SET has_dialog = 1 "
            "WHERE character_dialog IS NOT NULL "
            "  AND character_dialog != '' "
            "  AND character_dialog != 'null' "
            "  AND character_dialog != '[]'",
        )

    if "dialog_speakers" not in shots_cols:
        conn.execute("ALTER TABLE shots ADD COLUMN dialog_speakers TEXT")
        # Backfill: speakers = unique char_ids from character_dialog where
        # has_dialog=1; empty list otherwise. Default-empty is more useful
        # than NULL because callers always treat the field as a list.
        rows = conn.execute(
            "SELECT id, character_dialog FROM shots WHERE has_dialog = 1",
        ).fetchall()
        for shot_id, cd_json in rows:
            try:
                cd = json.loads(cd_json) if cd_json else []
                speakers = list({
                    e["char_id"] for e in cd
                    if isinstance(e, dict) and e.get("char_id")
                })
                conn.execute(
                    "UPDATE shots SET dialog_speakers = ? WHERE id = ?",
                    (json.dumps(speakers), shot_id),
                )
            except (ValueError, KeyError, TypeError):
                conn.execute(
                    "UPDATE shots SET dialog_speakers = '[]' WHERE id = ?",
                    (shot_id,),
                )
        # Any remaining NULLs (has_dialog=0) → '[]'.
        conn.execute(
            "UPDATE shots SET dialog_speakers = '[]' "
            "WHERE dialog_speakers IS NULL",
        )

    proj_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()
    }
    if "use_narrator" not in proj_cols:
        # Existing projects predate the optional-narrator design and
        # always had narration; default 1 preserves their semantics.
        conn.execute(
            "ALTER TABLE projects "
            "ADD COLUMN use_narrator INTEGER NOT NULL DEFAULT 1",
        )


def _migration_v4_project_refs(conn: sqlite3.Connection) -> None:
    """v3 → v4: add ref_image_paths to projects.

    Idempotent — guarded by a PRAGMA table_info check, so re-running on an
    already-v4 DB is a no-op. Default '[]' lets the column be read as a
    JSON array without a NULL guard at every read site.
    """
    proj_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()
    }
    if "ref_image_paths" not in proj_cols:
        conn.execute(
            "ALTER TABLE projects "
            "ADD COLUMN ref_image_paths TEXT NOT NULL DEFAULT '[]'",
        )


def _migration_v5_cast_size_confirmed(conn: sqlite3.Connection) -> None:
    """v4 → v5: add cast_size_confirmed to projects.

    Idempotent — guarded by a PRAGMA table_info check, so re-running on an
    already-v5 DB is a no-op. Default 0 preserves the pre-bump behavior
    (existing small-cast projects don't need explicit confirmation; the
    cost gate only fires above HARD_WARN_CAST_SIZE).
    """
    proj_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()
    }
    if "cast_size_confirmed" not in proj_cols:
        conn.execute(
            "ALTER TABLE projects "
            "ADD COLUMN cast_size_confirmed INTEGER NOT NULL DEFAULT 0 "
            "CHECK (cast_size_confirmed IN (0,1))",
        )


def _migration_v6_audio_safety_fallback(conn: sqlite3.Connection) -> None:
    """v5 to v6: add audio_safety_fallback to shots.

    Idempotent: guarded by a PRAGMA table_info check. Default 0 reflects
    the historical record (no shot has been rescued yet on existing rows).
    Set to 1 by render_worker when a shot succeeds on its second attempt
    after Seedance refused the first attempt's audio with code
    OutputAudioSensitiveContentDetected.
    """
    shots_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(shots)").fetchall()
    }
    if "audio_safety_fallback" not in shots_cols:
        conn.execute(
            "ALTER TABLE shots "
            "ADD COLUMN audio_safety_fallback INTEGER NOT NULL DEFAULT 0 "
            "CHECK (audio_safety_fallback IN (0,1))",
        )


def _migration_v3_shot_transitions(conn: sqlite3.Connection) -> None:
    """v2 → v3: add transition_to_next to shots.

    Idempotent — guarded by a PRAGMA table_info check, so re-running on an
    already-v3 DB is a no-op. The CHECK constraint is supported because
    SQLite ≥ 3.25 (we ship with the system sqlite3 module which is well
    above that on every supported target).
    """
    shots_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(shots)").fetchall()
    }
    if "transition_to_next" not in shots_cols:
        conn.execute(
            "ALTER TABLE shots ADD COLUMN transition_to_next TEXT "
            "NOT NULL DEFAULT 'cut' "
            "CHECK (transition_to_next IN ('cut','fade','dissolve','match_cut'))"
        )


def _migrate() -> None:
    conn = connect()
    try:
        # Phase 1: idempotent base schema (CREATE TABLE IF NOT EXISTS).
        conn.executescript(_SCHEMA_SQL)

        cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
        row = cur.fetchone()
        before = int(row["value"]) if row else 0

        if before == SCHEMA_VERSION:
            return

        # Phase 2: incremental migrations applied atomically. PRAGMA-guarded
        # bodies are idempotent, so a partial-then-resumed run is safe.
        conn.execute("BEGIN IMMEDIATE")
        try:
            if before < 2:
                _migration_v2_dialog_flags(conn)
            if before < 3:
                _migration_v3_shot_transitions(conn)
            if before < 4:
                _migration_v4_project_refs(conn)
            if before < 5:
                _migration_v5_cast_size_confirmed(conn)
            if before < 6:
                _migration_v6_audio_safety_fallback(conn)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        logger.info(
            "schema migrated: version %s -> %s", before, SCHEMA_VERSION,
        )
    finally:
        conn.close()


def _row_to_dict(row: Optional[sqlite3.Row], json_columns: set[str]) -> Optional[dict]:
    if row is None:
        return None
    out = {k: row[k] for k in row.keys()}
    for col in json_columns:
        if col in out and out[col] is not None and isinstance(out[col], str):
            out[col] = json.loads(out[col])
    return out


def _project_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return _row_to_dict(row, _PROJECT_JSON_COLUMNS)


def _character_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return _row_to_dict(row, _CHARACTER_JSON_COLUMNS)


def _shot_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return _row_to_dict(row, _SHOT_JSON_COLUMNS)


def _job_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return _row_to_dict(row, _JOB_JSON_COLUMNS)


def create_project(
    user_id: str,
    surface: str,
    brief: str,
    style_preset_id: str,
    duration_seconds: int,
    vibe: Optional[str] = None,
) -> dict:
    project_id = new_id()
    ts = now_ts()
    with txn() as conn:
        _execute_with_retry(
            conn,
            """INSERT INTO projects (
                id, user_id, surface, brief, style_preset_id, vibe,
                duration_seconds, phase, total_cost_usd, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'brief', 0, ?, ?)""",
            (project_id, user_id, surface, brief, style_preset_id, vibe,
             duration_seconds, ts, ts),
        )
    return get_project(project_id)


def get_project(project_id: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _project_to_dict(row)
    finally:
        conn.close()


def list_projects(
    *,
    user_id: str,
    limit: int = 20,
    phase: Optional[str] = None,
    with_thumbnail: bool = False,
) -> list[dict]:
    """Return up to `limit` projects for a user, ordered by updated_at DESC.
    Optionally filtered by phase.

    When `with_thumbnail=True`, each returned dict gains a `thumb_path`
    key holding the reference_still_path of the project's first shot
    (ordinal=1), or None if no shot exists yet or the first shot has no
    reference still. Path is returned as-is; the asset server returns 404
    if the file is gone.
    """
    sql = "SELECT * FROM projects WHERE user_id = ?"
    params: list[Any] = [user_id]
    if phase is not None:
        sql += " AND phase = ?"
        params.append(phase)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(int(limit))
    conn = connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        projects = [_project_to_dict(r) for r in rows]
        if with_thumbnail:
            for p in projects:
                shot = conn.execute(
                    "SELECT reference_still_path FROM shots "
                    "WHERE project_id = ? AND ordinal = 1",
                    (p["id"],),
                ).fetchone()
                p["thumb_path"] = (
                    shot["reference_still_path"] if shot else None
                )
        return projects
    finally:
        conn.close()


def sum_costs(
    *,
    user_id: str,
    since_ts: Optional[int] = None,
) -> dict:
    """Return {total_usd, project_count, by_phase: {phase: count}}."""
    sql = (
        "SELECT phase, COUNT(*) AS n, COALESCE(SUM(total_cost_usd), 0) AS s "
        "FROM projects WHERE user_id = ?"
    )
    params: list[Any] = [user_id]
    if since_ts is not None:
        sql += " AND created_at >= ?"
        params.append(int(since_ts))
    sql += " GROUP BY phase"

    conn = connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        total = 0.0
        count = 0
        by_phase: dict[str, int] = {}
        for r in rows:
            row = dict(r)
            total += float(row["s"] or 0)
            count += int(row["n"] or 0)
            by_phase[row["phase"]] = int(row["n"] or 0)
        return {
            "total_usd": total,
            "project_count": count,
            "by_phase": by_phase,
        }
    finally:
        conn.close()


def delete_project(project_id: str, *, keep_final_video: bool = False) -> dict:
    """Delete a project and ALL associated data (characters, shots, jobs).
    Filesystem artifacts under projects/<id>/ are removed unless
    keep_final_video=True (in which case the final.mp4 is preserved
    outside the wiped directory as projects/<id>_final.mp4).

    Returns a summary dict for the caller.
    """
    import shutil

    project = get_project(project_id)
    if project is None:
        return {"deleted": False, "reason": "project not found"}

    final_video = project.get("final_video_path")

    with txn() as conn:
        # Order matters: jobs → shots → characters → project (FK cascades
        # would also do this, but we want explicit row counts for the
        # caller).
        n_jobs = conn.execute(
            "DELETE FROM generation_jobs WHERE project_id = ?",
            (project_id,),
        ).rowcount
        n_shots = conn.execute(
            "DELETE FROM shots WHERE project_id = ?",
            (project_id,),
        ).rowcount
        n_chars = conn.execute(
            "DELETE FROM characters WHERE project_id = ?",
            (project_id,),
        ).rowcount
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    proj_dir = Path(__file__).resolve().parent / "projects" / project_id
    fs_kept: Optional[str] = None
    fs_removed = False
    if proj_dir.exists():
        if keep_final_video and final_video and Path(final_video).exists():
            kept_path = proj_dir.parent / f"{project_id}_final.mp4"
            try:
                shutil.move(final_video, kept_path)
                fs_kept = str(kept_path)
            except OSError as e:
                logger.warning("delete_project: keep_final_video failed: %s", e)
        shutil.rmtree(proj_dir, ignore_errors=True)
        fs_removed = True

    logger.info(
        "deleted project %s: jobs=%d shots=%d chars=%d fs_removed=%s kept=%s",
        project_id, n_jobs, n_shots, n_chars, fs_removed, fs_kept,
    )
    return {
        "deleted": True,
        "project_id": project_id,
        "rows_removed": {
            "jobs": n_jobs,
            "shots": n_shots,
            "characters": n_chars,
        },
        "filesystem_removed": fs_removed,
        "final_video_kept_at": fs_kept,
    }


def delete_project_cascade(project_id: str) -> dict:
    """ULID-validated, DB-only cascade delete.

    Mirrors delete_project's row order (jobs -> shots -> characters -> project)
    but skips the filesystem cleanup so the orchestrator can sequence FS
    teardown around in-flight task cancellation. The ULID guard runs BEFORE
    any SQL so a malformed project_id (path traversal attempt, empty string,
    lowercase, wrong length) never reaches the WHERE clause.

    Raises ValueError if project_id is not a valid ULID.
    Raises ProjectNotFoundError if no row matches.
    """
    if not _is_valid_ulid(project_id):
        raise ValueError(f"invalid project_id: {project_id!r}")

    project = get_project(project_id)
    if project is None:
        raise ProjectNotFoundError(f"project not found: {project_id}")

    with txn() as conn:
        n_jobs = _execute_with_retry(
            conn, "DELETE FROM generation_jobs WHERE project_id = ?",
            (project_id,),
        ).rowcount
        n_shots = _execute_with_retry(
            conn, "DELETE FROM shots WHERE project_id = ?",
            (project_id,),
        ).rowcount
        n_chars = _execute_with_retry(
            conn, "DELETE FROM characters WHERE project_id = ?",
            (project_id,),
        ).rowcount
        _execute_with_retry(
            conn, "DELETE FROM projects WHERE id = ?", (project_id,),
        )

    logger.info(
        "delete_project_cascade pid=%s jobs=%d shots=%d chars=%d",
        project_id, n_jobs, n_shots, n_chars,
    )
    return {
        "deleted": True,
        "project_id": project_id,
        "rows_removed": {
            "jobs": n_jobs,
            "shots": n_shots,
            "characters": n_chars,
        },
    }


def recover_orphan_timeline_jobs(threshold_seconds: int = 300) -> list[str]:
    """Mark projects stuck at phase='timeline' with no shots as failed.

    Called at plugin startup to clean up orphans left by a process restart
    that interrupted a background timeline-generation task. Only projects
    where approved_cast_at is older than `threshold_seconds` are touched,
    so an in-flight generation that started moments before this scan runs
    is left alone.

    Returns the list of project IDs that were marked failed.
    """
    cutoff = now_ts() - int(threshold_seconds)
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT p.id
            FROM projects p
            LEFT JOIN shots s ON s.project_id = p.id
            WHERE p.phase = 'timeline'
              AND p.approved_cast_at IS NOT NULL
              AND p.approved_cast_at < ?
            GROUP BY p.id
            HAVING COUNT(s.id) = 0
            """,
            (cutoff,),
        ).fetchall()
        ids = [r["id"] for r in rows]
    finally:
        conn.close()

    for pid in ids:
        try:
            set_phase(
                pid, "failed",
                error_message=(
                    "timeline generation interrupted by process restart; "
                    "retry with /sprite_timeline"
                ),
            )
        except Exception:
            logger.exception("recover_orphan_timeline_jobs: set_phase failed pid=%s", pid)
    return ids


def list_all_project_ids() -> list[str]:
    """Return every project id in creation order. Used by the startup
    backfill that creates on-disk project_dirs for projects whose row
    pre-dates the project-creation-time mkdir (P19a-13).
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id FROM projects ORDER BY created_at"
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def latest_project_for_user(user_id: str, phase: Optional[str] = None) -> Optional[dict]:
    conn = connect()
    try:
        if phase is None:
            row = conn.execute(
                "SELECT * FROM projects WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM projects WHERE user_id = ? AND phase = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (user_id, phase),
            ).fetchone()
        return _project_to_dict(row)
    finally:
        conn.close()


def _build_update(table: str, columns: set[str], json_columns: set[str],
                  pk: str, pk_value: str, fields: dict) -> tuple[str, tuple]:
    unknown = set(fields) - columns
    if unknown:
        raise ValueError(f"unknown column(s) for {table}: {sorted(unknown)}")
    if not fields:
        return "", ()
    pieces: list[str] = []
    values: list[Any] = []
    for col, val in fields.items():
        if col in json_columns and val is not None and not isinstance(val, str):
            val = json.dumps(val)
        pieces.append(f"{col} = ?")
        values.append(val)
    pieces.append("updated_at = ?")
    values.append(now_ts())
    sql = f"UPDATE {table} SET {', '.join(pieces)} WHERE {pk} = ?"
    values.append(pk_value)
    return sql, tuple(values)


def update_project(project_id: str, **fields) -> dict:
    sql, params = _build_update(
        "projects", _PROJECT_COLUMNS, _PROJECT_JSON_COLUMNS, "id", project_id, fields,
    )
    if sql:
        with txn() as conn:
            _execute_with_retry(conn, sql, params)
    return get_project(project_id)


def update_project_fields(
    project_id: str,
    *,
    allowed_phases: Optional[set[str]] = None,
    **fields: Any,
) -> dict:
    """Phase-guarded field updater for the web bridge.

    Returns a status dict instead of raising so handlers can serialize a
    structured error response.
    """
    if not fields:
        return {"updated": False, "reason": "no_fields"}

    project = get_project(project_id)
    if project is None:
        return {"updated": False, "reason": "project_not_found"}

    if allowed_phases is not None and project["phase"] not in allowed_phases:
        return {
            "updated": False,
            "reason": "phase_locked",
            "phase": project["phase"],
            "allowed": sorted(allowed_phases),
        }

    unknown = set(fields) - _PROJECT_COLUMNS
    if unknown:
        return {
            "updated": False,
            "reason": "unknown_fields",
            "fields": sorted(unknown),
        }

    sql, params = _build_update(
        "projects", _PROJECT_COLUMNS, _PROJECT_JSON_COLUMNS,
        "id", project_id, fields,
    )
    with txn() as conn:
        _execute_with_retry(conn, sql, params)

    return {"updated": True, "fields": fields}


def set_phase(project_id: str, phase: str, error_message: Optional[str] = None) -> dict:
    fields: dict = {"phase": phase}
    if error_message is not None:
        fields["error_message"] = error_message
    return update_project(project_id, **fields)


def create_character(
    project_id: str,
    ordinal: int,
    name: str,
    role: Optional[str],
    persona: str,
    visual_description: str,
    voice_personality: Optional[str] = None,
) -> dict:
    char_id = new_id()
    ts = now_ts()
    with txn() as conn:
        _execute_with_retry(
            conn,
            """INSERT INTO characters (
                id, project_id, ordinal, name, role, persona,
                visual_description, voice_personality, edit_history,
                is_approved, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', 0, ?, ?)""",
            (char_id, project_id, ordinal, name, role, persona,
             visual_description, voice_personality, ts, ts),
        )
    return get_character(char_id)


def get_character(character_id: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM characters WHERE id = ?", (character_id,),
        ).fetchone()
        return _character_to_dict(row)
    finally:
        conn.close()


def list_characters(project_id: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM characters WHERE project_id = ? ORDER BY ordinal ASC",
            (project_id,),
        ).fetchall()
        return [_character_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_character(character_id: str, **fields) -> dict:
    sql, params = _build_update(
        "characters", _CHARACTER_COLUMNS, _CHARACTER_JSON_COLUMNS,
        "id", character_id, fields,
    )
    if sql:
        with txn() as conn:
            _execute_with_retry(conn, sql, params)
    return get_character(character_id)


def delete_character(character_id: str) -> None:
    with txn() as conn:
        _execute_with_retry(conn, "DELETE FROM characters WHERE id = ?", (character_id,))


def reorder_characters(project_id: str, ordered_ids: list[str]) -> dict:
    """Atomic re-ordering of characters by ordinal.

    Validates that the supplied id set matches the project's existing
    character set exactly (no add, no drop). Uses a two-pass write
    (negative parking ordinals, then positive) so the UNIQUE
    (project_id, ordinal) constraint never collides mid-update.
    """
    existing = list_characters(project_id)
    existing_ids = {c["id"] for c in existing}
    supplied_ids = set(ordered_ids)

    if existing_ids != supplied_ids:
        missing = existing_ids - supplied_ids
        extra = supplied_ids - existing_ids
        return {
            "updated": False,
            "reason": "id_mismatch",
            "missing": sorted(missing),
            "extra": sorted(extra),
        }

    # Two-pass write: first park ordinals at high values that satisfy the
    # CHECK (ordinal >= 1) constraint and don't collide with the live
    # ordinal range (cast cap is models.MAX_CAST_SIZE=30), then write the
    # real values. This avoids a UNIQUE (project_id, ordinal) collision
    # mid-update.
    ts = now_ts()
    park_offset = 100000
    with txn() as conn:
        for new_ordinal, char_id in enumerate(ordered_ids, start=1):
            _execute_with_retry(
                conn,
                "UPDATE characters SET ordinal = ?, updated_at = ? "
                "WHERE id = ? AND project_id = ?",
                (park_offset + new_ordinal, ts, char_id, project_id),
            )
        for new_ordinal, char_id in enumerate(ordered_ids, start=1):
            _execute_with_retry(
                conn,
                "UPDATE characters SET ordinal = ?, updated_at = ? "
                "WHERE id = ? AND project_id = ?",
                (new_ordinal, ts, char_id, project_id),
            )

    return {"updated": True, "count": len(ordered_ids)}


def create_shot(
    project_id: str,
    ordinal: int,
    duration_seconds: int,
    setting: str,
    action: str,
    characters_present: list[str],
    camera: Optional[str] = None,
    emotion: Optional[str] = None,
    narration_line: Optional[str] = None,
    character_dialog: Any = None,
    dialog_speakers: Optional[list[str]] = None,
    has_dialog: bool = False,
    transition_to_next: str = "cut",
) -> dict:
    if transition_to_next not in VALID_SHOT_TRANSITIONS:
        raise ValueError(
            f"transition_to_next must be one of "
            f"{list(VALID_SHOT_TRANSITIONS)}, got {transition_to_next!r}"
        )
    shot_id = new_id()
    ts = now_ts()
    cp_json = json.dumps(characters_present)
    cd_json = json.dumps(character_dialog) if character_dialog is not None else None
    ds_json = json.dumps(list(dialog_speakers) if dialog_speakers else [])
    with txn() as conn:
        _execute_with_retry(
            conn,
            """INSERT INTO shots (
                id, project_id, ordinal, duration_seconds, setting, action,
                camera, emotion, characters_present, narration_line,
                character_dialog, render_status, cost_usd,
                created_at, updated_at, has_dialog, dialog_speakers,
                transition_to_next
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, ordinal, duration_seconds, setting, action,
             camera, emotion, cp_json, narration_line, cd_json, ts, ts,
             1 if has_dialog else 0, ds_json, transition_to_next),
        )
    return get_shot(shot_id)


def get_shot(shot_id: str) -> Optional[dict]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
        return _shot_to_dict(row)
    finally:
        conn.close()


def list_shots(project_id: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM shots WHERE project_id = ? ORDER BY ordinal ASC",
            (project_id,),
        ).fetchall()
        return [_shot_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_shot(shot_id: str, **fields) -> dict:
    sql, params = _build_update(
        "shots", _SHOT_COLUMNS, _SHOT_JSON_COLUMNS, "id", shot_id, fields,
    )
    if sql:
        with txn() as conn:
            _execute_with_retry(conn, sql, params)
    return get_shot(shot_id)


def reorder_shots(project_id: str, ordered_ids: list[str]) -> dict:
    """Atomic re-ordering of shots by ordinal.

    Validates that the supplied id set matches the project's existing
    shot set exactly (no add, no drop). Uses a two-pass write with
    positive parking ordinals so the CHECK (ordinal >= 1) constraint is
    never violated and UNIQUE (project_id, ordinal) never collides
    mid-update. Mirrors reorder_characters.
    """
    existing = list_shots(project_id)
    existing_ids = {s["id"] for s in existing}
    supplied_ids = set(ordered_ids)

    if existing_ids != supplied_ids:
        missing = existing_ids - supplied_ids
        extra = supplied_ids - existing_ids
        return {
            "updated": False,
            "reason": "id_mismatch",
            "missing": sorted(missing),
            "extra": sorted(extra),
        }

    ts = now_ts()
    park_offset = 100000
    with txn() as conn:
        for new_ordinal, shot_id in enumerate(ordered_ids, start=1):
            _execute_with_retry(
                conn,
                "UPDATE shots SET ordinal = ?, updated_at = ? "
                "WHERE id = ? AND project_id = ?",
                (park_offset + new_ordinal, ts, shot_id, project_id),
            )
        for new_ordinal, shot_id in enumerate(ordered_ids, start=1):
            _execute_with_retry(
                conn,
                "UPDATE shots SET ordinal = ?, updated_at = ? "
                "WHERE id = ? AND project_id = ?",
                (new_ordinal, ts, shot_id, project_id),
            )

    return {"updated": True, "count": len(ordered_ids)}


_SHOT_SAFE_FIELDS = {
    "duration_seconds", "setting", "action", "camera", "emotion",
    "narration_line", "transition_to_next",
    "character_dialog", "characters_present",
}


def _validate_character_dialog(value: Any) -> str:
    """Return canonical JSON for a character_dialog write. Raise ValueError on shape mismatch.

    Accepts either an already-parsed list or a JSON-string. Each entry must
    be {char_id: str, line: str}; extra keys are silently dropped on output
    so the stored JSON is canonical.
    """
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            parsed = json.loads(value) if value else []
        except json.JSONDecodeError as e:
            raise ValueError(f"character_dialog is not valid JSON: {e}") from e
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError(
            f"character_dialog must be a list, got {type(parsed).__name__}",
        )
    cleaned: list[dict] = []
    for i, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            raise ValueError(f"character_dialog[{i}] must be an object")
        if "char_id" not in entry or "line" not in entry:
            raise ValueError(
                f"character_dialog[{i}] missing required keys char_id/line",
            )
        char_id = entry["char_id"]
        line = entry["line"]
        if not isinstance(char_id, str) or not isinstance(line, str):
            raise ValueError(
                f"character_dialog[{i}] keys char_id/line must be strings",
            )
        cleaned.append({"char_id": char_id, "line": line})
    return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))


def _validate_characters_present(value: Any) -> str:
    """Return canonical JSON for a characters_present write. Raise ValueError on shape mismatch."""
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            parsed = json.loads(value) if value else []
        except json.JSONDecodeError as e:
            raise ValueError(
                f"characters_present is not valid JSON: {e}",
            ) from e
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError(
            f"characters_present must be a list, got {type(parsed).__name__}",
        )
    for i, entry in enumerate(parsed):
        if not isinstance(entry, str):
            raise ValueError(f"characters_present[{i}] must be a string")
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


_SHOT_FIELD_VALIDATORS = {
    "character_dialog": _validate_character_dialog,
    "characters_present": _validate_characters_present,
}


def update_shot_fields(
    shot_id: str,
    *,
    allowed_phases: Optional[set[str]] = None,
    **fields: Any,
) -> dict:
    """Phase-guarded shot field updater with a column whitelist.

    Returns a status dict instead of raising so handlers can serialize a
    structured error response. Unknown fields are silently dropped after
    the whitelist filter, never written. JSON-column writes are validated
    and canonicalized; an invalid value yields a structured error and
    leaves the row unchanged.

    When character_dialog is updated, has_dialog and dialog_speakers are
    derived in the same UPDATE so the audio routing flag (consumed by
    Seedance/render_worker) and the speaker list stay in sync.
    """
    if not fields:
        return {"updated": False, "reason": "no_fields"}

    shot = get_shot(shot_id)
    if shot is None:
        return {"updated": False, "reason": "shot_not_found"}

    if allowed_phases is not None:
        project = get_project(shot["project_id"])
        if project is None:
            return {"updated": False, "reason": "project_not_found"}
        if project["phase"] not in allowed_phases:
            return {
                "updated": False,
                "reason": "phase_locked",
                "phase": project["phase"],
                "allowed": sorted(allowed_phases),
            }

    # narration_excerpt is not a real column — map it onto narration_line
    # which is the canonical narration storage field.
    if "narration_excerpt" in fields:
        fields["narration_line"] = fields.pop("narration_excerpt")

    safe_fields = {k: v for k, v in fields.items() if k in _SHOT_SAFE_FIELDS}
    if not safe_fields:
        return {"updated": False, "reason": "no_safe_fields"}

    for field_name, validator in _SHOT_FIELD_VALIDATORS.items():
        if field_name in safe_fields:
            try:
                safe_fields[field_name] = validator(safe_fields[field_name])
            except ValueError as e:
                return {
                    "updated": False,
                    "reason": "invalid_value",
                    "field": field_name,
                    "detail": str(e),
                }

    # Derive has_dialog/dialog_speakers from the canonical character_dialog
    # JSON. These are not in _SHOT_SAFE_FIELDS on purpose: the frontend
    # must not be able to set them out of sync with character_dialog.
    if "character_dialog" in safe_fields:
        parsed = json.loads(safe_fields["character_dialog"])
        safe_fields["has_dialog"] = 1 if parsed else 0
        speakers = sorted({e["char_id"] for e in parsed})
        safe_fields["dialog_speakers"] = json.dumps(
            speakers, ensure_ascii=False, separators=(",", ":"),
        )

    set_clause = ", ".join(f"{k} = ?" for k in safe_fields)
    params = (*safe_fields.values(), now_ts(), shot_id)

    with txn() as conn:
        _execute_with_retry(
            conn,
            f"UPDATE shots SET {set_clause}, updated_at = ? WHERE id = ?",
            params,
        )

    return {"updated": True, "fields": safe_fields}


def create_shot_at_ordinal(
    project_id: str,
    ordinal: int,
    *,
    duration_seconds: int,
    setting: str,
    action: str,
    characters_present: list[str],
    camera: Optional[str] = None,
    emotion: Optional[str] = None,
    narration_line: Optional[str] = None,
    character_dialog: Any = None,
    dialog_speakers: Optional[list[str]] = None,
    has_dialog: bool = False,
    transition_to_next: str = "cut",
    allowed_phases: Optional[set[str]] = None,
) -> dict:
    """Insert a shot at `ordinal`, shifting existing shots at >= ordinal up by 1.

    Mirrors the two-pass park-and-unpark pattern from reorder_shots so the
    UNIQUE(project_id, ordinal) constraint never collides mid-update. The
    phase guard is inside the txn (closes the read-vs-write race that an
    orchestrator-level check leaves open).

    Concurrent inserts targeting the same ordinal serialize via SQLite's
    BEGIN IMMEDIATE; the second call's range check runs after the first
    commits and shifts up by 1 if needed.

    Raises:
        ValueError: ordinal out of range, transition invalid, or
            characters_present contains ids that don't belong to the project.
        ProjectNotFoundError: project_id does not exist.
        WrongPhaseError: project's phase is not in allowed_phases.
    """
    if transition_to_next not in VALID_SHOT_TRANSITIONS:
        raise ValueError(
            f"transition_to_next must be one of "
            f"{list(VALID_SHOT_TRANSITIONS)}, got {transition_to_next!r}"
        )
    if duration_seconds < 5 or duration_seconds > 15:
        raise ValueError(
            f"duration_seconds must be 5..15 (got {duration_seconds})"
        )
    if not isinstance(action, str) or not action.strip():
        raise ValueError("action must be a non-empty string")
    if len(action) > 2000:
        raise ValueError(f"action exceeds 2000 chars (got {len(action)})")

    shot_id = new_id()
    ts = now_ts()
    cp_json = json.dumps(list(characters_present or []))
    cd_json = json.dumps(character_dialog) if character_dialog is not None else None
    ds_json = json.dumps(list(dialog_speakers) if dialog_speakers else [])

    with txn() as conn:
        # Phase + project-existence check inside the txn closes the
        # check-vs-write race the orchestrator can't.
        proj_row = conn.execute(
            "SELECT phase FROM projects WHERE id = ?", (project_id,),
        ).fetchone()
        if proj_row is None:
            raise ProjectNotFoundError(f"project not found: {project_id}")
        if allowed_phases is not None and proj_row["phase"] not in allowed_phases:
            raise WrongPhaseError(
                f"project {project_id} is in phase {proj_row['phase']!r}, "
                f"expected one of {sorted(allowed_phases)}"
            )

        # Cross-project character validation: every supplied char_id must
        # belong to this project. Defends against accidental id leakage in
        # multi-project sessions.
        if characters_present:
            seen = set()
            unique_ids = []
            for cid in characters_present:
                if cid in seen:
                    continue
                seen.add(cid)
                unique_ids.append(cid)
            placeholders = ",".join("?" for _ in unique_ids)
            rows = conn.execute(
                f"SELECT id FROM characters "
                f"WHERE project_id = ? AND id IN ({placeholders})",
                (project_id, *unique_ids),
            ).fetchall()
            found = {r["id"] for r in rows}
            missing = [c for c in unique_ids if c not in found]
            if missing:
                raise ValueError(
                    f"unknown or cross-project character_ids: {missing}"
                )

        n_existing = conn.execute(
            "SELECT COUNT(*) FROM shots WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        if not (1 <= int(ordinal) <= n_existing + 1):
            raise ValueError(
                f"ordinal {ordinal} out of range 1..{n_existing + 1}"
            )

        # Park-and-shift: shots at ordinal >= insert get pushed up by 1
        # AND offset out of the live range, so a single-pass unpark
        # (subtract OFFSET) lands them on their final ordinals. Mirrors
        # reorder_shots' two-pass write but tighter (combined shift+park).
        _execute_with_retry(
            conn,
            "UPDATE shots SET ordinal = ordinal + ?, updated_at = ? "
            "WHERE project_id = ? AND ordinal >= ?",
            (_ORDINAL_PARK_OFFSET + 1, ts, project_id, int(ordinal)),
        )

        _execute_with_retry(
            conn,
            """INSERT INTO shots (
                id, project_id, ordinal, duration_seconds, setting, action,
                camera, emotion, characters_present, narration_line,
                character_dialog, render_status, cost_usd,
                created_at, updated_at, has_dialog, dialog_speakers,
                transition_to_next
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?)""",
            (shot_id, project_id, int(ordinal), int(duration_seconds),
             setting, action, camera, emotion, cp_json, narration_line,
             cd_json, ts, ts, 1 if has_dialog else 0, ds_json,
             transition_to_next),
        )

        _execute_with_retry(
            conn,
            "UPDATE shots SET ordinal = ordinal - ?, updated_at = ? "
            "WHERE project_id = ? AND ordinal >= ?",
            (_ORDINAL_PARK_OFFSET, ts, project_id, _ORDINAL_PARK_OFFSET),
        )

        _execute_with_retry(
            conn,
            "UPDATE projects SET updated_at = ? WHERE id = ?",
            (ts, project_id),
        )

    return get_shot(shot_id)


def delete_shot(
    shot_id: str,
    *,
    allowed_phases: Optional[set[str]] = None,
) -> dict:
    """Delete a shot and pack the remaining ordinals down by 1.

    Atomic: phase guard + last-shot guard + delete + repack live in a
    single txn, mirroring create_shot_at_ordinal. The shot's filesystem
    artifacts (per-shot dir under projects/<id>/shots/<shot_id>/) are NOT
    touched here; the orchestrator owns the trash move.

    Raises:
        ShotNotFoundError: shot_id doesn't exist.
        WrongPhaseError: project's phase is not in allowed_phases.
        LastShotError: deleting would leave the project with zero shots.
    """
    ts = now_ts()
    with txn() as conn:
        row = conn.execute(
            "SELECT s.ordinal, s.project_id, p.phase "
            "FROM shots s JOIN projects p ON p.id = s.project_id "
            "WHERE s.id = ?",
            (shot_id,),
        ).fetchone()
        if row is None:
            raise ShotNotFoundError(f"shot not found: {shot_id}")
        project_id = row["project_id"]
        deleted_ordinal = int(row["ordinal"])
        if allowed_phases is not None and row["phase"] not in allowed_phases:
            raise WrongPhaseError(
                f"project {project_id} is in phase {row['phase']!r}, "
                f"expected one of {sorted(allowed_phases)}"
            )

        n_existing = conn.execute(
            "SELECT COUNT(*) FROM shots WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        if n_existing <= 1:
            raise LastShotError(
                f"cannot delete the only shot in project {project_id}; "
                f"the timeline must have >= 1 shot"
            )

        _execute_with_retry(
            conn,
            "DELETE FROM shots WHERE id = ?",
            (shot_id,),
        )

        # Park-and-shift-down: shots above the gap get pushed up by OFFSET
        # AND down by 1 in a single statement, then a single-pass unpark
        # (subtract OFFSET) lands them on their final ordinals.
        _execute_with_retry(
            conn,
            "UPDATE shots SET ordinal = ordinal + ? - 1, updated_at = ? "
            "WHERE project_id = ? AND ordinal > ?",
            (_ORDINAL_PARK_OFFSET, ts, project_id, deleted_ordinal),
        )

        _execute_with_retry(
            conn,
            "UPDATE shots SET ordinal = ordinal - ?, updated_at = ? "
            "WHERE project_id = ? AND ordinal >= ?",
            (_ORDINAL_PARK_OFFSET, ts, project_id, _ORDINAL_PARK_OFFSET),
        )

        _execute_with_retry(
            conn,
            "UPDATE projects SET updated_at = ? WHERE id = ?",
            (ts, project_id),
        )

    return {
        "deleted": shot_id,
        "project_id": project_id,
        "ordinal_was": deleted_ordinal,
    }


def create_job(
    project_id: str,
    job_type: str,
    provider: str,
    model: str,
    input_payload: Optional[dict],
) -> dict:
    job_id = new_id()
    ts = now_ts()
    payload = json.dumps(input_payload) if input_payload is not None else None
    with txn() as conn:
        _execute_with_retry(
            conn,
            """INSERT INTO generation_jobs (
                id, project_id, job_type, provider, model,
                status, input_payload, attempt_count, created_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, 0, ?)""",
            (job_id, project_id, job_type, provider, model, payload, ts),
        )
    logger.info("job created: project=%s type=%s status=queued", _short(project_id), job_type)
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_to_dict(row)
    finally:
        conn.close()


def _job_lookup(conn: sqlite3.Connection, job_id: str) -> tuple[str, str]:
    row = conn.execute(
        "SELECT project_id, job_type FROM generation_jobs WHERE id = ?", (job_id,),
    ).fetchone()
    if row is None:
        return "", "?"
    return row["project_id"], row["job_type"]


def mark_job_running(job_id: str) -> None:
    with txn() as conn:
        project_id, job_type = _job_lookup(conn, job_id)
        _execute_with_retry(
            conn,
            "UPDATE generation_jobs SET status='running', "
            "attempt_count = attempt_count + 1 WHERE id = ?",
            (job_id,),
        )
    logger.info("job state: project=%s type=%s status=running", _short(project_id), job_type)


def mark_job_done(job_id: str, output_payload: Optional[dict], cost_usd: float) -> None:
    payload = json.dumps(output_payload) if output_payload is not None else None
    ts = now_ts()
    with txn() as conn:
        project_id, job_type = _job_lookup(conn, job_id)
        _execute_with_retry(
            conn,
            "UPDATE generation_jobs SET status='done', output_payload = ?, "
            "cost_usd = ?, completed_at = ? WHERE id = ?",
            (payload, cost_usd, ts, job_id),
        )
    logger.info("job state: project=%s type=%s status=done", _short(project_id), job_type)


def mark_job_failed(job_id: str, error_message: str) -> None:
    ts = now_ts()
    with txn() as conn:
        project_id, job_type = _job_lookup(conn, job_id)
        _execute_with_retry(
            conn,
            "UPDATE generation_jobs SET status='failed', error_message = ?, "
            "completed_at = ? WHERE id = ?",
            (error_message, ts, job_id),
        )
    logger.info("job state: project=%s type=%s status=failed", _short(project_id), job_type)


def mark_job_cancelled(job_id: str, reason: Optional[str] = None) -> None:
    ts = now_ts()
    with txn() as conn:
        project_id, job_type = _job_lookup(conn, job_id)
        if reason is not None:
            _execute_with_retry(
                conn,
                "UPDATE generation_jobs SET status='cancelled', "
                "error_message = ?, completed_at = ? WHERE id = ?",
                (reason[:500], ts, job_id),
            )
        else:
            _execute_with_retry(
                conn,
                "UPDATE generation_jobs SET status='cancelled', "
                "completed_at = ? WHERE id = ?",
                (ts, job_id),
            )
    logger.info("job state: project=%s type=%s status=cancelled", _short(project_id), job_type)


def list_jobs(
    *,
    project_id: str,
    status: Optional[str] = None,
) -> list[dict]:
    """Return generation_jobs rows for a project, optionally filtered by status."""
    sql = "SELECT * FROM generation_jobs WHERE project_id = ?"
    params: list[Any] = [project_id]
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at ASC"
    conn = connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_job_to_dict(r) for r in rows]
    finally:
        conn.close()


def increment_project_cost(project_id: str, delta_usd: float) -> None:
    ts = now_ts()
    with txn() as conn:
        _execute_with_retry(
            conn,
            "UPDATE projects SET total_cost_usd = total_cost_usd + ?, "
            "updated_at = ? WHERE id = ?",
            (delta_usd, ts, project_id),
        )


_migrate()

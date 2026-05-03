import inspect
import time

import pytest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Redirect db.DB_PATH to a per-test sqlite file and run schema migration."""
    from plugin import db

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db._migrate()
    return db


def _stale(seconds: int = 7200) -> int:
    return int(time.time()) - seconds


def _insert_project(
    db,
    project_id: str,
    *,
    phase: str,
    approved_cast_at: int,
    updated_at: int,
) -> None:
    with db.txn() as conn:
        conn.execute(
            """INSERT INTO projects
               (id, user_id, surface, brief, style_preset_id,
                duration_seconds, phase, total_cost_usd,
                created_at, updated_at, approved_cast_at)
               VALUES (?, 'u', 'cli', 'brief', 'studio-ghibli',
                       60, ?, 0, ?, ?, ?)""",
            (project_id, phase, updated_at, updated_at, approved_cast_at),
        )


def _insert_running_job(db, *, project_id: str, created_at: int) -> str:
    job_id = db.new_id()
    with db.txn() as conn:
        conn.execute(
            """INSERT INTO generation_jobs
               (id, project_id, job_type, provider, model,
                status, attempt_count, created_at)
               VALUES (?, ?, 'llm', 'tokenrouter', 'moonshotai/kimi-k2.6',
                       'running', 1, ?)""",
            (job_id, project_id, created_at),
        )
    return job_id


def test_skips_project_with_recent_running_job(temp_db):
    db = temp_db
    pid = "01TESTRECENTRUN0000000000A"
    _insert_project(
        db, pid,
        phase="timeline",
        approved_cast_at=_stale(7000),
        updated_at=_stale(7000),
    )
    _insert_running_job(db, project_id=pid, created_at=_stale(60))
    recovered = db.recover_orphan_timeline_jobs(threshold_seconds=1800)
    assert pid not in recovered, (
        "should not recover while a running job has been alive for less than "
        "_MAX_IN_FLIGHT_TIMELINE_SECONDS"
    )


def test_recovers_project_with_old_running_job(temp_db):
    db = temp_db
    pid = "01TESTOLDRUNNING000000000B"
    _insert_project(
        db, pid,
        phase="timeline",
        approved_cast_at=_stale(7000),
        updated_at=_stale(7000),
    )
    _insert_running_job(db, project_id=pid, created_at=_stale(7000))
    recovered = db.recover_orphan_timeline_jobs(threshold_seconds=1800)
    assert pid in recovered, "should recover when running job is older than max in-flight"


def test_recovers_project_with_no_jobs(temp_db):
    db = temp_db
    pid = "01TESTNOJOBS00000000000000"
    _insert_project(
        db, pid,
        phase="timeline",
        approved_cast_at=_stale(7000),
        updated_at=_stale(7000),
    )
    recovered = db.recover_orphan_timeline_jobs(threshold_seconds=1800)
    assert pid in recovered


def test_does_not_recover_recently_started_timeline(temp_db):
    db = temp_db
    pid = "01TESTRECENTTIMELINE000000"
    _insert_project(
        db, pid,
        phase="timeline",
        approved_cast_at=_stale(120),
        updated_at=_stale(120),
    )
    recovered = db.recover_orphan_timeline_jobs(threshold_seconds=1800)
    assert pid not in recovered, (
        "should not recover a project whose timeline only started 2 minutes ago"
    )


def test_default_threshold_is_1800():
    """Hard guard: do not silently regress the threshold to 300s."""
    from plugin import db
    sig = inspect.signature(db.recover_orphan_timeline_jobs)
    default = sig.parameters["threshold_seconds"].default
    assert default == 1800, f"default threshold must remain 1800, got {default}"

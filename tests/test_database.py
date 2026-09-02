"""Tests for the SQLite history store."""

from __future__ import annotations

from pathlib import Path

from maintenance.database import Database, NullDatabase, open_database


def test_run_lifecycle_and_summary(tmp_path: Path) -> None:
    db = Database(tmp_path / "h.db")
    try:
        db.start_run("run1", "clean", environment="test", dry_run=False)
        db.record_cleanup("run1", files_deleted=10, bytes_reclaimed=2048, duplicates_removed=2)
        db.finish_run("run1", status="ok", duration_s=1.5, health_score=88)
        summary = db.summary()
        assert summary["total_runs"] == 1
        assert summary["total_bytes_reclaimed"] == 2048
        assert summary["avg_health_score"] == 88.0
        runs = db.recent_runs()
        assert runs[0]["run_id"] == "run1"
        assert runs[0]["status"] == "ok"
    finally:
        db.close()


def test_backup_and_error_recording(tmp_path: Path) -> None:
    db = Database(tmp_path / "h.db")
    try:
        db.start_run("r", "backup", environment="test", dry_run=False)
        db.record_backup("r", source="/src", archive_path="/a.zip", files_count=5, bytes_total=99, verified=True)
        db.record_error("backup", "disk full", run_id="r")
        summary = db.summary()
        assert summary["total_backups"] == 1
        assert summary["total_errors"] == 1
    finally:
        db.close()


def test_null_database_is_noop() -> None:
    db = open_database(Path("unused.db"), enabled=False)
    assert isinstance(db, NullDatabase)
    db.start_run("x", "clean", environment="e", dry_run=True)
    db.record_cleanup("x", files_deleted=1)
    assert db.summary()["total_runs"] == 0
    assert db.recent_runs() == []
    db.close()


def test_context_manager(tmp_path: Path) -> None:
    with Database(tmp_path / "c.db") as db:
        db.start_run("ctx", "monitor", environment="t", dry_run=False)
    # Reopening confirms data was committed.
    with Database(tmp_path / "c.db") as db2:
        assert db2.summary()["total_runs"] == 1

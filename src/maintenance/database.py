"""SQLite persistence for maintenance history and statistics.

A thin repository over ``sqlite3`` that records every run and its outcomes so
the CLI and web dashboard can show trends (storage reclaimed over time, backup
success rate, recurring errors). The schema is created idempotently on first
use; there are no external migration tools to install.

The store is intentionally forgiving: when ``database.enabled`` is false, a
:class:`NullDatabase` no-op stand-in is returned so callers never branch on
persistence being available.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from .utils import iso_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT    NOT NULL UNIQUE,
    command       TEXT    NOT NULL,
    environment   TEXT,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    duration_s    REAL,
    status        TEXT    NOT NULL DEFAULT 'running',
    health_score  INTEGER,
    dry_run       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cleanup_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT NOT NULL,
    files_deleted      INTEGER NOT NULL DEFAULT 0,
    bytes_reclaimed    INTEGER NOT NULL DEFAULT 0,
    duplicates_removed INTEGER NOT NULL DEFAULT 0,
    empty_dirs_removed INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backup_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    source       TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    files_count  INTEGER NOT NULL DEFAULT 0,
    bytes_total  INTEGER NOT NULL DEFAULT 0,
    incremental  INTEGER NOT NULL DEFAULT 0,
    verified     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disk_stats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    path       TEXT NOT NULL,
    total      INTEGER NOT NULL,
    used       INTEGER NOT NULL,
    free       INTEGER NOT NULL,
    percent    REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS errors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT,
    module     TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    format     TEXT NOT NULL,
    path       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_cleanup_run ON cleanup_history(run_id);
CREATE INDEX IF NOT EXISTS idx_backup_run ON backup_history(run_id);
CREATE INDEX IF NOT EXISTS idx_errors_run ON errors(run_id);
"""


class DatabaseLike(Protocol):
    """Structural type implemented by both the real and null databases."""

    def start_run(self, run_id: str, command: str, *, environment: str, dry_run: bool) -> None: ...
    def finish_run(self, run_id: str, *, status: str, duration_s: float, health_score: int | None) -> None: ...
    def record_cleanup(self, run_id: str, **stats: int) -> None: ...
    def record_backup(self, run_id: str, **fields: Any) -> None: ...
    def record_disk(self, run_id: str, path: str, total: int, used: int, free: int, percent: float) -> None: ...
    def record_error(self, module: str, message: str, *, run_id: str | None = None) -> None: ...
    def record_report(self, run_id: str, fmt: str, path: str) -> None: ...
    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]: ...
    def summary(self) -> dict[str, Any]: ...
    def close(self) -> None: ...


class Database:
    """Concrete SQLite-backed history store."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # -- writes ------------------------------------------------------------ #
    def start_run(self, run_id: str, command: str, *, environment: str, dry_run: bool) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO runs (run_id, command, environment, started_at, status, dry_run)"
                " VALUES (?, ?, ?, ?, 'running', ?)",
                (run_id, command, environment, iso_now(), int(dry_run)),
            )

    def finish_run(self, run_id: str, *, status: str, duration_s: float, health_score: int | None) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE runs SET finished_at=?, duration_s=?, status=?, health_score=? WHERE run_id=?",
                (iso_now(), duration_s, status, health_score, run_id),
            )

    def record_cleanup(self, run_id: str, **stats: int) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO cleanup_history "
                "(run_id, files_deleted, bytes_reclaimed, duplicates_removed, empty_dirs_removed, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    int(stats.get("files_deleted", 0)),
                    int(stats.get("bytes_reclaimed", 0)),
                    int(stats.get("duplicates_removed", 0)),
                    int(stats.get("empty_dirs_removed", 0)),
                    iso_now(),
                ),
            )

    def record_backup(self, run_id: str, **fields: Any) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO backup_history "
                "(run_id, source, archive_path, files_count, bytes_total, incremental, verified, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    str(fields.get("source", "")),
                    str(fields.get("archive_path", "")),
                    int(fields.get("files_count", 0)),
                    int(fields.get("bytes_total", 0)),
                    int(bool(fields.get("incremental", False))),
                    int(bool(fields.get("verified", False))),
                    iso_now(),
                ),
            )

    def record_disk(self, run_id: str, path: str, total: int, used: int, free: int, percent: float) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO disk_stats (run_id, path, total, used, free, percent, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, path, total, used, free, percent, iso_now()),
            )

    def record_error(self, module: str, message: str, *, run_id: str | None = None) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO errors (run_id, module, message, created_at) VALUES (?, ?, ?, ?)",
                (run_id, module, message, iso_now()),
            )

    def record_report(self, run_id: str, fmt: str, path: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO reports (run_id, format, path, created_at) VALUES (?, ?, ?, ?)",
                (run_id, fmt, path, iso_now()),
            )

    # -- reads ------------------------------------------------------------- #
    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]

    def summary(self) -> dict[str, Any]:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM runs")
            total_runs = cur.fetchone()["n"]
            cur.execute("SELECT COALESCE(SUM(bytes_reclaimed), 0) AS b FROM cleanup_history")
            reclaimed = cur.fetchone()["b"]
            cur.execute("SELECT COUNT(*) AS n FROM backup_history")
            backups = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM errors")
            errors = cur.fetchone()["n"]
            cur.execute("SELECT AVG(health_score) AS a FROM runs WHERE health_score IS NOT NULL")
            avg_health = cur.fetchone()["a"]
        return {
            "total_runs": total_runs,
            "total_bytes_reclaimed": reclaimed,
            "total_backups": backups,
            "total_errors": errors,
            "avg_health_score": round(avg_health, 1) if avg_health is not None else None,
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class NullDatabase:
    """No-op store used when persistence is disabled."""

    def start_run(self, *args: Any, **kwargs: Any) -> None: ...
    def finish_run(self, *args: Any, **kwargs: Any) -> None: ...
    def record_cleanup(self, *args: Any, **kwargs: Any) -> None: ...
    def record_backup(self, *args: Any, **kwargs: Any) -> None: ...
    def record_disk(self, *args: Any, **kwargs: Any) -> None: ...
    def record_error(self, *args: Any, **kwargs: Any) -> None: ...
    def record_report(self, *args: Any, **kwargs: Any) -> None: ...
    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return []
    def summary(self) -> dict[str, Any]:
        return {
            "total_runs": 0,
            "total_bytes_reclaimed": 0,
            "total_backups": 0,
            "total_errors": 0,
            "avg_health_score": None,
        }
    def close(self) -> None: ...
    def __enter__(self) -> NullDatabase:
        return self
    def __exit__(self, *exc: object) -> None: ...


def open_database(path: Path, *, enabled: bool = True) -> DatabaseLike:
    """Factory returning a real :class:`Database` or a :class:`NullDatabase`."""
    if not enabled:
        return NullDatabase()
    return Database(path)

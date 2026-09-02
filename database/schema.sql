-- =============================================================================
-- System Maintenance Automation — SQLite schema (reference)
-- -----------------------------------------------------------------------------
-- This file documents the schema created automatically at runtime by
-- src/maintenance/database.py. You do NOT need to run it manually; it is kept
-- here for reference, code review and external tooling (e.g. DB browsers).
-- =============================================================================

-- One row per maintenance run (clean/backup/monitor/disk/scan/verify).
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT    NOT NULL UNIQUE,   -- short hex correlation id
    command       TEXT    NOT NULL,
    environment   TEXT,
    started_at    TEXT    NOT NULL,          -- ISO-8601 UTC
    finished_at   TEXT,
    duration_s    REAL,
    status        TEXT    NOT NULL DEFAULT 'running',  -- running | ok | error
    health_score  INTEGER,                   -- 0..100 (monitor runs)
    dry_run       INTEGER NOT NULL DEFAULT 0
);

-- Aggregated cleanup outcomes per run.
CREATE TABLE IF NOT EXISTS cleanup_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT NOT NULL,
    files_deleted      INTEGER NOT NULL DEFAULT 0,
    bytes_reclaimed    INTEGER NOT NULL DEFAULT 0,
    duplicates_removed INTEGER NOT NULL DEFAULT 0,
    empty_dirs_removed INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL
);

-- One row per archive created.
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

-- Per-volume disk usage captured during monitor/disk runs.
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

-- Errors recorded during any run.
CREATE TABLE IF NOT EXISTS errors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT,
    module     TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Report files written for a run.
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

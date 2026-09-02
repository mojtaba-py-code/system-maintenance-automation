"""Tests for the cleanup engine."""

from __future__ import annotations

from pathlib import Path

from maintenance.cleanup import CleanupEngine
from maintenance.config import Settings


def _engine(settings: Settings, *, dry_run: bool = False) -> CleanupEngine:
    return CleanupEngine(
        settings.cleanup,
        settings.security,
        quarantine_dir=settings.quarantine_dir,
        dry_run=dry_run,
    )


def test_clean_temp_removes_matching(settings: Settings, junk_dir: Path) -> None:
    engine = _engine(settings)
    result = engine.clean_temp()
    assert result.files_deleted == 5  # a.tmp, b.temp, dup1.tmp, dup2.tmp, nested/c.bak
    assert result.bytes_reclaimed > 0
    assert not (junk_dir / "a.tmp").exists()
    assert (junk_dir / "keep.txt").exists()  # non-temp preserved


def test_clean_temp_quarantines(settings: Settings, junk_dir: Path) -> None:
    engine = _engine(settings)
    engine.clean_temp()
    quarantined = list(settings.quarantine_dir.glob("*"))
    assert quarantined, "files should be moved to quarantine, not destroyed"


def test_dry_run_deletes_nothing(settings: Settings, junk_dir: Path) -> None:
    engine = _engine(settings, dry_run=True)
    result = engine.clean_temp()
    assert result.dry_run is True
    assert result.files_deleted == 5
    # Nothing actually removed.
    assert (junk_dir / "a.tmp").exists()


def test_remove_duplicates_keeps_one(settings: Settings, junk_dir: Path) -> None:
    engine = _engine(settings)
    result = engine.remove_duplicates([str(junk_dir)])
    assert result.duplicates_removed == 1
    remaining = [p.name for p in junk_dir.glob("dup*.tmp")]
    assert len(remaining) == 1


def test_remove_empty_dirs(settings: Settings, junk_dir: Path) -> None:
    engine = _engine(settings)
    result = engine.remove_empty_dirs([str(junk_dir)])
    assert result.empty_dirs_removed >= 1
    assert not (junk_dir / "empty").exists()


def test_protected_files_skipped(settings: Settings, junk_dir: Path) -> None:
    # Protect the junk dir entirely; nothing should be deleted.
    settings.security.protected_paths = [str(junk_dir)]
    engine = _engine(settings)
    result = engine.clean_temp()
    assert result.files_deleted == 0
    assert result.skipped >= 5


def test_run_orchestration(settings: Settings, junk_dir: Path) -> None:
    engine = _engine(settings)
    result = engine.run(temp=True, duplicates=True, empty_dirs=True)
    assert result.files_deleted >= 5
    payload = result.to_dict()
    assert "bytes_reclaimed_human" in payload


# --------------------------------------------------------------------------- #
# Deletion budget (security.max_delete_batch)
# --------------------------------------------------------------------------- #
def test_batch_limit_stops_a_runaway_cleanup(settings: Settings, junk_dir: Path) -> None:
    settings.security.max_delete_batch = 2
    result = _engine(settings).clean_temp()
    assert result.files_deleted == 2
    assert result.batch_limit_reached is True
    assert any("batch limit" in e for e in result.errors)
    # Everything past the cap is left untouched, not silently dropped.
    assert result.skipped >= 1


def test_batch_limit_applies_to_dry_run_previews(settings: Settings) -> None:
    settings.security.max_delete_batch = 3
    result = _engine(settings, dry_run=True).clean_temp()
    assert result.files_deleted == 3
    assert result.batch_limit_reached is True


def test_batch_limit_spans_a_whole_run(settings: Settings) -> None:
    # The budget is per-run, not per-operation: two operations share one cap.
    settings.security.max_delete_batch = 3
    result = _engine(settings).run(temp=True, duplicates=True, empty_dirs=True)
    assert result.files_deleted == 3


def test_batch_counter_resets_between_runs(settings: Settings) -> None:
    settings.security.max_delete_batch = 2
    engine = _engine(settings, dry_run=True)
    first = engine.run(temp=True)
    second = engine.run(temp=True)
    assert first.files_deleted == 2
    assert second.files_deleted == 2, "a fresh run gets a fresh budget"


# --------------------------------------------------------------------------- #
# Quarantine retention (cleanup.quarantine_retention_days)
# --------------------------------------------------------------------------- #
def _age_file(path: Path, days: float) -> None:
    """Backdate a file's mtime by ``days``."""
    import os
    import time

    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def test_purge_quarantine_removes_only_expired_entries(settings: Settings) -> None:
    settings.cleanup.quarantine_retention_days = 7
    quarantine = settings.quarantine_dir
    quarantine.mkdir(parents=True, exist_ok=True)
    stale = quarantine / "old.tmp"
    fresh = quarantine / "recent.tmp"
    stale.write_text("expired")
    fresh.write_text("still recoverable")
    _age_file(stale, 10)

    result = _engine(settings).purge_quarantine()

    assert result.quarantine_purged == 1
    assert not stale.exists()
    assert fresh.exists(), "files inside the recovery window must survive"


def test_purge_quarantine_honours_dry_run(settings: Settings) -> None:
    settings.cleanup.quarantine_retention_days = 1
    quarantine = settings.quarantine_dir
    quarantine.mkdir(parents=True, exist_ok=True)
    stale = quarantine / "old.tmp"
    stale.write_text("expired")
    _age_file(stale, 5)

    result = _engine(settings, dry_run=True).purge_quarantine()

    assert result.quarantine_purged == 1
    assert stale.exists()


def test_purge_quarantine_removes_expired_directories(settings: Settings) -> None:
    settings.cleanup.quarantine_retention_days = 2
    quarantine = settings.quarantine_dir
    quarantine.mkdir(parents=True, exist_ok=True)
    tree = quarantine / "old_tree"
    tree.mkdir()
    (tree / "inner.txt").write_text("nested")
    _age_file(tree, 30)

    assert _engine(settings).purge_quarantine().quarantine_purged == 1
    assert not tree.exists()


def test_run_purges_quarantine_as_part_of_cleanup(settings: Settings) -> None:
    settings.cleanup.quarantine_retention_days = 3
    quarantine = settings.quarantine_dir
    quarantine.mkdir(parents=True, exist_ok=True)
    stale = quarantine / "ancient.tmp"
    stale.write_text("expired")
    _age_file(stale, 90)

    result = _engine(settings).run(temp=True)

    assert result.quarantine_purged == 1
    assert not stale.exists()


def test_files_quarantined_by_this_run_keep_their_window(settings: Settings, junk_dir: Path) -> None:
    settings.cleanup.quarantine_retention_days = 7
    result = _engine(settings).run(temp=True)
    assert result.files_deleted > 0
    assert result.quarantine_purged == 0
    assert any(settings.quarantine_dir.iterdir()), "just-quarantined files stay recoverable"

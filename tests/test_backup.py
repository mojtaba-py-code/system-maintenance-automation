"""Tests for the backup engine."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from maintenance.backup import BackupEngine, _slugify
from maintenance.config import Settings


def _engine(settings: Settings, *, dry_run: bool = False) -> BackupEngine:
    return BackupEngine(
        settings.backup,
        settings.security,
        destination=settings.backup_dir,
        dry_run=dry_run,
    )


def test_backup_creates_verified_archive(settings: Settings, source_dir: Path) -> None:
    engine = _engine(settings)
    result = engine.backup_source(str(source_dir))
    assert result.archive_path is not None
    assert result.files_count == 3
    assert result.verified is True
    archive = Path(result.archive_path)
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        assert zf.testzip() is None
        assert len(zf.namelist()) == 3


def test_incremental_skips_unchanged(settings: Settings, source_dir: Path) -> None:
    engine = _engine(settings)
    first = engine.backup_source(str(source_dir))
    assert first.files_count == 3
    second = engine.backup_source(str(source_dir))
    assert second.files_count == 0
    assert second.skipped_unchanged == 3
    assert second.archive_path is None


def test_incremental_detects_change(settings: Settings, source_dir: Path) -> None:
    engine = _engine(settings)
    engine.backup_source(str(source_dir))
    (source_dir / "doc1.txt").write_text("changed content")
    third = engine.backup_source(str(source_dir))
    assert third.files_count == 1


def test_dry_run_creates_no_archive(settings: Settings, source_dir: Path) -> None:
    engine = _engine(settings, dry_run=True)
    result = engine.backup_source(str(source_dir))
    assert result.files_count == 3
    assert not list(settings.backup_dir.glob("*.zip"))


def test_missing_source_reports_error(settings: Settings, tmp_path: Path) -> None:
    engine = _engine(settings)
    result = engine.backup_source(str(tmp_path / "nope"))
    assert result.errors
    assert result.archive_path is None


def test_rotation_removes_old(settings: Settings, source_dir: Path) -> None:
    engine = _engine(settings)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(source_dir.resolve())
    # Fabricate 6 archives; keep_last=3 so 3 should be rotated out.
    for i in range(6):
        (settings.backup_dir / f"{slug}_2020010{i}T000000Z.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    removed = engine._rotate(source_dir.resolve())
    assert removed == 3
    assert len(list(settings.backup_dir.glob(f"{slug}_*.zip"))) == 3


# --------------------------------------------------------------------------- #
# Verification integrity
# --------------------------------------------------------------------------- #
def test_tampered_archive_fails_verification(settings: Settings, source_dir: Path) -> None:
    engine = _engine(settings)
    members, _ = engine._select_files(source_dir, {})
    archive = settings.backup_dir
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / "tampered.zip"
    with zipfile.ZipFile(target, "w") as zf:
        for _abs, arcname, _digest in members:
            zf.writestr(arcname, "content that does not match the recorded digest")
    assert engine._verify_archive(target, members) is False


def test_unverified_archive_is_set_aside(
    settings: Settings, source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(settings)
    # Force verification to fail without corrupting anything on disk.
    monkeypatch.setattr(BackupEngine, "_verify_archive", lambda self, archive, members: False)
    result = engine.backup_source(str(source_dir))

    assert result.verified is False
    assert result.archive_path is not None
    assert result.archive_path.endswith(".corrupt"), (
        "an archive that failed verification must not look like a good backup"
    )
    assert Path(result.archive_path).exists()
    # It must also be invisible to rotation, which only globs the real suffix.
    assert not list(settings.backup_dir.glob(f"{_slugify(source_dir)}_*.zip"))


def test_verification_streams_large_members(settings: Settings, tmp_path: Path) -> None:
    """A member larger than the hash chunk size must verify without buffering it."""
    big_source = tmp_path / "big_src"
    big_source.mkdir()
    # Comfortably more than the 1 MiB hashing chunk, so multiple reads happen.
    (big_source / "large.bin").write_bytes(b"x" * (3 * 1024 * 1024 + 17))

    settings.backup.sources = [str(big_source)]
    result = _engine(settings).backup_source(str(big_source))

    assert result.verified is True
    assert result.files_count == 1


def test_pre_1980_timestamps_do_not_abort_a_backup(settings: Settings, tmp_path: Path) -> None:
    """ZIP cannot represent pre-1980 dates; one odd file must not fail the run."""
    import os

    odd_source = tmp_path / "old_src"
    odd_source.mkdir()
    ancient = odd_source / "ancient.txt"
    ancient.write_text("from before the ZIP epoch")
    (odd_source / "normal.txt").write_text("ordinary file")
    os.utime(ancient, (0, 0))  # 1970-01-01

    result = _engine(settings).backup_source(str(odd_source))

    assert result.errors == []
    assert result.files_count == 2
    assert result.verified is True


def test_failed_archive_creation_leaves_no_stub(
    settings: Settings, source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: BackupEngine, archive: Path, members: list[object]) -> None:
        raise OSError("disk full")

    engine = _engine(settings)
    monkeypatch.setattr(BackupEngine, "_create_archive", explode)
    result = engine.backup_source(str(source_dir))

    assert result.errors and "Archive creation failed" in result.errors[0]
    assert not list(settings.backup_dir.glob("*.zip")), "no truncated archive left behind"


def test_rotation_tolerates_a_vanished_archive(settings: Settings, source_dir: Path) -> None:
    """A concurrent process removing an archive mid-scan must not raise."""
    settings.backup.keep_last = 1
    engine = _engine(settings)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    prefix = _slugify(source_dir)
    for stamp in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z"):
        (settings.backup_dir / f"{prefix}_{stamp}.zip").write_bytes(b"")

    assert engine._rotate(source_dir) == 2
    assert len(list(settings.backup_dir.glob(f"{prefix}_*.zip"))) == 1

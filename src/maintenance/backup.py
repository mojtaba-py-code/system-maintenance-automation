"""Backup engine: incremental, compressed, verified, rotated.

Features
--------
* **Compressed archives** -- ``zip`` (deflate) or ``tar.gz`` / ``tar.bz2``.
* **Incremental** -- a per-source SHA-256 manifest records the last-archived
  digest of every file; only new/changed files are re-archived.
* **Verification** -- after writing, every member is read back out of the
  archive and its digest compared against the source; a mismatch fails the run.
* **Rotation** -- only the newest ``keep_last`` archives per source are kept.

All timestamps are UTC. Archive names embed the source slug and an ISO-ish
stamp so they sort chronologically.
"""

from __future__ import annotations

import json
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import BackupConfig, SecurityConfig, expand_path
from .hashing import hash_stream, try_hash_file
from .logger import audit, get_logger
from .security import PathGuard
from .utils import human_size, iter_files, matches_any, safe_stat_size, utcnow

logger = get_logger("backup")

_EXTENSIONS = {"zip": ".zip", "tar.gz": ".tar.gz", "tar.bz2": ".tar.bz2"}


@dataclass(slots=True)
class BackupResult:
    """Outcome of backing up a single source."""

    source: str
    archive_path: str | None = None
    files_count: int = 0
    bytes_total: int = 0
    incremental: bool = False
    verified: bool = False
    skipped_unchanged: int = 0
    rotated: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "archive_path": self.archive_path,
            "files_count": self.files_count,
            "bytes_total": self.bytes_total,
            "bytes_total_human": human_size(self.bytes_total),
            "incremental": self.incremental,
            "verified": self.verified,
            "skipped_unchanged": self.skipped_unchanged,
            "rotated": self.rotated,
            "errors": list(self.errors),
        }


def _slugify(path: Path) -> str:
    """Produce a filesystem-safe slug from a source path."""
    text = str(path).replace(":", "").strip("\\/")
    safe = "".join(c if c.isalnum() else "-" for c in text).strip("-")
    return safe or "root"


class BackupEngine:
    """Creates verified, rotating backups of configured source directories."""

    def __init__(
        self,
        backup_cfg: BackupConfig,
        security_cfg: SecurityConfig,
        *,
        destination: Path,
        guard: PathGuard | None = None,
        dry_run: bool = False,
    ) -> None:
        self._cfg = backup_cfg
        self._guard = guard or PathGuard(security_cfg)
        self._destination = destination
        self._manifest_dir = destination / ".manifests"
        self._dry_run = dry_run
        self._follow_symlinks = security_cfg.follow_symlinks

    # -- manifest ---------------------------------------------------------- #
    def _manifest_path(self, source: Path) -> Path:
        return self._manifest_dir / f"{_slugify(source)}.json"

    def _load_manifest(self, source: Path) -> dict[str, str]:
        path = self._manifest_path(source)
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("files", {}) if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_manifest(self, source: Path, files: dict[str, str]) -> None:
        if self._dry_run:
            return
        self._manifest_dir.mkdir(parents=True, exist_ok=True)
        payload = {"source": str(source), "updated_at": utcnow().isoformat(), "files": files}
        self._manifest_path(source).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    # -- selection --------------------------------------------------------- #
    def _select_files(
        self, source: Path, manifest: dict[str, str]
    ) -> tuple[list[tuple[Path, str, str]], int]:
        """Return ``[(abs_path, arcname, digest)]`` plus a skipped count.

        In incremental mode, files whose digest matches the manifest are
        skipped. ``arcname`` is the path relative to the source's parent.
        """
        selected: list[tuple[Path, str, str]] = []
        skipped = 0
        anchor = source.parent
        for file in iter_files(
            source,
            exclude_patterns=self._cfg.exclude_patterns,
            follow_symlinks=self._follow_symlinks,
        ):
            if matches_any(file.name, self._cfg.exclude_patterns):
                continue
            digest = try_hash_file(file)
            if digest is None:
                continue
            # Use POSIX separators so archive member names are consistent with
            # how zipfile/tarfile store them (avoids '\\' vs '/' mismatches on
            # Windows during verification and extraction).
            arcname = file.relative_to(anchor).as_posix()
            if self._cfg.incremental and manifest.get(arcname) == digest:
                skipped += 1
                continue
            selected.append((file, arcname, digest))
        return selected, skipped

    # -- archive writers --------------------------------------------------- #
    def _write_zip(self, archive: Path, members: list[tuple[Path, str, str]]) -> None:
        compress = zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(
            archive,
            "w",
            compression=compress,
            compresslevel=self._cfg.compression_level,
            allowZip64=True,
            # ZIP timestamps cannot represent dates before 1980. Rather than
            # abort a whole backup over one file with a bogus mtime, clamp it.
            strict_timestamps=False,
        ) as zf:
            for abs_path, arcname, _ in members:
                zf.write(abs_path, arcname)

    def _write_tar(
        self,
        archive: Path,
        members: list[tuple[Path, str, str]],
        mode: Literal["w:gz", "w:bz2"],
    ) -> None:
        with tarfile.open(archive, mode) as tf:
            for abs_path, arcname, _ in members:
                tf.add(abs_path, arcname=arcname, recursive=False)

    def _create_archive(self, archive: Path, members: list[tuple[Path, str, str]]) -> None:
        fmt = self._cfg.archive_format
        if fmt == "zip":
            self._write_zip(archive, members)
        elif fmt == "tar.gz":
            self._write_tar(archive, members, "w:gz")
        elif fmt == "tar.bz2":
            self._write_tar(archive, members, "w:bz2")
        else:  # pragma: no cover - validated by config
            raise ValueError(f"Unsupported archive format: {fmt}")

    # -- verification ------------------------------------------------------ #
    def _verify_archive(self, archive: Path, members: list[tuple[Path, str, str]]) -> bool:
        """Re-hash each stored member and compare against the recorded digest.

        Members are hashed as a *stream*: verifying a multi-gigabyte archive
        must not require holding a member in memory, or backing up large files
        would fail exactly on the machines that most need backing up.
        """
        expected = {arcname: digest for _, arcname, digest in members}
        try:
            if self._cfg.archive_format == "zip":
                with zipfile.ZipFile(archive, "r") as zf:
                    if zf.testzip() is not None:
                        return False
                    for arcname, digest in expected.items():
                        with zf.open(arcname, "r") as stored:
                            if hash_stream(stored) != digest:
                                return False
            else:
                mode: Literal["r:gz", "r:bz2"] = (
                    "r:gz" if self._cfg.archive_format == "tar.gz" else "r:bz2"
                )
                with tarfile.open(archive, mode) as tf:
                    for arcname, digest in expected.items():
                        extracted = tf.extractfile(arcname)
                        if extracted is None:
                            return False
                        with extracted:
                            if hash_stream(extracted) != digest:
                                return False
        except (OSError, KeyError, zipfile.BadZipFile, tarfile.TarError) as exc:
            logger.warning("Verification error for %s: %s", archive, exc)
            return False
        return True

    # -- rotation ---------------------------------------------------------- #
    def _rotate(self, source: Path) -> int:
        """Delete archives beyond ``keep_last`` for this source; return count.

        Sorting tolerates archives that vanish mid-scan (a concurrent run, a
        sync client) -- an unreadable entry sorts oldest and is rotated first.
        """
        ext = _EXTENSIONS[self._cfg.archive_format]
        prefix = _slugify(source)

        def mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        archives = sorted(
            self._destination.glob(f"{prefix}_*{ext}"),
            key=mtime,
            reverse=True,
        )
        removed = 0
        for old in archives[self._cfg.keep_last :]:
            if self._dry_run:
                removed += 1
                continue
            try:
                old.unlink()
                removed += 1
                audit("backup-rotate", path=str(old))
            except OSError as exc:
                logger.warning("Failed to rotate out %s: %s", old, exc)
        return removed

    # -- public API -------------------------------------------------------- #
    def backup_source(self, raw_source: str) -> BackupResult:
        """Back up a single source directory."""
        source = expand_path(raw_source)
        result = BackupResult(source=str(source), incremental=self._cfg.incremental)
        if not source.exists():
            result.errors.append(f"Source does not exist: {source}")
            logger.warning("Backup source missing: %s", source)
            return result

        manifest = self._load_manifest(source)
        members, skipped = self._select_files(source, manifest)
        result.skipped_unchanged = skipped

        if not members:
            logger.info("No changed files to back up for %s", source)
            result.verified = True
            return result

        self._destination.mkdir(parents=True, exist_ok=True)
        stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
        ext = _EXTENSIONS[self._cfg.archive_format]
        archive = self._destination / f"{_slugify(source)}_{stamp}{ext}"
        result.files_count = len(members)
        result.bytes_total = sum(safe_stat_size(p) for p, _, _ in members)

        if self._dry_run:
            result.archive_path = str(archive)
            logger.info(
                "[dry-run] would archive %d files (%s) from %s",
                result.files_count,
                human_size(result.bytes_total),
                source,
            )
            return result

        try:
            self._create_archive(archive, members)
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
            result.errors.append(f"Archive creation failed: {exc}")
            logger.error("Backup failed for %s: %s", source, exc)
            # Never leave a truncated archive behind: a half-written file that
            # looks like a backup is worse than no backup at all.
            archive.unlink(missing_ok=True)
            return result

        result.archive_path = str(archive)
        audit("backup", source=str(source), archive=str(archive), files=result.files_count)

        if self._cfg.verify_after_backup:
            result.verified = self._verify_archive(archive, members)
            if not result.verified:
                # Move it out of the way: an archive that failed verification
                # must never be mistaken for a good one, nor occupy a slot in
                # the keep_last rotation. The bytes are kept for diagnosis.
                quarantined = archive.with_suffix(archive.suffix + ".corrupt")
                try:
                    archive.replace(quarantined)
                    result.archive_path = str(quarantined)
                except OSError as exc:  # pragma: no cover - defensive
                    logger.warning("Could not set aside unverified archive: %s", exc)
                result.errors.append("Backup verification failed")
                logger.error("Verification FAILED for %s", archive)
                return result
        else:
            result.verified = False

        # Commit manifest only after a successful (and verified) write.
        for _, arcname, digest in members:
            manifest[arcname] = digest
        self._save_manifest(source, manifest)
        result.rotated = self._rotate(source)

        logger.info(
            "Backup complete: %s (%d files, %s%s)",
            archive.name,
            result.files_count,
            human_size(result.bytes_total),
            ", verified" if result.verified else "",
        )
        return result

    def run(self) -> list[BackupResult]:
        """Back up every configured source; returns one result per source."""
        results: list[BackupResult] = []
        for raw in self._cfg.sources:
            results.append(self.backup_source(raw))
        return results

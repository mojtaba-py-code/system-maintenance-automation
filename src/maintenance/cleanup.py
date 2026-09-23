"""Safe cleanup engine.

Removes temporary files, stale logs, duplicate files and empty directories --
always through :class:`~maintenance.security.PathGuard`, always honouring
*dry-run*, and (by default) moving deletions to a **quarantine** folder rather
than unlinking them so a mistake can be undone.

Design notes
------------
* Nothing is deleted without passing ``guard.check_deletable``.
* ``dry_run`` short-circuits every mutating operation but still reports what
  *would* happen -- so operators can preview before committing.
* Duplicate detection groups by size first (cheap) and only hashes files whose
  sizes collide, keeping the oldest copy as the canonical one.
* ``security.max_delete_batch`` caps how many files a single run may remove.
  A misconfigured glob that suddenly matches 400,000 files stops at the cap
  and reports it, instead of running to completion before anyone notices.
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import CleanupConfig, SecurityConfig, expand_path
from .hashing import hash_many
from .logger import audit, get_logger
from .security import PathGuard, SecurityError
from .utils import (
    age_in_days,
    human_size,
    iter_files,
    matches_any,
    safe_stat_size,
    utcnow,
)

logger = get_logger("cleanup")


@dataclass(slots=True)
class CleanupResult:
    """Aggregated outcome of a cleanup run."""

    dry_run: bool = False
    files_deleted: int = 0
    bytes_reclaimed: int = 0
    duplicates_removed: int = 0
    empty_dirs_removed: int = 0
    skipped: int = 0
    quarantine_purged: int = 0
    batch_limit_reached: bool = False
    errors: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)

    def merge(self, other: CleanupResult) -> None:
        self.files_deleted += other.files_deleted
        self.bytes_reclaimed += other.bytes_reclaimed
        self.duplicates_removed += other.duplicates_removed
        self.empty_dirs_removed += other.empty_dirs_removed
        self.skipped += other.skipped
        self.quarantine_purged += other.quarantine_purged
        self.batch_limit_reached = self.batch_limit_reached or other.batch_limit_reached
        self.errors.extend(other.errors)
        self.actions.extend(other.actions)

    def to_dict(self, *, include_actions: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "dry_run": self.dry_run,
            "files_deleted": self.files_deleted,
            "bytes_reclaimed": self.bytes_reclaimed,
            "bytes_reclaimed_human": human_size(self.bytes_reclaimed),
            "duplicates_removed": self.duplicates_removed,
            "empty_dirs_removed": self.empty_dirs_removed,
            "skipped": self.skipped,
            "quarantine_purged": self.quarantine_purged,
            "batch_limit_reached": self.batch_limit_reached,
            "errors": list(self.errors),
        }
        if include_actions:
            data["actions"] = self.actions
        return data


class CleanupEngine:
    """Performs cleanup operations under the protection of a :class:`PathGuard`."""

    def __init__(
        self,
        cleanup_cfg: CleanupConfig,
        security_cfg: SecurityConfig,
        *,
        quarantine_dir: Path,
        guard: PathGuard | None = None,
        dry_run: bool = False,
        workers: int = 4,
    ) -> None:
        self._cfg = cleanup_cfg
        self._guard = guard or PathGuard(security_cfg)
        self._quarantine_dir = quarantine_dir
        self._dry_run = dry_run
        self._workers = max(1, workers)
        self._follow_symlinks = security_cfg.follow_symlinks
        self._max_delete_batch = security_cfg.max_delete_batch
        self._deleted_this_run = 0

    # -- batch limiting ---------------------------------------------------- #
    def _budget_exhausted(self, result: CleanupResult) -> bool:
        """True once this run has hit ``security.max_delete_batch``.

        Counted across every operation in a single :meth:`run`, and applied in
        dry-run too so a preview reflects what the real run would actually do.
        """
        if self._deleted_this_run < self._max_delete_batch:
            return False
        if not result.batch_limit_reached:
            result.batch_limit_reached = True
            message = (
                f"Deletion batch limit reached ({self._max_delete_batch} files); "
                "stopping. Raise security.max_delete_batch to continue, or re-run."
            )
            result.errors.append(message)
            logger.warning(message)
        return True

    # -- low-level deletion ------------------------------------------------ #
    def _delete_path(self, path: Path, result: CleanupResult, *, reason: str) -> None:
        """Delete a single file safely, recording the outcome in ``result``."""
        if self._budget_exhausted(result):
            result.skipped += 1
            return
        try:
            # Operate on the validated, resolved path returned by the guard to
            # minimise the check-to-use (TOCTOU) window.
            target = self._guard.check_deletable(path)
        except SecurityError as exc:
            result.skipped += 1
            logger.debug("Skipping protected/invalid path: %s (%s)", path, exc)
            return

        if matches_any(path.name, self._cfg.exclude_patterns):
            result.skipped += 1
            return

        size = safe_stat_size(target)
        if self._dry_run:
            self._deleted_this_run += 1
            result.files_deleted += 1
            result.bytes_reclaimed += size
            result.actions.append(
                {"action": "would-delete", "reason": reason, "path": str(path), "size": size}
            )
            logger.info("[dry-run] would delete %s (%s)", path, human_size(size))
            return

        try:
            if self._cfg.quarantine:
                self._quarantine(target)
                action = "quarantine"
            else:
                target.unlink()
                action = "delete"
            self._deleted_this_run += 1
            result.files_deleted += 1
            result.bytes_reclaimed += size
            result.actions.append(
                {"action": action, "reason": reason, "path": str(path), "size": size}
            )
            audit(action, path=str(path), size=size, reason=reason)
        except OSError as exc:
            msg = f"Failed to delete {path}: {exc}"
            result.errors.append(msg)
            logger.warning(msg)

    def _quarantine(self, path: Path) -> None:
        """Move ``path`` into the quarantine folder, preserving uniqueness."""
        self._quarantine_dir.mkdir(parents=True, exist_ok=True)
        # Derive the destination from the *name* only, so a crafted path can
        # never steer the move outside the quarantine directory.
        target = self._quarantine_dir / Path(path.name).name
        counter = 1
        while target.exists():
            target = self._quarantine_dir / f"{path.stem}_{counter}{path.suffix}"
            counter += 1
        if not PathGuard.is_within(target, self._quarantine_dir):  # pragma: no cover - defensive
            raise SecurityError(f"Quarantine target escapes the quarantine root: {target}")
        shutil.move(str(path), str(target))

    def purge_quarantine(self) -> CleanupResult:
        """Delete quarantined files older than ``quarantine_retention_days``.

        This is what makes quarantine a *recovery window* rather than a folder
        that grows without limit: files stay recoverable for the retention
        period, then are removed for real. A retention of ``0`` means the next
        run clears the quarantine entirely.
        """
        result = CleanupResult(dry_run=self._dry_run)
        if not self._cfg.quarantine or not self._quarantine_dir.is_dir():
            return result
        max_age = self._cfg.quarantine_retention_days
        reference = utcnow()
        for entry in sorted(self._quarantine_dir.iterdir()):
            age = age_in_days(entry, reference=reference)
            if age is None or age < max_age:
                continue
            if self._dry_run:
                result.quarantine_purged += 1
                result.actions.append({"action": "would-purge-quarantine", "path": str(entry)})
                continue
            try:
                if entry.is_symlink() or entry.is_file():
                    entry.unlink(missing_ok=True)
                else:
                    shutil.rmtree(entry, ignore_errors=True)
                result.quarantine_purged += 1
                result.actions.append({"action": "purge-quarantine", "path": str(entry)})
                audit("purge-quarantine", path=str(entry), age_days=round(age, 1))
            except OSError as exc:
                msg = f"Failed to purge quarantined {entry}: {exc}"
                result.errors.append(msg)
                logger.warning(msg)
        if result.quarantine_purged:
            logger.info(
                "Quarantine purge: %d entr%s older than %d day(s) removed",
                result.quarantine_purged,
                "y" if result.quarantine_purged == 1 else "ies",
                max_age,
            )
        return result

    # -- operations -------------------------------------------------------- #
    def clean_temp(self) -> CleanupResult:
        """Remove temporary files from configured temp directories."""
        result = CleanupResult(dry_run=self._dry_run)
        for raw in self._cfg.temp_directories:
            root = expand_path(raw)
            if not root.is_dir():
                continue
            for file in iter_files(
                root,
                exclude_dirs=self._cfg.exclude_dirs,
                exclude_patterns=self._cfg.exclude_patterns,
                follow_symlinks=self._follow_symlinks,
            ):
                suffixes = "".join(file.suffixes).lower()
                if file.suffix.lower() in self._cfg.temp_extensions or any(
                    suffixes.endswith(ext) for ext in self._cfg.temp_extensions
                ):
                    self._delete_path(file, result, reason="temp")
        logger.info(
            "Temp cleanup: %d files, %s reclaimed%s",
            result.files_deleted,
            human_size(result.bytes_reclaimed),
            " (dry-run)" if self._dry_run else "",
        )
        return result

    def clean_old_logs(self) -> CleanupResult:
        """Remove log files older than ``log_max_age_days``."""
        result = CleanupResult(dry_run=self._dry_run)
        max_age = self._cfg.log_max_age_days
        reference = utcnow()
        for raw in self._cfg.log_directories:
            root = expand_path(raw)
            if not root.is_dir():
                continue
            for file in iter_files(
                root,
                exclude_dirs=self._cfg.exclude_dirs,
                exclude_patterns=self._cfg.exclude_patterns,
                follow_symlinks=self._follow_symlinks,
            ):
                age = age_in_days(file, reference=reference)
                if age is not None and age >= max_age:
                    self._delete_path(file, result, reason="old-log")
        logger.info("Old-log cleanup: %d files removed", result.files_deleted)
        return result

    def remove_duplicates(self, roots: list[str] | None = None) -> CleanupResult:
        """Detect and remove duplicate files, keeping the oldest copy.

        Files are grouped by size, then hashed only within size-collision groups
        to minimise I/O.
        """
        result = CleanupResult(dry_run=self._dry_run)
        search_roots = [expand_path(r) for r in (roots or self._cfg.temp_directories)]
        by_size: dict[int, list[Path]] = defaultdict(list)
        for root in search_roots:
            if not root.is_dir():
                continue
            for file in iter_files(
                root,
                exclude_dirs=self._cfg.exclude_dirs,
                exclude_patterns=self._cfg.exclude_patterns,
                follow_symlinks=self._follow_symlinks,
            ):
                size = safe_stat_size(file)
                if size > 0:
                    by_size[size].append(file)

        candidates = [f for group in by_size.values() if len(group) > 1 for f in group]
        if not candidates:
            return result

        digests = hash_many(candidates, workers=self._workers)
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for file, digest in digests.items():
            if digest is not None:
                by_hash[digest].append(file)

        for group in by_hash.values():
            if len(group) < 2:
                continue
            # Keep the oldest file (smallest mtime); remove the rest.
            group.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0)
            for duplicate in group[1:]:
                before = result.files_deleted
                self._delete_path(duplicate, result, reason="duplicate")
                if result.files_deleted > before:
                    result.duplicates_removed += 1
        logger.info("Duplicate cleanup: %d duplicates removed", result.duplicates_removed)
        return result

    def remove_empty_dirs(self, roots: list[str] | None = None) -> CleanupResult:
        """Remove directories that contain no files (bottom-up)."""
        result = CleanupResult(dry_run=self._dry_run)
        if not self._cfg.remove_empty_dirs:
            return result
        search_roots = [expand_path(r) for r in (roots or self._cfg.temp_directories)]
        for root in search_roots:
            if not root.is_dir():
                continue
            # Walk deepest-first so parents become empty after children go.
            dirs = sorted(
                (p for p in root.rglob("*") if p.is_dir()),
                key=lambda p: len(p.parts),
                reverse=True,
            )
            for directory in dirs:
                if directory.name.lower() in {d.lower() for d in self._cfg.exclude_dirs}:
                    continue
                try:
                    if any(directory.iterdir()):
                        continue
                    self._guard.check_deletable(directory)
                except (OSError, SecurityError):
                    result.skipped += 1
                    continue
                if self._dry_run:
                    result.empty_dirs_removed += 1
                    result.actions.append({"action": "would-rmdir", "path": str(directory)})
                    continue
                try:
                    directory.rmdir()
                    result.empty_dirs_removed += 1
                    result.actions.append({"action": "rmdir", "path": str(directory)})
                    audit("rmdir", path=str(directory))
                except OSError as exc:
                    msg = f"Failed to remove empty dir {directory}: {exc}"
                    result.errors.append(msg)
                    logger.warning(msg)
        logger.info("Empty-dir cleanup: %d directories removed", result.empty_dirs_removed)
        return result

    def empty_recycle_bin(self) -> CleanupResult:
        """Empty the OS recycle bin / trash (best-effort, platform-specific)."""
        result = CleanupResult(dry_run=self._dry_run)
        if self._dry_run:
            result.actions.append({"action": "would-empty-recycle-bin"})
            return result
        import os
        import subprocess

        try:
            if os.name == "nt":
                subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "Clear-RecycleBin -Force -ErrorAction SilentlyContinue",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=60,
                )
            else:
                trash = Path.home() / ".local/share/Trash/files"
                if trash.is_dir():
                    for entry in trash.iterdir():
                        # Never traverse a symlink planted in Trash — unlink the
                        # link itself so rmtree can't escape the trash folder.
                        if entry.is_symlink():
                            entry.unlink(missing_ok=True)
                        elif entry.is_dir():
                            shutil.rmtree(entry, ignore_errors=True)
                        else:
                            entry.unlink(missing_ok=True)
            result.actions.append({"action": "empty-recycle-bin"})
            audit("empty-recycle-bin")
        except (OSError, subprocess.SubprocessError) as exc:
            msg = f"Failed to empty recycle bin: {exc}"
            result.errors.append(msg)
            logger.warning(msg)
        return result

    def run(
        self,
        *,
        temp: bool = True,
        old_logs: bool = False,
        duplicates: bool = False,
        empty_dirs: bool = False,
        recycle_bin: bool = False,
    ) -> CleanupResult:
        """Orchestrate a full cleanup according to the requested operations."""
        self._deleted_this_run = 0
        combined = CleanupResult(dry_run=self._dry_run)
        if temp:
            combined.merge(self.clean_temp())
        if old_logs:
            combined.merge(self.clean_old_logs())
        if duplicates:
            combined.merge(self.remove_duplicates())
        if empty_dirs:
            combined.merge(self.remove_empty_dirs())
        if recycle_bin or (self._cfg.empty_recycle_bin and temp):
            combined.merge(self.empty_recycle_bin())
        # Expire the recovery window last. With the default retention, files
        # quarantined by this run are far too young to be swept up here; with a
        # retention of 0 the operator has asked for no recovery window at all.
        combined.merge(self.purge_quarantine())
        return combined

"""Disk usage analysis: storage reports, large-file and old-file detection.

Complements :mod:`maintenance.monitor` (which reports *volume-level* usage) by
walking a directory tree to answer "where did my space go?" questions:

* aggregate size and file count of a tree,
* the largest immediate sub-directories,
* files at/above a size threshold,
* files untouched for longer than an age threshold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import DiskConfig, SecurityConfig, expand_path
from .logger import get_logger
from .security import PathGuard
from .utils import age_in_days, file_mtime, human_size, iter_files, safe_stat_size, utcnow

logger = get_logger("disk")


@dataclass(slots=True)
class FileEntry:
    path: str
    size: int
    modified: str | None
    age_days: float | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["size_human"] = human_size(self.size)
        return data


@dataclass(slots=True)
class DirUsage:
    path: str
    size: int
    file_count: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["size_human"] = human_size(self.size)
        return data


@dataclass(slots=True)
class DiskAnalysis:
    root: str
    total_size: int
    file_count: int
    largest_dirs: list[DirUsage] = field(default_factory=list)
    large_files: list[FileEntry] = field(default_factory=list)
    old_files: list[FileEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "total_size": self.total_size,
            "total_size_human": human_size(self.total_size),
            "file_count": self.file_count,
            "largest_dirs": [d.to_dict() for d in self.largest_dirs],
            "large_files": [f.to_dict() for f in self.large_files],
            "old_files": [f.to_dict() for f in self.old_files],
        }


class DiskAnalyzer:
    """Walks directory trees to produce storage analyses."""

    def __init__(
        self,
        disk_cfg: DiskConfig,
        security_cfg: SecurityConfig,
        *,
        guard: PathGuard | None = None,
    ) -> None:
        self._cfg = disk_cfg
        self._guard = guard or PathGuard(security_cfg)
        self._follow_symlinks = security_cfg.follow_symlinks

    def find_large_files(self, root: str | Path, *, limit: int = 100) -> list[FileEntry]:
        """Return files at/above the configured size threshold, largest first."""
        base = expand_path(str(root))
        threshold = self._cfg.large_file_threshold
        entries: list[FileEntry] = []
        for file in iter_files(base, follow_symlinks=self._follow_symlinks):
            size = safe_stat_size(file)
            if size >= threshold:
                mtime = file_mtime(file)
                entries.append(
                    FileEntry(
                        path=str(file),
                        size=size,
                        modified=mtime.isoformat() if mtime else None,
                        age_days=age_in_days(file),
                    )
                )
        entries.sort(key=lambda e: e.size, reverse=True)
        return entries[:limit]

    def find_old_files(self, root: str | Path, *, limit: int = 100) -> list[FileEntry]:
        """Return files older than the configured age threshold, oldest first."""
        base = expand_path(str(root))
        max_age = self._cfg.old_file_age_days
        reference = utcnow()
        entries: list[FileEntry] = []
        for file in iter_files(base, follow_symlinks=self._follow_symlinks):
            age = age_in_days(file, reference=reference)
            if age is not None and age >= max_age:
                mtime = file_mtime(file)
                entries.append(
                    FileEntry(
                        path=str(file),
                        size=safe_stat_size(file),
                        modified=mtime.isoformat() if mtime else None,
                        age_days=round(age, 1),
                    )
                )
        entries.sort(key=lambda e: e.age_days or 0.0, reverse=True)
        return entries[:limit]

    def usage(self, root: str | Path, *, top_dirs: int = 15) -> DiskAnalysis:
        """Aggregate a full analysis of ``root``."""
        base = expand_path(str(root))
        total_size = 0
        file_count = 0
        dir_sizes: dict[Path, list[int]] = {}

        for file in iter_files(base, follow_symlinks=self._follow_symlinks):
            size = safe_stat_size(file)
            total_size += size
            file_count += 1
            # Attribute the file to its top-level sub-directory under base.
            try:
                relative = file.relative_to(base)
            except ValueError:  # pragma: no cover - defensive
                continue
            top = base / relative.parts[0] if relative.parts else base
            bucket = dir_sizes.setdefault(top, [0, 0])
            bucket[0] += size
            bucket[1] += 1

        largest = sorted(
            (DirUsage(str(p), s[0], s[1]) for p, s in dir_sizes.items()),
            key=lambda d: d.size,
            reverse=True,
        )[:top_dirs]

        analysis = DiskAnalysis(
            root=str(base),
            total_size=total_size,
            file_count=file_count,
            largest_dirs=largest,
            large_files=self.find_large_files(base, limit=50),
            old_files=self.find_old_files(base, limit=50),
        )
        logger.info(
            "Disk analysis of %s: %s across %d files",
            base,
            human_size(total_size),
            file_count,
        )
        return analysis

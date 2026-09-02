"""Tests for disk usage analysis."""

from __future__ import annotations

import os
import time
from pathlib import Path

from maintenance.config import Settings
from maintenance.disk import DiskAnalyzer


def _analyzer(settings: Settings) -> DiskAnalyzer:
    return DiskAnalyzer(settings.disk, settings.security)


def test_find_large_files(settings: Settings, source_dir: Path) -> None:
    # Threshold is 1 byte in the test config, so every file qualifies.
    entries = _analyzer(settings).find_large_files(source_dir)
    assert len(entries) == 3
    assert entries[0].size >= entries[-1].size  # sorted descending


def test_usage_totals(settings: Settings, source_dir: Path) -> None:
    analysis = _analyzer(settings).usage(source_dir)
    assert analysis.file_count == 3
    assert analysis.total_size > 0
    data = analysis.to_dict()
    assert "total_size_human" in data


def test_find_old_files(settings: Settings, source_dir: Path) -> None:
    old_time = time.time() - 10 * 86400
    for file in source_dir.rglob("*"):
        if file.is_file():
            os.utime(file, (old_time, old_time))
    entries = _analyzer(settings).find_old_files(source_dir)
    assert len(entries) == 3
    assert all(e.age_days is not None and e.age_days >= 1 for e in entries)


def test_largest_dirs_reported(settings: Settings, source_dir: Path) -> None:
    analysis = _analyzer(settings).usage(source_dir)
    paths = {d.path for d in analysis.largest_dirs}
    # The nested 'reports' subdirectory should be attributed.
    assert any("reports" in p for p in paths)

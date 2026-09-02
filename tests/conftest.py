"""Shared pytest fixtures.

Every test runs against an isolated ``tmp_path`` so nothing touches the real
system. The :func:`settings` fixture builds a fully valid :class:`Settings`
rooted in the temp directory, and :func:`sample_tree` populates a small file
tree for cleanup/backup/disk tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maintenance.config import Settings
from maintenance.service import MaintenanceService


@pytest.fixture
def junk_dir(tmp_path: Path) -> Path:
    """A directory of temporary-looking files, some duplicated."""
    root = tmp_path / "junk"
    root.mkdir()
    (root / "a.tmp").write_text("temporary-a")
    (root / "b.temp").write_text("temporary-b")
    (root / "keep.txt").write_text("keep me")
    (root / "dup1.tmp").write_text("identical-content")
    (root / "dup2.tmp").write_text("identical-content")
    sub = root / "nested"
    sub.mkdir()
    (sub / "c.bak").write_text("backup-file")
    (root / "empty").mkdir()
    return root


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    """A source directory for backup tests."""
    root = tmp_path / "src_data"
    root.mkdir()
    (root / "doc1.txt").write_text("hello world")
    (root / "doc2.txt").write_text("second document")
    sub = root / "reports"
    sub.mkdir()
    (sub / "r.csv").write_text("a,b,c\n1,2,3\n")
    return root


@pytest.fixture
def settings(tmp_path: Path, junk_dir: Path, source_dir: Path) -> Settings:
    """A valid Settings object confined to ``tmp_path``."""
    data = {
        "app": {"data_dir": ".", "environment": "test", "require_confirmation": False},
        "logging": {"console": False, "audit_log": True, "directory": "logs"},
        "database": {"enabled": True, "path": "database/test.db"},
        "security": {"protected_paths": [], "allowed_roots": [str(tmp_path)]},
        "cleanup": {
            "temp_directories": [str(junk_dir)],
            "temp_extensions": [".tmp", ".temp", ".bak"],
            "exclude_patterns": ["*.keep"],
            "quarantine": True,
            "quarantine_dir": "backups/quarantine",
            "remove_empty_dirs": True,
        },
        "backup": {
            "sources": [str(source_dir)],
            "destination": "backups",
            "archive_format": "zip",
            "incremental": True,
            "keep_last": 3,
            "verify_after_backup": True,
        },
        "monitor": {"cpu_sample_interval": 0.05, "top_processes": 5},
        "disk": {"large_file_threshold": 1, "old_file_age_days": 1},
        "report": {"formats": ["json", "html"], "directory": "reports"},
        "base_dir": str(tmp_path),
    }
    return Settings.model_validate(data)


@pytest.fixture
def cli_config(tmp_path: Path) -> str:
    """A minimal, isolated config file so CLI runs never touch the real system."""
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "x.tmp").write_text("temp")
    src = tmp_path / "cli_src"
    src.mkdir()
    (src / "keep.dat").write_text("payload")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "app:\n"
        "  require_confirmation: false\n"
        "logging:\n  console: false\n"
        f"security:\n  allowed_roots: ['{tmp_path.as_posix()}']\n"
        f"cleanup:\n  temp_directories: ['{junk.as_posix()}']\n"
        "  temp_extensions: ['.tmp']\n"
        f"backup:\n  sources: ['{src.as_posix()}']\n"
        "  archive_format: 'zip'\n",
        encoding="utf-8",
    )
    return str(cfg)


@pytest.fixture
def service(settings: Settings) -> MaintenanceService:
    """A ready-to-use service bound to the temp settings."""
    svc = MaintenanceService(settings, configure_logs=False)
    try:
        yield svc
    finally:
        svc.close()

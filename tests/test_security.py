"""Tests for the PathGuard security guardrails."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from maintenance.config import SecurityConfig
from maintenance.security import PathGuard, SecurityError


def test_system_roots_are_protected() -> None:
    guard = PathGuard(SecurityConfig())
    system_path = Path(os.environ.get("SystemRoot", "/etc")) if os.name == "nt" else Path("/etc")
    assert guard.is_protected(system_path)


def test_os_scratch_area_is_not_protected(tmp_path: Path) -> None:
    """Temp/scratch space must stay cleanable on every platform.

    Regression test for macOS: ``tmp_path`` there lives under ``/var/folders``,
    which ``resolve()`` expands to ``/private/var/folders``. Because ``/var`` is
    a protected system root, the guard used to classify the whole macOS
    temp/cache tree as untouchable and refuse every cleanup inside it.
    """
    guard = PathGuard(SecurityConfig())
    assert guard.is_protected(tmp_path / "scratch.tmp") is False


def test_scratch_root_itself_stays_protected() -> None:
    """The carve-out exempts descendants only, never the scratch root."""
    if sys.platform != "darwin":
        pytest.skip("macOS-specific scratch root")
    guard = PathGuard(SecurityConfig())
    assert guard.is_protected(Path("/var/folders")) is True


def test_user_protection_beats_scratch_exemption(tmp_path: Path) -> None:
    """The scratch carve-out must never override an operator's own rule.

    On macOS ``tmp_path`` sits under ``/var/folders``, which built-in system
    protection exempts so the temp tree stays cleanable. A directory the
    operator explicitly protected there must still be refused.
    """
    precious = tmp_path / "precious"
    precious.mkdir()
    guard = PathGuard(SecurityConfig(protected_paths=[str(precious)]))
    assert guard.is_protected(precious) is True
    assert guard.is_protected(precious / "inside.txt") is True
    with pytest.raises(SecurityError):
        guard.check_deletable(precious / "inside.txt")


def test_user_protected_paths(tmp_path: Path) -> None:
    protected = tmp_path / "precious"
    protected.mkdir()
    guard = PathGuard(SecurityConfig(protected_paths=[str(protected)]))
    assert guard.is_protected(protected)
    assert guard.is_protected(protected / "inside.txt")  # descendant
    assert guard.is_protected(tmp_path)  # ancestor


def test_check_deletable_rejects_protected(tmp_path: Path) -> None:
    protected = tmp_path / "keep"
    protected.mkdir()
    guard = PathGuard(SecurityConfig(protected_paths=[str(protected)]))
    with pytest.raises(SecurityError):
        guard.check_deletable(protected / "file.txt")


def test_allowed_roots_enforced(tmp_path: Path) -> None:
    allowed = tmp_path / "workspace"
    allowed.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    guard = PathGuard(SecurityConfig(allowed_roots=[str(allowed)]))
    assert guard.within_allowed_roots(allowed / "ok.txt")
    assert not guard.within_allowed_roots(outside / "no.txt")
    with pytest.raises(SecurityError):
        guard.check_deletable(outside / "no.txt")


def test_is_within_blocks_traversal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert PathGuard.is_within(root / "a" / "b.txt", root)
    assert not PathGuard.is_within(root / ".." / "escape.txt", root)


def test_extraction_guard(tmp_path: Path) -> None:
    guard = PathGuard(SecurityConfig())
    root = tmp_path / "extract"
    root.mkdir()
    guard.assert_extraction_safe(root / "safe.txt", root)
    with pytest.raises(SecurityError):
        guard.assert_extraction_safe(tmp_path / "evil.txt", root)


def test_deletable_allows_normal_file(tmp_path: Path) -> None:
    guard = PathGuard(SecurityConfig(allowed_roots=[str(tmp_path)]))
    target = tmp_path / "junk.tmp"
    target.write_text("x")
    resolved = guard.check_deletable(target)
    assert resolved == target.resolve()

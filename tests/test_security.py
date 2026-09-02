"""Tests for the PathGuard security guardrails."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from maintenance.config import SecurityConfig
from maintenance.security import PathGuard, SecurityError


def test_system_roots_are_protected() -> None:
    guard = PathGuard(SecurityConfig())
    system_path = Path(os.environ.get("SystemRoot", "/etc")) if os.name == "nt" else Path("/etc")
    assert guard.is_protected(system_path)


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

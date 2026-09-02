"""Tests for hashing utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path

from maintenance.hashing import hash_bytes, hash_file, hash_many, try_hash_file, verify_file


def test_hash_file_matches_hashlib(tmp_path: Path) -> None:
    file = tmp_path / "data.bin"
    payload = b"the quick brown fox" * 1000
    file.write_bytes(payload)
    assert hash_file(file) == hashlib.sha256(payload).hexdigest()


def test_hash_bytes() -> None:
    assert hash_bytes(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_try_hash_missing_returns_none(tmp_path: Path) -> None:
    assert try_hash_file(tmp_path / "nope.bin") is None


def test_hash_many(tmp_path: Path) -> None:
    files = []
    for i in range(3):
        f = tmp_path / f"f{i}.txt"
        f.write_text(f"content-{i}")
        files.append(f)
    result = hash_many(files, workers=2)
    assert len(result) == 3
    assert all(v is not None for v in result.values())


def test_verify_file(tmp_path: Path) -> None:
    file = tmp_path / "x.txt"
    file.write_text("verify me")
    digest = hash_file(file)
    assert verify_file(file, digest)
    assert not verify_file(file, "0" * 64)


def test_identical_content_same_hash(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("same")
    b.write_text("same")
    assert hash_file(a) == hash_file(b)

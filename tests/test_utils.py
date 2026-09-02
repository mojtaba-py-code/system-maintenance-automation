"""Tests for the utility helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from maintenance.utils import (
    Timer,
    age_in_days,
    clamp,
    human_duration,
    human_size,
    iter_files,
    matches_any,
    retry,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0 B"), (512, "512 B"), (1024, "1.00 KiB"), (1536, "1.50 KiB"), (1048576, "1.00 MiB")],
)
def test_human_size(value: int, expected: str) -> None:
    assert human_size(value) == expected


def test_human_size_negative() -> None:
    assert human_size(-1024) == "-1.00 KiB"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5, "500 ms"), (5, "5s"), (65, "1m 05s"), (3665, "1h 01m 05s")],
)
def test_human_duration(value: float, expected: str) -> None:
    assert human_duration(value) == expected


def test_timer_measures_elapsed() -> None:
    with Timer() as timer:
        sum(range(1000))
    assert timer.elapsed >= 0.0


def test_matches_any_case_insensitive() -> None:
    assert matches_any("Report.KEEP", ["*.keep"])
    assert not matches_any("report.txt", ["*.keep"])


def test_iter_files_excludes_dirs(tmp_path: Path) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "a.txt").write_text("a")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    found = {p.name for p in iter_files(tmp_path, exclude_dirs=[".git"])}
    assert found == {"a.txt"}


def test_iter_files_excludes_patterns(tmp_path: Path) -> None:
    (tmp_path / "a.log").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    found = {p.name for p in iter_files(tmp_path, exclude_patterns=["*.log"])}
    assert found == {"b.txt"}


def test_retry_eventually_succeeds() -> None:
    calls = {"n": 0}

    @retry(attempts=3, backoff=0.0)
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("transient")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_reraises_after_exhaustion() -> None:
    @retry(attempts=1, backoff=0.0)
    def always_fail() -> None:
        raise OSError("nope")

    with pytest.raises(OSError, match="nope"):
        always_fail()


def test_age_in_days_positive(tmp_path: Path) -> None:
    file = tmp_path / "old.txt"
    file.write_text("x")
    import os
    import time

    old = time.time() - 3 * 86400
    os.utime(file, (old, old))
    age = age_in_days(file)
    assert age is not None and age >= 2.5


def test_clamp() -> None:
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10

"""Tests for the argparse CLI front-end."""

from __future__ import annotations

from pathlib import Path

import pytest

from maintenance.cli import build_parser, main


def test_parser_requires_command() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


def test_info_command(cli_config: str, tmp_path: Path) -> None:
    code = main(["--config", cli_config, "--data-dir", str(tmp_path), "--silent", "info"])
    assert code == 0


def test_hash_command(cli_config: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "file.txt"
    target.write_text("hash me")
    code = main(["--config", cli_config, "--data-dir", str(tmp_path), "--silent", "hash", str(target)])
    assert code == 0
    out = capsys.readouterr().out
    assert len(out.strip().split()[0]) == 64  # sha256 hex length


def test_clean_dry_run(cli_config: str, tmp_path: Path) -> None:
    code = main(
        ["--config", cli_config, "--data-dir", str(tmp_path), "--silent", "--dry-run", "--no-report", "clean"]
    )
    assert code == 0
    # Dry-run must not delete the temp file.
    assert (tmp_path / "junk" / "x.tmp").exists()


def test_clean_executes(cli_config: str, tmp_path: Path) -> None:
    code = main(
        ["--config", cli_config, "--data-dir", str(tmp_path), "--silent", "--force", "--no-report", "clean"]
    )
    assert code == 0
    assert not (tmp_path / "junk" / "x.tmp").exists()


def test_monitor_command(cli_config: str, tmp_path: Path) -> None:
    code = main(
        ["--config", cli_config, "--data-dir", str(tmp_path), "--silent", "--no-report", "monitor"]
    )
    assert code == 0


def test_report_command(cli_config: str, tmp_path: Path) -> None:
    code = main(["--config", cli_config, "--data-dir", str(tmp_path), "--silent", "report"])
    assert code == 0


def test_scheduler_list(cli_config: str, tmp_path: Path) -> None:
    code = main(["--config", cli_config, "--data-dir", str(tmp_path), "--silent", "scheduler", "--list"])
    assert code == 0


def test_bad_config_returns_one(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("monitor:\n  cpu_threshold: 999\n", encoding="utf-8")
    code = main(["--config", str(bad), "--silent", "info"])
    assert code == 1

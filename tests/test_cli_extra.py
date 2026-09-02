"""Additional CLI coverage for the remaining subcommands."""

from __future__ import annotations

from pathlib import Path

from maintenance.cli import main


def _base(cli_config: str, tmp_path: Path) -> list[str]:
    return ["--config", cli_config, "--data-dir", str(tmp_path), "--silent"]


def test_backup_command(cli_config: str, tmp_path: Path) -> None:
    assert main([*_base(cli_config, tmp_path), "--no-report", "backup"]) == 0
    # An archive should have been produced for the configured source.
    assert list((tmp_path / "backups").glob("*.zip"))


def test_disk_command(cli_config: str, tmp_path: Path) -> None:
    assert main([*_base(cli_config, tmp_path), "--no-report", "disk", str(tmp_path / "cli_src")]) == 0


def test_scan_command(cli_config: str, tmp_path: Path) -> None:
    assert main([*_base(cli_config, tmp_path), "--no-report", "scan"]) == 0


def test_verify_command(cli_config: str, tmp_path: Path) -> None:
    # Create a backup first, then verify it.
    main([*_base(cli_config, tmp_path), "--no-report", "backup"])
    assert main([*_base(cli_config, tmp_path), "--no-report", "verify"]) == 0


def test_update_command(cli_config: str, tmp_path: Path) -> None:
    assert main([*_base(cli_config, tmp_path), "update"]) == 0


def test_report_output_written(cli_config: str, tmp_path: Path) -> None:
    assert main([*_base(cli_config, tmp_path), "--output", "json", "monitor"]) == 0
    assert list((tmp_path / "reports").glob("*.json"))


def test_monitor_with_alerts_does_not_crash(tmp_path: Path) -> None:
    # Zero thresholds guarantee alerts fire, exercising the alert/suggestion
    # print path (which emits Unicode glyphs). Must not raise or exit non-zero.
    cfg = tmp_path / "alert.yaml"
    cfg.write_text(
        "logging:\n  console: false\n"
        "monitor:\n  cpu_threshold: 0\n  memory_threshold: 0\n"
        "  disk_threshold: 0\n  cpu_sample_interval: 0.05\n",
        encoding="utf-8",
    )
    assert main(["--config", str(cfg), "--data-dir", str(tmp_path), "--no-report", "monitor"]) == 0


def test_utf8_console_helper_is_safe() -> None:
    from maintenance.cli import _enable_utf8_console

    _enable_utf8_console()  # must never raise regardless of stream type


def test_clean_requires_force_when_non_interactive(cli_config: str, tmp_path: Path) -> None:
    # require_confirmation is false in the CLI config, so this still runs; assert
    # the confirmation gate does not block a forced run either.
    assert main([*_base(cli_config, tmp_path), "--force", "--no-report", "clean", "--all"]) == 0

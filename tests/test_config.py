"""Tests for configuration loading, validation and env resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from maintenance.config import (
    ConfigError,
    Settings,
    _resolve_env_placeholders,
    expand_path,
    load_settings,
)


def test_defaults_build_without_file() -> None:
    settings = load_settings(None)
    assert settings.app.environment == "production"
    assert settings.monitor.cpu_threshold == 85.0
    assert settings.performance.worker_count >= 1


def test_env_placeholder_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "s3cr3t")
    resolved = _resolve_env_placeholders({"token": "${ENV:MY_SECRET}", "n": 1})
    assert resolved == {"token": "s3cr3t", "n": 1}


def test_env_placeholder_missing_is_blank() -> None:
    assert _resolve_env_placeholders("${ENV:DEFINITELY_NOT_SET_1234}") == ""


def test_load_yaml_file(tmp_path: Path) -> None:
    cfg = tmp_path / "config" / "config.yaml"
    cfg.parent.mkdir()
    cfg.write_text(
        "app:\n  environment: staging\nmonitor:\n  cpu_threshold: 50\n",
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.app.environment == "staging"
    assert settings.monitor.cpu_threshold == 50.0
    # base_dir resolves to the parent of the config/ directory.
    assert settings.base_dir == tmp_path.resolve()


def test_invalid_value_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("monitor:\n  cpu_threshold: 500\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(cfg)


def test_missing_file_raises() -> None:
    with pytest.raises(ConfigError):
        load_settings("does-not-exist.yaml")


def test_expand_path_user_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYDIR", "abc")
    result = expand_path("$MYDIR/sub")
    assert result.name == "sub"
    assert "abc" in str(result)


def test_path_accessors_are_absolute(settings: Settings) -> None:
    assert settings.log_dir.is_absolute()
    assert settings.report_dir.is_absolute()
    assert settings.backup_dir.is_absolute()
    assert settings.database_path.is_absolute()


def test_extension_normalisation() -> None:
    settings = Settings.model_validate({"cleanup": {"temp_extensions": ["tmp", ".bak"]}})
    assert settings.cleanup.temp_extensions == [".tmp", ".bak"]

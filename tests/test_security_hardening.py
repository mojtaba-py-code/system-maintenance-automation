"""Tests for the fail-closed security rules in configuration and transport.

These cover the cases where a *silent* misconfiguration would be dangerous:
an unauthenticated dashboard on a routable address, a secret sent over plain
HTTP, an SMTP password on an unencrypted link, or an integration that looks
enabled but has an empty credential because its environment variable was never
exported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maintenance.config import (
    MIN_API_TOKEN_LENGTH,
    ConfigError,
    EmailConfig,
    PerformanceConfig,
    WebConfig,
    load_settings,
)

GOOD_TOKEN = "T" * MIN_API_TOKEN_LENGTH


# --------------------------------------------------------------------------- #
# Web exposure rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_bind_needs_no_token(host: str) -> None:
    assert WebConfig(host=host).auth_required is False


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "maintenance.example.com"])
def test_network_bind_without_token_is_refused(host: str) -> None:
    with pytest.raises(ValueError, match="Refusing to expose an unauthenticated dashboard"):
        WebConfig(host=host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10"])
def test_network_bind_with_token_is_allowed(host: str) -> None:
    cfg = WebConfig(host=host, api_token=GOOD_TOKEN)
    assert cfg.auth_required is True


def test_short_api_token_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 32 characters"):
        WebConfig(api_token="hunter2")


def test_assignment_cannot_bypass_the_exposure_rule() -> None:
    cfg = WebConfig()
    with pytest.raises(ValueError, match="Refusing to expose an unauthenticated dashboard"):
        cfg.host = "0.0.0.0"


# --------------------------------------------------------------------------- #
# Notification transport rules
# --------------------------------------------------------------------------- #
def _write_config(tmp_path: Path, body: str) -> Path:
    directory = tmp_path / "config"
    directory.mkdir(exist_ok=True)
    path = directory / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize("channel", ["discord", "slack"])
def test_plain_http_webhook_is_refused(tmp_path: Path, channel: str) -> None:
    cfg = _write_config(
        tmp_path,
        f"notifications:\n  {channel}:\n    webhook_url: 'http://hooks.example.com/abc'\n",
    )
    with pytest.raises(ConfigError, match="must use https"):
        load_settings(cfg)


@pytest.mark.parametrize("channel", ["discord", "slack"])
def test_https_webhook_is_accepted(tmp_path: Path, channel: str) -> None:
    cfg = _write_config(
        tmp_path,
        f"notifications:\n  {channel}:\n    webhook_url: 'https://hooks.example.com/abc'\n",
    )
    settings = load_settings(cfg)
    assert getattr(settings.notifications, channel).webhook_url.startswith("https://")


def test_enabled_channel_with_empty_credential_is_refused(tmp_path: Path) -> None:
    # This is the shape of the accident: the placeholder resolved to "" because
    # TELEGRAM_BOT_TOKEN was never exported, and the channel would have sat
    # silently broken forever.
    cfg = _write_config(
        tmp_path,
        "notifications:\n"
        "  enabled: true\n"
        "  telegram:\n"
        "    enabled: true\n"
        "    bot_token: '${ENV:DEFINITELY_UNSET_TELEGRAM_TOKEN}'\n"
        "    chat_id: '12345'\n",
    )
    with pytest.raises(ConfigError, match="bot_token/chat_id are empty"):
        load_settings(cfg)


def test_smtp_credentials_require_tls_for_remote_hosts() -> None:
    with pytest.raises(ValueError, match="would be sent in plaintext"):
        EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            use_tls=False,
            username="bot",
            password="s3cret",
            from_addr="bot@example.com",
            to_addrs=["ops@example.com"],
        )


def test_smtp_credentials_without_tls_are_allowed_for_a_local_relay() -> None:
    cfg = EmailConfig(
        enabled=True,
        smtp_host="127.0.0.1",
        use_tls=False,
        username="bot",
        password="s3cret",
        from_addr="bot@example.com",
        to_addrs=["ops@example.com"],
    )
    assert cfg.smtp_host == "127.0.0.1"


# --------------------------------------------------------------------------- #
# Environment placeholder resolution
# --------------------------------------------------------------------------- #
def test_unresolved_placeholders_are_reported(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, "app:\n  environment: '${ENV:UNSET_ENVIRONMENT_NAME}'\n")
    settings = load_settings(cfg)
    assert settings.unresolved_env_vars == ["UNSET_ENVIRONMENT_NAME"]
    assert settings.app.environment == ""


def test_resolved_placeholders_do_not_appear_as_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SET_ENVIRONMENT_NAME", "staging")
    cfg = _write_config(tmp_path, "app:\n  environment: '${ENV:SET_ENVIRONMENT_NAME}'\n")
    settings = load_settings(cfg)
    assert settings.app.environment == "staging"
    assert settings.unresolved_env_vars == []


# --------------------------------------------------------------------------- #
# Outbound POST behaviour
# --------------------------------------------------------------------------- #
def test_post_json_refuses_non_https() -> None:
    pytest.importorskip("httpx")
    from maintenance.notifications.channels import _post_json

    assert _post_json("http://hooks.example.com/abc", {"text": "hi"}) is False


def test_post_json_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    httpx = pytest.importorskip("httpx")
    from maintenance.notifications import channels

    calls: list[str] = []

    def fake_post(url: str, **kwargs: Any) -> Any:
        calls.append(url)
        request = httpx.Request("POST", url)
        status = 500 if len(calls) < 3 else 200
        return httpx.Response(status, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(channels.time, "sleep", lambda _s: None)

    ok = channels._post_json(
        "https://hooks.example.com/abc",
        {"text": "hi"},
        performance=PerformanceConfig(retry_attempts=2),
    )
    assert ok is True
    assert len(calls) == 3


def test_post_json_does_not_retry_a_rejected_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    httpx = pytest.importorskip("httpx")
    from maintenance.notifications import channels

    calls: list[str] = []

    def fake_post(url: str, **kwargs: Any) -> Any:
        calls.append(url)
        return httpx.Response(403, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(channels.time, "sleep", lambda _s: None)

    ok = channels._post_json(
        "https://hooks.example.com/abc",
        {"text": "hi"},
        performance=PerformanceConfig(retry_attempts=5),
    )
    assert ok is False
    assert len(calls) == 1, "a 403 is permanent; retrying only burns rate limit"


def test_post_json_never_follows_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    httpx = pytest.importorskip("httpx")
    from maintenance.notifications import channels

    seen: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert channels._post_json("https://hooks.example.com/abc", {"text": "hi"}) is True
    assert seen["follow_redirects"] is False

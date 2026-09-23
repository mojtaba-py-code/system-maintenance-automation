"""Tests for the optional FastAPI web layer (skipped if extras absent)."""

from __future__ import annotations

from pathlib import Path

import pytest

from maintenance.config import MIN_API_TOKEN_LENGTH, Settings

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = "s" * MIN_API_TOKEN_LENGTH


def _client(settings: Settings) -> TestClient:
    from maintenance.web.server import create_app

    return TestClient(create_app(settings))


def _authed_client(settings: Settings) -> TestClient:
    settings.web.api_token = TOKEN
    return _client(settings)


# --------------------------------------------------------------------------- #
# Open (loopback, no token) behaviour
# --------------------------------------------------------------------------- #
def test_ping_is_unauthenticated(settings: Settings) -> None:
    resp = _client(settings).get("/api/ping")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_endpoint(settings: Settings) -> None:
    resp = _client(settings).get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "snapshot" in body and "health" in body


def test_dashboard_html_renders(settings: Settings) -> None:
    resp = _client(settings).get("/")
    assert resp.status_code == 200
    assert "Health Score" in resp.text


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def test_token_required_when_configured(settings: Settings) -> None:
    client = _authed_client(settings)
    assert client.get("/api/system").status_code == 401
    ok = client.get("/api/system", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200


def test_dashboard_is_gated_too(settings: Settings) -> None:
    # The HTML view exposes the same host inventory as the API, so a configured
    # token must protect it as well — not just /api/*.
    client = _authed_client(settings)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_with_correct_token_grants_a_session(settings: Settings) -> None:
    client = _authed_client(settings)
    resp = client.post("/login", data={"token": TOKEN}, follow_redirects=False)
    assert resp.status_code == 303
    cookie = resp.cookies.get("session")
    assert cookie and TOKEN not in cookie, "the cookie must not carry the token itself"
    # The session now unlocks both surfaces.
    assert client.get("/").status_code == 200
    assert client.get("/api/system").status_code == 200


def test_login_with_wrong_token_is_rejected(settings: Settings) -> None:
    client = _authed_client(settings)
    resp = client.post("/login", data={"token": "wrong"}, follow_redirects=False)
    assert resp.status_code == 401
    assert "session" not in resp.cookies
    assert client.get("/", follow_redirects=False).status_code == 303


def test_repeated_failures_lock_the_client_out(settings: Settings) -> None:
    settings.web.max_failed_logins = 2
    client = _authed_client(settings)
    for _ in range(2):
        assert client.post("/login", data={"token": "wrong"}).status_code == 401
    locked = client.post("/login", data={"token": "wrong"}, follow_redirects=False)
    assert locked.status_code == 429
    # Even the correct token is refused while the lockout is in force.
    still_locked = client.post("/login", data={"token": TOKEN}, follow_redirects=False)
    assert still_locked.status_code == 429


def test_logout_invalidates_the_session(settings: Settings) -> None:
    client = _authed_client(settings)
    client.post("/login", data={"token": TOKEN})
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_session_cookie_is_hardened(settings: Settings) -> None:
    client = _authed_client(settings)
    resp = client.post("/login", data={"token": TOKEN}, follow_redirects=False)
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie


# --------------------------------------------------------------------------- #
# Hardening of the surface itself
# --------------------------------------------------------------------------- #
def test_security_headers_are_present(settings: Settings) -> None:
    resp = _client(settings).get("/api/ping")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert "default-src 'none'" in resp.headers["content-security-policy"]


def test_dashboard_script_carries_the_csp_nonce(settings: Settings) -> None:
    resp = _client(settings).get("/")
    csp = resp.headers["content-security-policy"]
    nonce = csp.split("script-src 'nonce-", 1)[1].split("'", 1)[0]
    assert f'<script nonce="{nonce}">' in resp.text
    assert f'<style nonce="{nonce}">' in resp.text


def test_history_limit_is_bounded(settings: Settings) -> None:
    client = _client(settings)
    # A single request must not be able to pull the whole runs table.
    assert client.get("/api/history", params={"limit": 10_000_000}).status_code == 422
    assert client.get("/api/history", params={"limit": 0}).status_code == 422
    assert client.get("/api/history", params={"limit": 5}).status_code == 200


def test_disk_usage_rejects_paths_outside_allowed_roots(settings: Settings, tmp_path: Path) -> None:
    # settings.security.allowed_roots is [tmp_path]; a sibling path must be refused.
    outside = tmp_path.parent / "definitely-not-allowed"
    resp = _client(settings).get("/api/disk-usage", params={"path": str(outside)})
    assert resp.status_code == 403


def test_clean_preview_is_dry_run(settings: Settings, junk_dir: Path) -> None:
    resp = _client(settings).post("/api/clean/preview")
    assert resp.status_code == 200
    assert resp.json()["dry_run"] is True
    # Dry-run preview must not have deleted anything.
    assert (junk_dir / "a.tmp").exists()


def test_clean_preview_does_not_return_host_paths(
    settings: Settings, junk_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed removal names the file; the HTTP caller must not be told which.

    CleanupResult.errors is written for the console, where the operator is
    looking at their own filesystem. Returned over HTTP it maps the host for
    whoever holds the token, which is the same disclosure to_dict already
    avoids by withholding `actions`.
    """
    from maintenance.cleanup import CleanupEngine, CleanupResult

    secret_path = "/srv/private/customer-archive/2026-invoices.tar.gz"

    def fake_run(self: CleanupEngine, **_kwargs: object) -> CleanupResult:
        return CleanupResult(
            dry_run=True,
            errors=[f"Failed to delete {secret_path}: [Errno 13] Permission denied"],
        )

    monkeypatch.setattr(CleanupEngine, "run", fake_run)

    resp = _client(settings).post("/api/clean/preview")
    assert resp.status_code == 200
    body = resp.json()

    assert body["error_count"] == 1, "the caller still learns that something failed"
    assert "errors" not in body
    assert secret_path not in resp.text
    assert "Permission denied" not in resp.text


def test_run_server_rejects_an_unauthenticated_network_bind(settings: Settings) -> None:
    from maintenance.web.server import run_server

    # A --host override on the command line must not bypass the config rule.
    with pytest.raises(ValueError, match="Refusing to expose an unauthenticated dashboard"):
        run_server(settings, host="0.0.0.0")

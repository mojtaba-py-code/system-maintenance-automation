"""FastAPI dashboard + REST API.

Read-heavy by design: the API exposes live monitoring, system info, disk
analysis, security scan and run history. The only state-changing endpoint is a
**dry-run** cleanup preview -- the web surface never deletes anything, which
keeps the attack surface small when the dashboard is exposed.

Authentication
--------------
When ``web.api_token`` is set, *every* non-public route is authenticated --
including the HTML dashboard, which exposes the same host inventory and run
history the JSON API does. Two credential carriers are accepted:

* ``Authorization: Bearer <token>`` -- for scripts and ``curl``.
* A ``session`` cookie -- issued by ``POST /login`` after a browser submits the
  token in a form body. The token itself is never placed in a URL (which would
  leak it into history, proxy logs and ``Referer`` headers) and never stored in
  the cookie; the cookie holds an opaque, expiring session id.

Repeated bad tokens from one client lock that client out for
``web.lockout_seconds``, so a token cannot be brute-forced over the network.

With no token configured the server may only bind to loopback -- enforced in
:class:`~maintenance.config.WebConfig`, not merely defaulted -- so an
unauthenticated dashboard is never reachable off-host.
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from ..config import Settings
from ..disk import DiskAnalyzer
from ..health import compute_health
from ..logger import get_logger
from ..monitor import SystemMonitor
from ..security import PathGuard
from ..systeminfo import collect_system_info

logger = get_logger("web")

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

SESSION_COOKIE = "session"
#: Upper bound on ``/api/history?limit=`` so a single request cannot be used to
#: pull (and buffer in memory) the entire run table.
MAX_HISTORY_LIMIT = 500
#: Cap on tracked client keys, so the brute-force table cannot be grown without
#: bound by an attacker rotating source addresses.
_MAX_TRACKED_CLIENTS = 1024
#: Maximum accepted login body. The form carries one short field, so anything
#: larger is either a mistake or an attempt to make the server buffer garbage.
_MAX_LOGIN_BODY_BYTES = 4096


async def _read_submitted_token(request: Request) -> str:
    """Extract the ``token`` field from a urlencoded login body.

    Parsed directly rather than through ``request.form()`` so the package needs
    no multipart dependency for its single one-field form. The body is size
    capped, and the token never travels in the query string.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_LOGIN_BODY_BYTES:
        return ""
    raw = await request.body()
    if len(raw) > _MAX_LOGIN_BODY_BYTES:
        return ""
    fields = parse_qs(raw.decode("utf-8", errors="replace"))
    values = fields.get("token") or [""]
    return values[0]


class SessionStore:
    """In-memory sessions plus a per-client failed-login lockout.

    Deliberately process-local: this dashboard is a single-process operator tool,
    so there is no shared session backend to secure, and sessions die with the
    server (a restart logs everyone out, which is the safe default).
    """

    def __init__(self, *, ttl_seconds: int, max_failures: int, lockout_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._max_failures = max_failures
        self._lockout = lockout_seconds
        self._sessions: dict[str, float] = {}
        self._failures: OrderedDict[str, tuple[int, float]] = OrderedDict()

    # -- sessions ---------------------------------------------------------- #
    def create(self) -> str:
        """Mint a new opaque session id and return it."""
        self._purge_expired()
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = time.monotonic() + self._ttl
        return sid

    def validate(self, sid: str | None) -> bool:
        """True if ``sid`` is a live, unexpired session."""
        if not sid:
            return False
        expiry = self._sessions.get(sid)
        if expiry is None:
            return False
        if expiry < time.monotonic():
            self._sessions.pop(sid, None)
            return False
        return True

    def destroy(self, sid: str | None) -> None:
        if sid:
            self._sessions.pop(sid, None)

    def _purge_expired(self) -> None:
        now = time.monotonic()
        for sid in [s for s, exp in self._sessions.items() if exp < now]:
            self._sessions.pop(sid, None)

    # -- brute-force guard -------------------------------------------------- #
    def is_locked_out(self, client: str) -> bool:
        """True while ``client`` is serving a lockout for repeated bad tokens."""
        entry = self._failures.get(client)
        if entry is None:
            return False
        count, until = entry
        if until < time.monotonic():
            self._failures.pop(client, None)
            return False
        return count >= self._max_failures

    def register_failure(self, client: str) -> None:
        count, until = self._failures.get(client, (0, 0.0))
        if until < time.monotonic():
            count = 0
        self._failures[client] = (count + 1, time.monotonic() + self._lockout)
        self._failures.move_to_end(client)
        while len(self._failures) > _MAX_TRACKED_CLIENTS:
            self._failures.popitem(last=False)

    def register_success(self, client: str) -> None:
        self._failures.pop(client, None)


def _client_key(request: Request) -> str:
    """Identify the caller for lockout accounting (best-effort)."""
    return request.client.host if request.client else "unknown"


_LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in — Maintenance Dashboard</title>
<style nonce="{nonce}">
 body {{ margin:0; min-height:100vh; display:grid; place-items:center;
        font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
        background:#0d1117; color:#e6edf3; }}
 form {{ background:#161b22; border:1px solid #30363d; border-radius:10px;
        padding:28px 32px; width:min(360px, 90vw); }}
 h1 {{ font-size:16px; margin:0 0 18px; }}
 input {{ width:100%; padding:9px 10px; border-radius:6px; border:1px solid #30363d;
         background:#0d1117; color:#e6edf3; font-size:14px; }}
 button {{ margin-top:14px; width:100%; padding:9px; border:0; border-radius:6px;
          background:#1f6feb; color:#fff; font-size:14px; cursor:pointer; }}
 .err {{ color:#f85149; font-size:13px; margin:12px 0 0; }}
</style></head>
<body><form method="post" action="/login">
 <h1>Maintenance Dashboard</h1>
 <label for="token">API token</label>
 <input id="token" name="token" type="password" autocomplete="current-password" autofocus>
 <button type="submit">Sign in</button>
 {error}
</form></body></html>
"""


def create_app(settings: Settings) -> FastAPI:
    """Build the FastAPI application bound to ``settings``."""
    app = FastAPI(
        title="System Maintenance Automation",
        version="1.0.0",
        description="Live system maintenance dashboard and REST API.",
    )
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    monitor = SystemMonitor(settings.monitor)
    guard = PathGuard(settings.security)
    sessions = SessionStore(
        ttl_seconds=settings.web.session_ttl_minutes * 60,
        max_failures=settings.web.max_failed_logins,
        lockout_seconds=settings.web.lockout_seconds,
    )

    # -- middleware --------------------------------------------------------- #
    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        """Attach defence-in-depth response headers to every response.

        The dashboard renders host data, so a strict CSP (nonce-based, no
        external origins) limits what an injected string could ever do, and
        ``X-Frame-Options`` keeps it out of third-party frames.
        """
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response: Response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; "
            f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
            # Style *attributes* carry the dynamic gauge widths. They cannot
            # execute script, and script-src stays nonce-only.
            "style-src-attr 'unsafe-inline'; "
            "img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    # -- authentication ----------------------------------------------------- #
    def _token_matches(candidate: str) -> bool:
        # Constant-time comparison to avoid leaking the token via timing.
        return secrets.compare_digest(candidate, settings.web.api_token)

    def _authenticated(request: Request, authorization: str | None) -> bool:
        if not settings.web.auth_required:
            return True
        prefix = "Bearer "
        if authorization and authorization.startswith(prefix):
            return _token_matches(authorization[len(prefix) :])
        return sessions.validate(request.cookies.get(SESSION_COOKIE))

    def require_token(
        request: Request, authorization: str | None = Header(default=None)
    ) -> None:
        """Dependency guarding the JSON API."""
        if _authenticated(request, authorization):
            return
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def _login_page(request: Request, *, error: str = "", status: int = 200) -> HTMLResponse:
        nonce = getattr(request.state, "csp_nonce", "")
        body = _LOGIN_PAGE.format(
            nonce=nonce,
            error=f'<p class="err">{error}</p>' if error else "",
        )
        return HTMLResponse(body, status_code=status)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> Response:
        if not settings.web.auth_required:
            return RedirectResponse("/", status_code=303)
        return _login_page(request)

    @app.post("/login")
    async def login(request: Request) -> Response:
        if not settings.web.auth_required:
            return RedirectResponse("/", status_code=303)
        client = _client_key(request)
        if sessions.is_locked_out(client):
            logger.warning("Rejected login from locked-out client %s", client)
            return _login_page(
                request,
                error="Too many failed attempts. Try again later.",
                status=429,
            )
        # The token stays in the request body, never in the query string where
        # it would land in access logs, browser history and Referer headers.
        submitted = await _read_submitted_token(request)
        if submitted and _token_matches(submitted):
            sessions.register_success(client)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                SESSION_COOKIE,
                sessions.create(),
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                max_age=settings.web.session_ttl_minutes * 60,
                path="/",
            )
            return response
        sessions.register_failure(client)
        logger.warning("Failed dashboard login from %s", client)
        return _login_page(request, error="Invalid token.", status=401)

    @app.post("/logout")
    def logout(request: Request) -> Response:
        sessions.destroy(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    def assert_readable_path(path: str) -> None:
        """Fail-closed path gate for filesystem-enumerating endpoints.

        With an explicit allow-list, the path must fall inside it. With no
        allow-list configured, restrict enumeration to the application's own
        base directory rather than exposing the whole volume over the network.
        """
        if settings.security.allowed_roots:
            allowed = guard.within_allowed_roots(path)
        else:
            allowed = PathGuard.is_within(Path(path), settings.base_dir)
        if not allowed:
            raise HTTPException(status_code=403, detail="Path is not within an allowed root.")

    # -- HTML dashboard ---------------------------------------------------- #
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, authorization: str | None = Header(default=None)) -> Any:
        # The dashboard renders the same data as the API, so it gets the same
        # gate -- redirecting rather than 401-ing, since the caller is a browser.
        if not _authenticated(request, authorization):
            return RedirectResponse("/login", status_code=303)
        snapshot = monitor.snapshot()
        health = compute_health(snapshot, settings.monitor)
        from ..database import open_database

        db = open_database(settings.database_path, enabled=settings.database.enabled)
        try:
            summary = db.summary()
            recent = db.recent_runs(limit=10)
        finally:
            db.close()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "snapshot": snapshot,
                "health": health,
                "summary": summary,
                "recent": recent,
                "system": collect_system_info(),
                "environment": settings.app.environment,
                "csp_nonce": getattr(request.state, "csp_nonce", ""),
                "auth_required": settings.web.auth_required,
            },
        )

    # -- REST API ---------------------------------------------------------- #
    @app.get("/api/health", dependencies=[Depends(require_token)])
    def api_health() -> dict[str, Any]:
        snapshot = monitor.snapshot()
        health = compute_health(snapshot, settings.monitor)
        return {"snapshot": snapshot.to_dict(), "health": health.to_dict()}

    @app.get("/api/system", dependencies=[Depends(require_token)])
    def api_system() -> dict[str, Any]:
        return collect_system_info().to_dict()

    @app.get("/api/history", dependencies=[Depends(require_token)])
    def api_history(
        limit: int = Query(default=20, ge=1, le=MAX_HISTORY_LIMIT),
    ) -> dict[str, Any]:
        from ..database import open_database

        db = open_database(settings.database_path, enabled=settings.database.enabled)
        try:
            return {"summary": db.summary(), "runs": db.recent_runs(limit=limit)}
        finally:
            db.close()

    @app.get("/api/disks", dependencies=[Depends(require_token)])
    def api_disks() -> dict[str, Any]:
        return {"disks": [d.__dict__ for d in monitor.disks()]}

    @app.get("/api/disk-usage", dependencies=[Depends(require_token)])
    def api_disk_usage(path: str = ".") -> dict[str, Any]:
        # Fail-closed: never enumerate arbitrary filesystem locations over HTTP.
        assert_readable_path(path)
        analyzer = DiskAnalyzer(settings.disk, settings.security, guard=guard)
        return analyzer.usage(path).to_dict()

    @app.post("/api/clean/preview", dependencies=[Depends(require_token)])
    def api_clean_preview() -> dict[str, Any]:
        """Dry-run cleanup preview. Never deletes anything via the web API."""
        from ..cleanup import CleanupEngine

        engine = CleanupEngine(
            settings.cleanup,
            settings.security,
            quarantine_dir=settings.quarantine_dir,
            guard=guard,
            dry_run=True,
        )
        return engine.run(temp=True, duplicates=True, empty_dirs=True).to_dict()

    @app.get("/api/ping")
    def ping() -> dict[str, str]:
        """Unauthenticated liveness probe. Reveals no host information."""
        return {"status": "ok", "service": "system-maintenance-automation"}

    return app


def run_server(settings: Settings, *, host: str | None = None, port: int | None = None) -> None:
    """Run the dashboard with uvicorn (blocking).

    ``host``/``port`` overrides from the CLI are re-validated against the same
    rule the config enforces: no unauthenticated bind outside loopback.
    """
    import uvicorn

    from ..config import WebConfig

    effective = settings.web.model_copy(
        update={"host": host or settings.web.host, "port": port or settings.web.port}
    )
    # model_copy skips validators, so re-run them explicitly on the override.
    WebConfig.model_validate(effective.model_dump())

    app = create_app(settings)
    uvicorn.run(app, host=effective.host, port=effective.port, log_level="info")

"""Typed, validated application configuration.

Configuration is loaded from a YAML (or TOML) file into a tree of pydantic
models. This gives us:

* **Validation** -- wrong types / out-of-range values fail loudly at load time
  instead of deep inside a maintenance run.
* **Documentation** -- every setting is described where it is defined.
* **Safety** -- secrets are referenced via ``${ENV:VAR}`` placeholders and
  resolved from the environment, never stored in the file.

The public surface is small: build a :class:`Settings` object with
:func:`load_settings` and pass it around (dependency injection). Nothing in the
package reads global state.
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ``tomllib`` is stdlib from 3.11; degrade gracefully on 3.10.
try:  # pragma: no cover - trivial import guard
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

_ENV_PLACEHOLDER = re.compile(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ArchiveFormat = Literal["zip", "tar.gz", "tar.bz2"]
ReportFormat = Literal["json", "csv", "html", "markdown"]


class ConfigError(Exception):
    """Raised when configuration cannot be loaded or is invalid."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def expand_path(value: str | os.PathLike[str]) -> Path:
    """Expand ``~`` and environment variables, returning an absolute-ish Path.

    The result is *not* resolved against the filesystem (the target may not
    exist yet); callers combine it with :attr:`Settings.data_dir` as needed.
    """
    text = os.path.expandvars(os.fspath(value))
    return Path(text).expanduser()


def _resolve_env_placeholders(obj: Any, missing: set[str] | None = None) -> Any:
    """Recursively replace ``${ENV:VAR}`` placeholders with environment values.

    Unset variables resolve to an empty string so that "disabled" integrations
    do not explode at load time; the feature simply stays inert. Their names are
    collected into ``missing`` so the loader can surface them -- an unresolved
    placeholder is reported, never silently swallowed, because "the secret
    quietly became empty" is exactly how authentication ends up disabled by
    accident. The validators below then fail closed on anything that matters.
    """

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.environ.get(name)
        if value is None:
            if missing is not None:
                missing.add(name)
            return ""
        return value

    if isinstance(obj, str):
        return _ENV_PLACEHOLDER.sub(substitute, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_placeholders(v, missing) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_placeholders(v, missing) for v in obj]
    return obj


def _is_loopback_host(host: str) -> bool:
    """True if ``host`` can only be reached from the local machine.

    Hostnames other than ``localhost`` are treated as non-loopback: we cannot
    resolve them safely at config-load time, so we assume the riskier case.
    """
    candidate = host.strip().strip("[]").lower()
    if candidate in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _require_https(url: str, *, field_name: str) -> str:
    """Validate that a webhook/API URL uses TLS.

    Webhook URLs are bearer credentials in their own right -- posting one over
    plain HTTP leaks it to anything on the path.
    """
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(
            f"{field_name} must use https:// (got {parsed.scheme or 'no'} scheme); "
            "the URL is a secret and plain HTTP would expose it in transit."
        )
    if not parsed.netloc:
        raise ValueError(f"{field_name} is not a valid URL: {url!r}")
    return url


# --------------------------------------------------------------------------- #
# Sub-models
# --------------------------------------------------------------------------- #
class AppConfig(BaseModel):
    environment: str = "production"
    data_dir: str = "."
    require_confirmation: bool = True


class LoggingConfig(BaseModel):
    level: LogLevel = "INFO"
    console: bool = True
    directory: str = "logs"
    rotate_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    rotate_backup_count: int = Field(default=7, ge=0)
    date_stamped_files: bool = True
    audit_log: bool = True


class DatabaseConfig(BaseModel):
    path: str = "database/maintenance.db"
    enabled: bool = True


class SecurityConfig(BaseModel):
    protected_paths: list[str] = Field(default_factory=list)
    allowed_roots: list[str] = Field(default_factory=list)
    max_delete_batch: int = Field(default=5000, ge=1)
    follow_symlinks: bool = False


class CleanupConfig(BaseModel):
    temp_directories: list[str] = Field(default_factory=list)
    log_max_age_days: int = Field(default=30, ge=0)
    log_directories: list[str] = Field(default_factory=list)
    temp_extensions: list[str] = Field(
        default_factory=lambda: [".tmp", ".temp", ".bak", ".dmp"]
    )
    exclude_patterns: list[str] = Field(default_factory=list)
    exclude_dirs: list[str] = Field(
        default_factory=lambda: [".git", "node_modules", "__pycache__", ".venv"]
    )
    empty_recycle_bin: bool = False
    remove_empty_dirs: bool = True
    quarantine: bool = True
    quarantine_dir: str = "backups/quarantine"
    quarantine_retention_days: int = Field(default=7, ge=0)

    @field_validator("temp_extensions")
    @classmethod
    def _normalise_extensions(cls, exts: list[str]) -> list[str]:
        return [e if e.startswith(".") else f".{e}" for e in exts]


class BackupConfig(BaseModel):
    sources: list[str] = Field(default_factory=list)
    destination: str = "backups"
    archive_format: ArchiveFormat = "zip"
    compression_level: int = Field(default=6, ge=0, le=9)
    incremental: bool = True
    keep_last: int = Field(default=10, ge=1)
    verify_after_backup: bool = True
    exclude_patterns: list[str] = Field(default_factory=list)


class MonitorConfig(BaseModel):
    cpu_threshold: float = Field(default=85.0, ge=0, le=100)
    memory_threshold: float = Field(default=90.0, ge=0, le=100)
    swap_threshold: float = Field(default=80.0, ge=0, le=100)
    disk_threshold: float = Field(default=90.0, ge=0, le=100)
    watch_paths: list[str] = Field(default_factory=list)
    cpu_sample_interval: float = Field(default=1.0, gt=0, le=10)
    top_processes: int = Field(default=10, ge=1, le=100)


class DiskConfig(BaseModel):
    large_file_threshold: int = Field(default=100 * 1024 * 1024, ge=1)
    old_file_age_days: int = Field(default=365, ge=1)
    max_depth: int = Field(default=6, ge=0)


class ReportConfig(BaseModel):
    formats: list[ReportFormat] = Field(
        default_factory=lambda: cast("list[ReportFormat]", ["json", "html"])
    )
    directory: str = "reports"
    include_file_lists: bool = False


class PerformanceConfig(BaseModel):
    threads: int = Field(default=0, ge=0)
    command_timeout: int = Field(default=120, ge=1)
    retry_attempts: int = Field(default=2, ge=0)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)

    @property
    def worker_count(self) -> int:
        """Effective worker-thread count (auto-detect when configured as 0)."""
        return self.threads if self.threads > 0 else (os.cpu_count() or 4)


class ScheduledJob(BaseModel):
    name: str
    task: Literal["clean", "backup", "monitor", "report", "scan"]
    interval: str
    at: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class SchedulerConfig(BaseModel):
    enabled: bool = False
    jobs: list[ScheduledJob] = Field(default_factory=list)


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""

    @model_validator(mode="after")
    def _require_credentials_when_enabled(self) -> TelegramConfig:
        if self.enabled and not (self.bot_token and self.chat_id):
            raise ValueError(
                "telegram is enabled but bot_token/chat_id are empty "
                "(check that the referenced ${ENV:...} variables are exported)."
            )
        return self


class DiscordConfig(BaseModel):
    enabled: bool = False
    webhook_url: str = ""

    @field_validator("webhook_url")
    @classmethod
    def _https_only(cls, url: str) -> str:
        return _require_https(url, field_name="notifications.discord.webhook_url")

    @model_validator(mode="after")
    def _require_credentials_when_enabled(self) -> DiscordConfig:
        if self.enabled and not self.webhook_url:
            raise ValueError("discord is enabled but webhook_url is empty.")
        return self


class SlackConfig(BaseModel):
    enabled: bool = False
    webhook_url: str = ""

    @field_validator("webhook_url")
    @classmethod
    def _https_only(cls, url: str) -> str:
        return _require_https(url, field_name="notifications.slack.webhook_url")

    @model_validator(mode="after")
    def _require_credentials_when_enabled(self) -> SlackConfig:
        if self.enabled and not self.webhook_url:
            raise ValueError("slack is enabled but webhook_url is empty.")
        return self


class EmailConfig(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    use_tls: bool = True
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_safe_transport(self) -> EmailConfig:
        if not self.enabled:
            return self
        if not (self.smtp_host and self.from_addr and self.to_addrs):
            raise ValueError("email is enabled but smtp_host/from_addr/to_addrs are incomplete.")
        # Refuse to hand SMTP credentials to a remote server in the clear.
        if self.username and not self.use_tls and not _is_loopback_host(self.smtp_host):
            raise ValueError(
                "email.use_tls is false while credentials are configured for a remote "
                f"SMTP host ({self.smtp_host}); the password would be sent in plaintext. "
                "Enable use_tls, or point smtp_host at a local relay."
            )
        return self


class NotificationsConfig(BaseModel):
    enabled: bool = False
    notify_below_health: int = Field(default=70, ge=0, le=100)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)


#: Minimum accepted ``web.api_token`` length. 32 URL-safe characters is roughly
#: 190 bits of entropy from ``secrets.token_urlsafe(24)`` -- far beyond brute
#: force, and short enough to stay copy-pasteable.
MIN_API_TOKEN_LENGTH = 32


class WebConfig(BaseModel):
    """Dashboard/REST API settings.

    The security contract enforced here: **a server reachable from the network
    must be authenticated.** Binding to anything other than loopback without a
    token is rejected at load time rather than quietly serving host inventory,
    run history and disk layout to the local network.
    """

    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)
    api_token: str = ""
    session_ttl_minutes: int = Field(default=60, ge=1, le=1440)
    max_failed_logins: int = Field(default=5, ge=1, le=100)
    lockout_seconds: int = Field(default=300, ge=1, le=86400)

    # Re-validate on assignment: mutating ``settings.web.host`` at runtime must
    # not be a way around the "no unauthenticated network bind" rule.
    model_config = {"validate_assignment": True}

    @property
    def auth_required(self) -> bool:
        """True when an API token is configured and must be presented."""
        return bool(self.api_token)

    @model_validator(mode="after")
    def _enforce_exposure_rules(self) -> WebConfig:
        if self.api_token and len(self.api_token) < MIN_API_TOKEN_LENGTH:
            raise ValueError(
                f"web.api_token must be at least {MIN_API_TOKEN_LENGTH} characters "
                f"(got {len(self.api_token)}). Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        if not self.api_token and not _is_loopback_host(self.host):
            raise ValueError(
                f"web.host is {self.host!r}, which is reachable from the network, but "
                "web.api_token is empty. Set a token (e.g. via ${ENV:MAINTENANCE_API_TOKEN}) "
                "or bind to 127.0.0.1. Refusing to expose an unauthenticated dashboard."
            )
        return self


# --------------------------------------------------------------------------- #
# Root model
# --------------------------------------------------------------------------- #
class Settings(BaseModel):
    """Root configuration object for the whole application."""

    app: AppConfig = Field(default_factory=AppConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    disk: DiskConfig = Field(default_factory=DiskConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    web: WebConfig = Field(default_factory=WebConfig)

    # Absolute base directory for all runtime output. Populated by the loader.
    base_dir: Path = Field(default_factory=Path.cwd)
    #: ``${ENV:VAR}`` names referenced by the config file but absent from the
    #: environment. Populated by the loader so the CLI can warn about silently
    #: inert integrations. Security-critical cases already fail validation.
    unresolved_env_vars: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def _resolve_base_dir(self) -> Settings:
        base = expand_path(self.app.data_dir)
        if not base.is_absolute():
            base = (self.base_dir / base).resolve()
        object.__setattr__(self, "base_dir", base)
        return self

    # -- Convenience path accessors ---------------------------------------- #
    def _under_base(self, value: str) -> Path:
        p = expand_path(value)
        return p if p.is_absolute() else (self.base_dir / p)

    @property
    def log_dir(self) -> Path:
        return self._under_base(self.logging.directory)

    @property
    def report_dir(self) -> Path:
        return self._under_base(self.report.directory)

    @property
    def backup_dir(self) -> Path:
        return self._under_base(self.backup.destination)

    @property
    def database_path(self) -> Path:
        return self._under_base(self.database.path)

    @property
    def quarantine_dir(self) -> Path:
        return self._under_base(self.cleanup.quarantine_dir)

    def ensure_directories(self) -> None:
        """Create the runtime output directories if they do not exist."""
        for directory in (self.log_dir, self.report_dir, self.backup_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if self.database.enabled:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.cleanup.quarantine:
            self.quarantine_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _read_mapping(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(raw) or {}
    elif suffix == ".toml":
        if tomllib is None:  # pragma: no cover
            raise ConfigError("TOML config requires Python 3.11+ (tomllib).")
        data = tomllib.loads(raw)
    else:
        raise ConfigError(f"Unsupported config format: {suffix!r} ({path})")
    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(data).__name__}.")
    return data


def load_settings(
    config_path: str | os.PathLike[str] | None = None,
    *,
    base_dir: str | os.PathLike[str] | None = None,
) -> Settings:
    """Load and validate :class:`Settings`.

    Parameters
    ----------
    config_path:
        Path to a YAML/TOML config file. When ``None``, sensible built-in
        defaults are used (no file required).
    base_dir:
        Project root used to resolve relative output paths. Defaults to the
        directory containing ``config_path``'s parent, else the current dir.

    Raises
    ------
    ConfigError
        If the file is missing, malformed, or fails validation.
    """
    missing_env: set[str] = set()
    if config_path is None:
        data: dict[str, Any] = {}
        resolved_base = Path(base_dir).resolve() if base_dir else Path.cwd()
    else:
        path = Path(config_path).expanduser()
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        data = _read_mapping(path)
        data = _resolve_env_placeholders(data, missing_env)
        # Default project root: the parent of the `config/` directory.
        if base_dir is not None:
            resolved_base = Path(base_dir).resolve()
        elif path.parent.name == "config":
            resolved_base = path.parent.parent.resolve()
        else:
            resolved_base = path.parent.resolve()

    data.setdefault("base_dir", resolved_base)
    data["unresolved_env_vars"] = sorted(missing_env)
    try:
        return Settings.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and friends
        raise ConfigError(f"Invalid configuration: {exc}") from exc


def find_default_config(start: str | os.PathLike[str] | None = None) -> Path | None:
    """Locate a ``config/config.yaml`` (then ``config.example.yaml``) upward.

    Returns ``None`` when nothing is found, letting the caller fall back to
    built-in defaults.
    """
    current = Path(start).resolve() if start else Path.cwd()
    for directory in (current, *current.parents):
        for name in ("config/config.yaml", "config/config.yml", "config/config.example.yaml"):
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None

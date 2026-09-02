"""Enterprise-grade logging setup.

Provides three coordinated sinks driven by :class:`~maintenance.config.LoggingConfig`:

* **Console** -- human-friendly, colourised output (rich when available).
* **Rotating file** -- ``logs/maintenance-YYYY-MM-DD.log`` with size rotation.
* **Error file** -- ``logs/errors.log`` capturing WARNING+ only.
* **Audit trail** -- ``logs/audit.log``, an append-only record of every
  destructive or state-changing action, for after-the-fact accountability.

Call :func:`configure_logging` once at start-up. Everywhere else, use
``logging.getLogger("maintenance.<module>")`` (or :func:`get_logger`).
"""

from __future__ import annotations

import logging
import logging.handlers
from datetime import date
from pathlib import Path

from .config import LoggingConfig

_AUDIT_LOGGER_NAME = "maintenance.audit"
_ROOT_LOGGER_NAME = "maintenance"
_configured = False


def _build_console_handler(level: int) -> logging.Handler:
    """Return a rich console handler, falling back to a plain stream handler."""
    try:
        from rich.logging import RichHandler

        handler: logging.Handler = RichHandler(
            level=level,
            rich_tracebacks=True,
            show_path=False,
            markup=False,
            log_time_format="[%X]",
        )
    except Exception:  # pragma: no cover - rich should be installed
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
    handler.setLevel(level)
    return handler


def _rotating_handler(path: Path, cfg: LoggingConfig, level: int) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=cfg.rotate_max_bytes,
        backupCount=cfg.rotate_backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


def configure_logging(cfg: LoggingConfig, log_dir: Path, *, silent: bool = False) -> logging.Logger:
    """Configure the ``maintenance`` logger hierarchy.

    Parameters
    ----------
    cfg:
        Logging configuration.
    log_dir:
        Directory to write log files into (created if missing).
    silent:
        When True, suppress the console sink (file logging still happens).

    Returns
    -------
    logging.Logger
        The configured package root logger.
    """
    global _configured
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, cfg.level, logging.INFO)

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(level)
    root.propagate = False
    # Reset handlers so repeated calls (e.g. in tests) don't duplicate output.
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()

    if cfg.console and not silent:
        root.addHandler(_build_console_handler(level))

    stamp = date.today().isoformat() if cfg.date_stamped_files else "current"
    root.addHandler(_rotating_handler(log_dir / f"maintenance-{stamp}.log", cfg, level))
    root.addHandler(_rotating_handler(log_dir / "errors.log", cfg, logging.WARNING))

    if cfg.audit_log:
        audit = logging.getLogger(_AUDIT_LOGGER_NAME)
        audit.setLevel(logging.INFO)
        audit.propagate = False
        for existing in list(audit.handlers):
            audit.removeHandler(existing)
            existing.close()
        audit_handler = logging.handlers.RotatingFileHandler(
            log_dir / "audit.log",
            maxBytes=cfg.rotate_max_bytes,
            backupCount=max(cfg.rotate_backup_count, 3),
            encoding="utf-8",
        )
        audit_handler.setFormatter(
            logging.Formatter("%(asctime)s | AUDIT | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        audit.addHandler(audit_handler)

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``maintenance`` namespace."""
    if not name.startswith(_ROOT_LOGGER_NAME):
        name = f"{_ROOT_LOGGER_NAME}.{name}"
    return logging.getLogger(name)


def audit(message: str, **fields: object) -> None:
    """Write a structured entry to the audit trail.

    Example
    -------
    >>> audit("delete", path="/tmp/foo", size=1024, dry_run=False)
    """
    logger = logging.getLogger(_AUDIT_LOGGER_NAME)
    if fields:
        extras = " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info("%s %s", message, extras)
    else:
        logger.info("%s", message)

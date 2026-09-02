"""Concrete notification channels.

Each channel implements :class:`~maintenance.notifications.base.Notifier`.
Network calls use ``httpx`` with a short timeout and never raise -- a failed
notification must not fail the maintenance run.

Transport rules, enforced here and in the config validators:

* Webhook URLs must be ``https``. A webhook URL *is* a credential; sending it
  over plain HTTP hands it to anything on the path.
* Redirects are never followed, so a compromised or misconfigured endpoint
  cannot bounce the payload (and the token in the Telegram URL) elsewhere.
* Transient failures are retried with exponential backoff; permanent ones
  (4xx) are not, because retrying a rejected credential only burns rate limit.
"""

from __future__ import annotations

import smtplib
import ssl
import time
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from ..config import DiscordConfig, EmailConfig, PerformanceConfig, SlackConfig, TelegramConfig
from ..logger import get_logger
from .base import Notification, Notifier

logger = get_logger("notifications")
_TIMEOUT = 10.0

#: Retry policy used when the caller does not supply a PerformanceConfig.
_DEFAULT_RETRY = PerformanceConfig()


def _post_json(
    url: str,
    payload: dict[str, object],
    *,
    performance: PerformanceConfig | None = None,
) -> bool:
    """POST JSON with httpx; return success. Never raises.

    Retries transient failures (network errors, 5xx, 429) according to
    ``performance.retry_attempts`` / ``retry_backoff_seconds``.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - optional extra
        logger.warning("httpx not installed; cannot send web notification.")
        return False

    if not url.lower().startswith("https://"):
        # Defence in depth: config validation already rejects this, but a
        # programmatically-built channel must not be able to bypass it.
        logger.error("Refusing to POST a notification over a non-HTTPS URL.")
        return False

    cfg = performance or _DEFAULT_RETRY
    attempts = cfg.retry_attempts + 1
    for attempt in range(attempts):
        try:
            response = httpx.post(url, json=payload, timeout=_TIMEOUT, follow_redirects=False)
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            # SECURITY: never log the exception/URL — for Telegram the URL embeds
            # the bot token, and webhook URLs are themselves secrets. Status only.
            status = exc.response.status_code
            retryable = status == 429 or status >= 500
            logger.warning("Notification POST failed with HTTP %s", status)
            if not retryable:
                return False
        except Exception as exc:
            logger.warning("Notification POST failed: %s", type(exc).__name__)
        if attempt < attempts - 1:
            time.sleep(cfg.retry_backoff_seconds * (2**attempt))
    return False


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(self, config: TelegramConfig, performance: PerformanceConfig | None = None) -> None:
        self._config = config
        self._performance = performance

    def send(self, notification: Notification) -> bool:
        if not (self._config.bot_token and self._config.chat_id):
            logger.warning("Telegram not configured (missing token/chat id).")
            return False
        url = f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage"
        return _post_json(
            url,
            {"chat_id": self._config.chat_id, "text": notification.as_text()},
            performance=self._performance,
        )


class DiscordNotifier(Notifier):
    name = "discord"

    def __init__(self, config: DiscordConfig, performance: PerformanceConfig | None = None) -> None:
        self._config = config
        self._performance = performance

    def send(self, notification: Notification) -> bool:
        if not self._config.webhook_url:
            logger.warning("Discord not configured (missing webhook url).")
            return False
        return _post_json(
            self._config.webhook_url,
            {"content": notification.as_text()},
            performance=self._performance,
        )


class SlackNotifier(Notifier):
    name = "slack"

    def __init__(self, config: SlackConfig, performance: PerformanceConfig | None = None) -> None:
        self._config = config
        self._performance = performance

    def send(self, notification: Notification) -> bool:
        if not self._config.webhook_url:
            logger.warning("Slack not configured (missing webhook url).")
            return False
        return _post_json(
            self._config.webhook_url,
            {"text": notification.as_text()},
            performance=self._performance,
        )


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, config: EmailConfig, performance: PerformanceConfig | None = None) -> None:
        self._config = config
        self._performance = performance

    def send(self, notification: Notification) -> bool:
        cfg = self._config
        if not (cfg.smtp_host and cfg.from_addr and cfg.to_addrs):
            logger.warning("Email not configured (missing host/from/to).")
            return False
        message = MIMEText(notification.as_text(), _charset="utf-8")
        message["Subject"] = notification.title
        message["From"] = cfg.from_addr
        message["To"] = ", ".join(cfg.to_addrs)
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid()
        try:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=_TIMEOUT) as server:
                if cfg.use_tls:
                    # A default context verifies the certificate chain *and* the
                    # hostname, so STARTTLS cannot be stripped down to a
                    # man-in-the-middle's self-signed cert.
                    server.starttls(context=ssl.create_default_context())
                if cfg.username and cfg.password:
                    if not cfg.use_tls:
                        # Config validation blocks this for remote hosts; refuse
                        # here too so a hand-built config cannot leak a password.
                        logger.error("Refusing to send SMTP credentials over an unencrypted link.")
                        return False
                    server.login(cfg.username, cfg.password)
                server.sendmail(cfg.from_addr, cfg.to_addrs, message.as_string())
            return True
        except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
            logger.warning("Email send failed: %s", type(exc).__name__)
            return False

"""Outbound notification channels (Telegram, Discord, Slack, e-mail).

All channels are opt-in and disabled by default. Credentials are never stored
in code or config files -- they are resolved from environment variables via the
``${ENV:VAR}`` placeholders in ``config.yaml``. A run only notifies when the
health score is at/below the configured threshold or errors occurred, so the
channels stay quiet on healthy runs.
"""

from __future__ import annotations

from .base import Level, Notification, Notifier
from .manager import NotificationManager

__all__ = ["Level", "Notification", "NotificationManager", "Notifier"]

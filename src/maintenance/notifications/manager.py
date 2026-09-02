"""Notification dispatch manager.

Builds the set of enabled channels from configuration and fans a single
:class:`Notification` out to all of them, honouring the
``notify_below_health`` gate so healthy runs stay silent.
"""

from __future__ import annotations

from ..config import NotificationsConfig, PerformanceConfig
from ..logger import get_logger
from .base import Notification, Notifier
from .channels import DiscordNotifier, EmailNotifier, SlackNotifier, TelegramNotifier

logger = get_logger("notifications")


class NotificationManager:
    """Aggregates enabled channels and dispatches notifications."""

    def __init__(
        self,
        config: NotificationsConfig,
        performance: PerformanceConfig | None = None,
    ) -> None:
        self._config = config
        self._performance = performance or PerformanceConfig()
        self._channels: list[Notifier] = []
        if config.enabled:
            self._build_channels()

    def _build_channels(self) -> None:
        cfg = self._config
        perf = self._performance
        if cfg.telegram.enabled:
            self._channels.append(TelegramNotifier(cfg.telegram, perf))
        if cfg.discord.enabled:
            self._channels.append(DiscordNotifier(cfg.discord, perf))
        if cfg.slack.enabled:
            self._channels.append(SlackNotifier(cfg.slack, perf))
        if cfg.email.enabled:
            self._channels.append(EmailNotifier(cfg.email, perf))
        logger.debug("Notification channels enabled: %s", [c.name for c in self._channels])

    @property
    def enabled(self) -> bool:
        return bool(self._channels)

    def should_notify(self, *, health_score: int | None, has_errors: bool) -> bool:
        """Decide whether a run warrants a notification."""
        if not self.enabled:
            return False
        if has_errors:
            return True
        if health_score is None:
            return False
        return health_score <= self._config.notify_below_health

    def dispatch(self, notification: Notification) -> dict[str, bool]:
        """Send to every enabled channel; return ``{channel: success}``."""
        results: dict[str, bool] = {}
        for channel in self._channels:
            try:
                results[channel.name] = channel.send(notification)
            except Exception as exc:
                logger.warning("Channel %s raised: %s", channel.name, exc)
                results[channel.name] = False
        return results

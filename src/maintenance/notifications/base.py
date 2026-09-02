"""Notification value object and channel interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Literal

Level = Literal["info", "warning", "critical"]


@dataclass(slots=True)
class Notification:
    """A message to deliver across one or more channels."""

    title: str
    body: str
    level: Level = "info"
    health_score: int | None = None

    def as_text(self) -> str:
        """Plain-text rendering suitable for any channel."""
        header = f"[{self.level.upper()}] {self.title}"
        if self.health_score is not None:
            header += f" (health {self.health_score}/100)"
        return f"{header}\n\n{self.body}"


class Notifier(abc.ABC):
    """Base class for a single delivery channel."""

    name: str = "notifier"

    @abc.abstractmethod
    def send(self, notification: Notification) -> bool:
        """Deliver ``notification``; return True on success, False otherwise."""
        raise NotImplementedError

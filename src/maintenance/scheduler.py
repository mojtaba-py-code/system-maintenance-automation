"""Periodic execution of maintenance tasks.

Wraps the tiny, dependency-free ``schedule`` library (when installed) and maps
the declarative jobs from ``scheduler.jobs`` onto a callback that actually runs
a task. Kept intentionally thin: the scheduler decides *when*, the CLI's
``TaskRunner`` decides *what*.

For OS-native scheduling, :func:`generate_cron_line` and
:func:`windows_task_command` emit ready-to-use crontab / Task Scheduler
snippets so the tool can be driven by the platform scheduler instead.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from .config import ScheduledJob, SchedulerConfig
from .logger import get_logger

logger = get_logger("scheduler")

TaskCallback = Callable[[str, dict[str, Any]], None]
_EVERY_N = re.compile(r"every\s+(\d+)\s*(second|minute|hour|day|week)s?", re.IGNORECASE)


class Scheduler:
    """Registers and runs periodic jobs via the ``schedule`` library."""

    def __init__(self, config: SchedulerConfig, runner: TaskCallback) -> None:
        self._config = config
        self._runner = runner
        try:
            import schedule

            self._schedule = schedule
        except ImportError as exc:  # pragma: no cover - schedule is a core dep
            raise RuntimeError("The 'schedule' package is required for scheduling.") from exc

    def _register(self, job: ScheduledJob) -> None:
        s = self._schedule
        interval = job.interval.strip().lower()

        def _invoke() -> None:
            logger.info("Running scheduled job '%s' (task=%s)", job.name, job.task)
            try:
                self._runner(job.task, dict(job.args))
            except Exception:
                logger.exception("Scheduled job '%s' failed", job.name)

        match = _EVERY_N.match(interval)
        if match:
            count, unit = int(match.group(1)), match.group(2)
            getattr(s.every(count), f"{unit}s").do(_invoke)
        elif interval == "hourly":
            s.every().hour.do(_invoke)
        elif interval == "daily":
            job_spec = s.every().day
            if job.at:
                job_spec = job_spec.at(job.at)
            job_spec.do(_invoke)
        elif interval == "weekly":
            job_spec = s.every().week
            if job.at:
                job_spec = job_spec.at(job.at)
            job_spec.do(_invoke)
        elif interval == "monthly":
            # 'schedule' has no native monthly; approximate as every 30 days.
            s.every(30).days.do(_invoke)
        else:
            logger.warning("Unrecognised interval '%s' for job '%s'; skipping", interval, job.name)
            return
        logger.info("Registered job '%s' (%s)", job.name, interval)

    def setup(self) -> int:
        """Register all configured jobs; return the number registered."""
        self._schedule.clear()
        for job in self._config.jobs:
            self._register(job)
        return len(self._schedule.get_jobs())

    def run_forever(self, *, poll_seconds: float = 1.0) -> None:
        """Block, running due jobs until interrupted."""
        count = self.setup()
        if count == 0:
            logger.warning("No jobs registered; scheduler has nothing to do.")
            return
        logger.info("Scheduler started with %d job(s). Press Ctrl+C to stop.", count)
        try:
            while True:
                self._schedule.run_pending()
                time.sleep(poll_seconds)
        except KeyboardInterrupt:  # pragma: no cover - interactive
            logger.info("Scheduler stopped by user.")


def generate_cron_line(interval: str, command: str, *, at: str | None = None) -> str:
    """Return a crontab line for a given interval (Linux/macOS)."""
    hour, minute = 2, 30
    if at and ":" in at:
        hour, minute = (int(part) for part in at.split(":", 1))
    mapping = {
        "hourly": f"0 * * * * {command}",
        "daily": f"{minute} {hour} * * * {command}",
        "weekly": f"{minute} {hour} * * 0 {command}",
        "monthly": f"{minute} {hour} 1 * * {command}",
    }
    return mapping.get(interval.lower(), f"{minute} {hour} * * * {command}")


def windows_task_command(name: str, interval: str, command: str, *, at: str = "02:30") -> str:
    """Return a ``schtasks`` command registering a Windows scheduled task."""
    freq = {"hourly": "HOURLY", "daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY"}
    schedule_type = freq.get(interval.lower(), "DAILY")
    return (
        f'schtasks /Create /TN "{name}" /TR "{command}" '
        f"/SC {schedule_type} /ST {at} /F"
    )

"""Tests for the scheduler helpers and job registration."""

from __future__ import annotations

from maintenance.config import ScheduledJob, SchedulerConfig
from maintenance.scheduler import Scheduler, generate_cron_line, windows_task_command


def test_generate_cron_line_daily() -> None:
    line = generate_cron_line("daily", "maintenance clean", at="02:30")
    assert line == "30 2 * * * maintenance clean"


def test_generate_cron_line_hourly() -> None:
    assert generate_cron_line("hourly", "cmd").startswith("0 * * * *")


def test_windows_task_command() -> None:
    cmd = windows_task_command("nightly", "daily", "maintenance clean", at="03:00")
    assert "schtasks /Create" in cmd
    assert "/SC DAILY" in cmd
    assert "/ST 03:00" in cmd


def test_scheduler_registers_jobs() -> None:
    calls: list[str] = []
    config = SchedulerConfig(
        enabled=True,
        jobs=[
            ScheduledJob(name="j1", task="monitor", interval="hourly"),
            ScheduledJob(name="j2", task="clean", interval="every 5 minutes"),
            ScheduledJob(name="j3", task="backup", interval="daily", at="01:00"),
        ],
    )
    scheduler = Scheduler(config, lambda task, args: calls.append(task))
    count = scheduler.setup()
    assert count == 3


def test_scheduler_ignores_bad_interval() -> None:
    config = SchedulerConfig(
        enabled=True,
        jobs=[ScheduledJob(name="bad", task="monitor", interval="whenever")],
    )
    scheduler = Scheduler(config, lambda task, args: None)
    assert scheduler.setup() == 0

"""Supplementary tests exercising less-travelled but important code paths."""

from __future__ import annotations

import os
import time
from pathlib import Path

from maintenance.backup import BackupEngine
from maintenance.cleanup import CleanupEngine
from maintenance.config import ScheduledJob, SchedulerConfig, Settings
from maintenance.inspection import (
    check_updates,
    running_services,
    scheduled_tasks,
    startup_programs,
)
from maintenance.reports import ReportGenerator, build_report
from maintenance.scheduler import Scheduler


# -- cleanup extras --------------------------------------------------------- #
def test_clean_old_logs(settings: Settings, tmp_path: Path) -> None:
    logs = tmp_path / "oldlogs"
    logs.mkdir()
    old = logs / "app.log"
    old.write_text("stale")
    past = time.time() - 60 * 86400
    os.utime(old, (past, past))
    settings.cleanup.log_directories = [str(logs)]
    settings.cleanup.log_max_age_days = 30
    settings.cleanup.quarantine = False
    engine = CleanupEngine(settings.cleanup, settings.security, quarantine_dir=settings.quarantine_dir)
    result = engine.clean_old_logs()
    assert result.files_deleted == 1
    assert not old.exists()


def test_recycle_bin_dry_run(settings: Settings) -> None:
    engine = CleanupEngine(
        settings.cleanup, settings.security, quarantine_dir=settings.quarantine_dir, dry_run=True
    )
    result = engine.empty_recycle_bin()
    assert any(a["action"] == "would-empty-recycle-bin" for a in result.actions)


def test_quarantine_name_collision(settings: Settings, junk_dir: Path) -> None:
    # Two different dirs contain a file with the same name -> quarantine must
    # de-duplicate rather than overwrite.
    other = junk_dir / "more"
    other.mkdir()
    (other / "a.tmp").write_text("second a")
    engine = CleanupEngine(settings.cleanup, settings.security, quarantine_dir=settings.quarantine_dir)
    engine.clean_temp()
    names = sorted(p.name for p in settings.quarantine_dir.glob("a*"))
    assert "a.tmp" in names
    assert any(n.startswith("a_") for n in names)  # collision-renamed copy


# -- backup extras ---------------------------------------------------------- #
def test_backup_targz(settings: Settings, source_dir: Path) -> None:
    settings.backup.archive_format = "tar.gz"
    engine = BackupEngine(settings.backup, settings.security, destination=settings.backup_dir)
    result = engine.backup_source(str(source_dir))
    assert result.archive_path is not None
    assert result.archive_path.endswith(".tar.gz")
    assert result.verified is True


# -- inspection collectors (best-effort; assert they never raise) ----------- #
def test_inspection_collectors_return_lists() -> None:
    assert isinstance(running_services(timeout=15), list)
    assert isinstance(startup_programs(timeout=15), list)
    assert isinstance(scheduled_tasks(timeout=15), list)
    assert isinstance(check_updates(timeout=15), list)


# -- reports extras --------------------------------------------------------- #
def test_report_with_nested_sections(tmp_path: Path) -> None:
    report = build_report(
        run_id="r2",
        command="disk",
        environment="test",
        dry_run=False,
        duration_s=1.0,
        sections={"disk": {"analyses": [{"root": "/tmp", "files": [1, 2, 3]}]}},
    )
    gen = ReportGenerator(tmp_path)
    paths = gen.write(report, ["markdown", "csv"])
    md = paths["markdown"].read_text(encoding="utf-8")
    assert "## Disk" in md
    csv_text = paths["csv"].read_text(encoding="utf-8")
    assert "key,value" in csv_text


# -- scheduler runner invocation -------------------------------------------- #
def test_scheduler_runner_invoked() -> None:
    calls: list[tuple[str, dict]] = []
    config = SchedulerConfig(
        enabled=True,
        jobs=[ScheduledJob(name="j", task="monitor", interval="every 1 minutes", args={"k": 1})],
    )
    scheduler = Scheduler(config, lambda task, args: calls.append((task, args)))
    scheduler.setup()
    scheduler._schedule.run_all()  # force-run regardless of timing
    assert calls == [("monitor", {"k": 1})]
    scheduler._schedule.clear()

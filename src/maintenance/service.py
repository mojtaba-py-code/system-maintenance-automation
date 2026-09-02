"""Application service layer -- the orchestrator used by CLI, web and scheduler.

:class:`MaintenanceService` wires configuration, logging, the SQLite history
store, the security guard and every engine together, and exposes coarse-grained
operations (``clean``, ``backup``, ``monitor``, ``analyze_disk``, ``scan``,
``report``). Keeping this logic out of the CLI makes it reusable and testable:
tests drive the service directly; the CLI is a thin argument parser on top.

The service is a context manager so the database connection is always closed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backup import BackupEngine, BackupResult
from .cleanup import CleanupEngine, CleanupResult
from .config import Settings
from .database import DatabaseLike, open_database
from .disk import DiskAnalysis, DiskAnalyzer
from .hashing import hash_file
from .health import HealthReport, compute_health
from .inspection import InspectionResult, security_scan
from .logger import configure_logging, get_logger
from .monitor import MonitorSnapshot, SystemMonitor
from .notifications import Level, Notification, NotificationManager
from .reports import ReportGenerator, build_report
from .security import PathGuard
from .systeminfo import collect_system_info
from .utils import Timer, human_duration

logger = get_logger("service")


@dataclass(slots=True)
class RunOutcome:
    """The consolidated result of a single service operation."""

    run_id: str
    command: str
    duration_s: float = 0.0
    dry_run: bool = False
    sections: dict[str, Any] = field(default_factory=dict)
    health: HealthReport | None = None
    errors: list[str] = field(default_factory=list)
    report_paths: dict[str, str] = field(default_factory=dict)

    def to_report_dict(self, settings: Settings) -> dict[str, Any]:
        return build_report(
            run_id=self.run_id,
            command=self.command,
            environment=settings.app.environment,
            dry_run=self.dry_run,
            duration_s=self.duration_s,
            system=collect_system_info().to_dict(),
            health=self.health.to_dict() if self.health else {},
            sections=self.sections,
            errors=self.errors,
        )


class MaintenanceService:
    """High-level orchestrator over the maintenance engines."""

    def __init__(
        self,
        settings: Settings,
        *,
        dry_run: bool = False,
        force: bool = False,
        silent: bool = False,
        configure_logs: bool = True,
    ) -> None:
        self.settings = settings
        self.dry_run = dry_run
        self.force = force
        settings.ensure_directories()
        if configure_logs:
            configure_logging(settings.logging, settings.log_dir, silent=silent)
        self.guard = PathGuard(settings.security)
        self.db: DatabaseLike = open_database(
            settings.database_path, enabled=settings.database.enabled
        )
        self._reporter = ReportGenerator(settings.report_dir)
        self.notifier = NotificationManager(settings.notifications, settings.performance)

    # -- lifecycle --------------------------------------------------------- #
    def __enter__(self) -> MaintenanceService:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _new_run_id() -> str:
        return uuid.uuid4().hex[:12]

    @property
    def _workers(self) -> int:
        return self.settings.performance.worker_count

    # -- notifications ------------------------------------------------------ #
    def notify(self, outcome: RunOutcome) -> dict[str, bool]:
        """Dispatch ``outcome`` to the configured notification channels.

        Silent by design: a dry run never notifies (nothing happened), and a
        healthy, error-free run is filtered out by ``notify_below_health`` so
        the channels stay useful instead of becoming noise people mute.

        Delivery failures are logged, never raised -- an unreachable webhook
        must not turn a successful maintenance run into a failed one.
        """
        health_score = outcome.health.score if outcome.health else None
        if self.dry_run or not self.notifier.should_notify(
            health_score=health_score, has_errors=bool(outcome.errors)
        ):
            return {}
        level: Level = "critical" if outcome.errors else "warning"
        lines = [
            f"Command: {outcome.command}",
            f"Run id: {outcome.run_id}",
            f"Host: {collect_system_info().hostname}",
            f"Duration: {human_duration(outcome.duration_s)}",
        ]
        if outcome.errors:
            lines.append("")
            lines.append(f"Errors ({len(outcome.errors)}):")
            # Cap the body: a run with thousands of errors must not produce a
            # message that the channel rejects outright.
            lines.extend(f"  - {err}" for err in outcome.errors[:10])
            if len(outcome.errors) > 10:
                lines.append(f"  ... and {len(outcome.errors) - 10} more")
        try:
            return self.notifier.dispatch(
                Notification(
                    title=f"Maintenance '{outcome.command}' finished",
                    body="\n".join(lines),
                    level=level,
                    health_score=health_score,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Notification dispatch failed: %s", exc)
            return {}

    # -- operations -------------------------------------------------------- #
    def clean(
        self,
        *,
        temp: bool = True,
        old_logs: bool = False,
        duplicates: bool = False,
        empty_dirs: bool = False,
        recycle_bin: bool = False,
    ) -> tuple[RunOutcome, CleanupResult]:
        """Run cleanup operations and persist the outcome."""
        run_id = self._new_run_id()
        self.db.start_run(run_id, "clean", environment=self.settings.app.environment, dry_run=self.dry_run)
        engine = CleanupEngine(
            self.settings.cleanup,
            self.settings.security,
            quarantine_dir=self.settings.quarantine_dir,
            guard=self.guard,
            dry_run=self.dry_run,
            workers=self._workers,
        )
        with Timer() as timer:
            result = engine.run(
                temp=temp,
                old_logs=old_logs,
                duplicates=duplicates,
                empty_dirs=empty_dirs,
                recycle_bin=recycle_bin,
            )
        outcome = RunOutcome(
            run_id=run_id,
            command="clean",
            duration_s=timer.elapsed,
            dry_run=self.dry_run,
            sections={"cleanup": result.to_dict(include_actions=self.settings.report.include_file_lists)},
            errors=result.errors,
        )
        if not self.dry_run:
            self.db.record_cleanup(
                run_id,
                files_deleted=result.files_deleted,
                bytes_reclaimed=result.bytes_reclaimed,
                duplicates_removed=result.duplicates_removed,
                empty_dirs_removed=result.empty_dirs_removed,
            )
        for err in result.errors:
            self.db.record_error("cleanup", err, run_id=run_id)
        self.db.finish_run(run_id, status="ok", duration_s=timer.elapsed, health_score=None)
        self.notify(outcome)
        return outcome, result

    def backup(self) -> tuple[RunOutcome, list[BackupResult]]:
        """Back up all configured sources and persist the outcome."""
        run_id = self._new_run_id()
        self.db.start_run(run_id, "backup", environment=self.settings.app.environment, dry_run=self.dry_run)
        engine = BackupEngine(
            self.settings.backup,
            self.settings.security,
            destination=self.settings.backup_dir,
            guard=self.guard,
            dry_run=self.dry_run,
        )
        with Timer() as timer:
            results = engine.run()
        errors = [e for r in results for e in r.errors]
        outcome = RunOutcome(
            run_id=run_id,
            command="backup",
            duration_s=timer.elapsed,
            dry_run=self.dry_run,
            sections={"backup": {"sources": [r.to_dict() for r in results]}},
            errors=errors,
        )
        if not self.dry_run:
            for r in results:
                if r.archive_path:
                    self.db.record_backup(
                        run_id,
                        source=r.source,
                        archive_path=r.archive_path,
                        files_count=r.files_count,
                        bytes_total=r.bytes_total,
                        incremental=r.incremental,
                        verified=r.verified,
                    )
        for err in errors:
            self.db.record_error("backup", err, run_id=run_id)
        status = "error" if errors else "ok"
        self.db.finish_run(run_id, status=status, duration_s=timer.elapsed, health_score=None)
        self.notify(outcome)
        return outcome, results

    def monitor(self) -> tuple[RunOutcome, MonitorSnapshot, HealthReport]:
        """Take a resource snapshot, compute health, and persist it."""
        run_id = self._new_run_id()
        self.db.start_run(run_id, "monitor", environment=self.settings.app.environment, dry_run=self.dry_run)
        mon = SystemMonitor(self.settings.monitor)
        with Timer() as timer:
            snapshot = mon.snapshot()
        health = compute_health(snapshot, self.settings.monitor, error_count=0)
        for disk in snapshot.disks:
            self.db.record_disk(run_id, disk.path, disk.total, disk.used, disk.free, disk.percent)
        outcome = RunOutcome(
            run_id=run_id,
            command="monitor",
            duration_s=timer.elapsed,
            sections={"monitor": snapshot.to_dict()},
            health=health,
            errors=[],
        )
        self.db.finish_run(run_id, status="ok", duration_s=timer.elapsed, health_score=health.score)
        self.notify(outcome)
        return outcome, snapshot, health

    def analyze_disk(self, paths: list[str]) -> tuple[RunOutcome, list[DiskAnalysis]]:
        """Analyse storage usage of the given paths."""
        run_id = self._new_run_id()
        self.db.start_run(run_id, "disk", environment=self.settings.app.environment, dry_run=self.dry_run)
        analyzer = DiskAnalyzer(self.settings.disk, self.settings.security, guard=self.guard)
        analyses: list[DiskAnalysis] = []
        with Timer() as timer:
            for path in paths:
                analyses.append(analyzer.usage(path))
        outcome = RunOutcome(
            run_id=run_id,
            command="disk",
            duration_s=timer.elapsed,
            sections={"disk": {"analyses": [a.to_dict() for a in analyses]}},
        )
        self.db.finish_run(run_id, status="ok", duration_s=timer.elapsed, health_score=None)
        return outcome, analyses

    def scan(self, *, deep: bool = False) -> tuple[RunOutcome, InspectionResult]:
        """Run a read-only security/system inspection."""
        run_id = self._new_run_id()
        self.db.start_run(run_id, "scan", environment=self.settings.app.environment, dry_run=self.dry_run)
        with Timer() as timer:
            inspection = security_scan(self.settings.performance.command_timeout, deep=deep)
        outcome = RunOutcome(
            run_id=run_id,
            command="scan",
            duration_s=timer.elapsed,
            sections={"scan": inspection.to_dict()},
        )
        self.db.finish_run(run_id, status="ok", duration_s=timer.elapsed, health_score=None)
        return outcome, inspection

    def verify_backups(self) -> tuple[RunOutcome, dict[str, Any]]:
        """Verify integrity of existing backup archives in the destination."""
        import tarfile
        import zipfile

        run_id = self._new_run_id()
        self.db.start_run(run_id, "verify", environment=self.settings.app.environment, dry_run=self.dry_run)
        checked: list[dict[str, Any]] = []
        errors: list[str] = []
        with Timer() as timer:
            for archive in sorted(self.settings.backup_dir.glob("*")):
                if archive.suffix == ".zip":
                    try:
                        with zipfile.ZipFile(archive) as zf:
                            ok = zf.testzip() is None
                    except (OSError, zipfile.BadZipFile) as exc:
                        ok, errors = False, [*errors, f"{archive.name}: {exc}"]
                elif archive.suffixes[-2:] in ([".tar", ".gz"], [".tar", ".bz2"]):
                    try:
                        with tarfile.open(archive) as tf:
                            tf.getmembers()
                            ok = True
                    except (OSError, tarfile.TarError) as exc:
                        ok, errors = False, [*errors, f"{archive.name}: {exc}"]
                else:
                    continue
                checked.append({"archive": archive.name, "ok": ok})
        section = {"archives_checked": len(checked), "results": checked}
        outcome = RunOutcome(
            run_id=run_id,
            command="verify",
            duration_s=timer.elapsed,
            sections={"verify": section},
            errors=errors,
        )
        for err in errors:
            self.db.record_error("verify", err, run_id=run_id)
        self.db.finish_run(
            run_id, status="error" if errors else "ok", duration_s=timer.elapsed, health_score=None
        )
        self.notify(outcome)
        return outcome, section

    @staticmethod
    def file_hash(path: str) -> str:
        """SHA-256 of a single file (thin wrapper for the CLI)."""
        return hash_file(Path(path))

    # -- reporting --------------------------------------------------------- #
    def write_reports(self, outcome: RunOutcome, formats: list[str] | None = None) -> dict[str, Path]:
        """Persist ``outcome`` as report files; record them in the database."""
        fmts = formats or self.settings.report.formats
        report = outcome.to_report_dict(self.settings)
        paths = self._reporter.write(report, list(fmts))
        for fmt, path in paths.items():
            outcome.report_paths[fmt] = str(path)
            self.db.record_report(outcome.run_id, fmt, str(path))
        return paths

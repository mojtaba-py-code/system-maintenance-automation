"""End-to-end integration tests driving the service layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from maintenance.config import Settings
from maintenance.service import MaintenanceService

pytestmark = pytest.mark.integration


def test_clean_records_history(service: MaintenanceService, junk_dir: Path) -> None:
    outcome, result = service.clean(temp=True, duplicates=True, empty_dirs=True)
    assert result.files_deleted >= 5
    assert "cleanup" in outcome.sections
    summary = service.db.summary()
    assert summary["total_runs"] == 1
    assert summary["total_bytes_reclaimed"] > 0


def test_backup_flow(service: MaintenanceService) -> None:
    outcome, results = service.backup()
    assert results and results[0].verified
    assert service.db.summary()["total_backups"] == 1
    # Verify the freshly created archive.
    _, section = service.verify_backups()
    assert section["archives_checked"] >= 1
    assert all(r["ok"] for r in section["results"])


def test_monitor_and_report(service: MaintenanceService, settings: Settings) -> None:
    outcome, snapshot, health = service.monitor()
    assert 0 <= health.score <= 100
    paths = service.write_reports(outcome, ["json", "html"])
    assert paths["json"].exists()
    assert paths["html"].exists()
    assert outcome.report_paths


def test_disk_analysis_flow(service: MaintenanceService, source_dir: Path) -> None:
    outcome, analyses = service.analyze_disk([str(source_dir)])
    assert analyses[0].file_count == 3
    assert "disk" in outcome.sections


def test_scan_flow(service: MaintenanceService) -> None:
    outcome, inspection = service.scan(deep=False)
    assert inspection.findings
    assert "scan" in outcome.sections


def test_dry_run_leaves_files(settings: Settings, junk_dir: Path) -> None:
    with MaintenanceService(settings, dry_run=True, configure_logs=False) as svc:
        _, result = svc.clean(temp=True)
        assert result.dry_run
    assert (junk_dir / "a.tmp").exists()


# --------------------------------------------------------------------------- #
# Notification dispatch
# --------------------------------------------------------------------------- #
class _RecordingNotifier:
    """Stands in for the real channel fan-out."""

    def __init__(self, *, enabled: bool = True, threshold: int = 70) -> None:
        self.enabled = enabled
        self.threshold = threshold
        self.sent: list[object] = []

    def should_notify(self, *, health_score: int | None, has_errors: bool) -> bool:
        if not self.enabled:
            return False
        if has_errors:
            return True
        return health_score is not None and health_score <= self.threshold

    def dispatch(self, notification: object) -> dict[str, bool]:
        self.sent.append(notification)
        return {"recording": True}


def test_errors_trigger_a_critical_notification(service: MaintenanceService) -> None:
    recorder = _RecordingNotifier()
    service.notifier = recorder  # type: ignore[assignment]
    service.settings.backup.sources = ["/definitely/not/a/real/path"]

    outcome, _results = service.backup()

    assert outcome.errors
    assert len(recorder.sent) == 1
    notification = recorder.sent[0]
    assert notification.level == "critical"  # type: ignore[attr-defined]
    assert "backup" in notification.body  # type: ignore[attr-defined]


def test_a_dry_run_never_notifies(settings: Settings, junk_dir: Path) -> None:
    svc = MaintenanceService(settings, dry_run=True, configure_logs=False)
    recorder = _RecordingNotifier()
    svc.notifier = recorder  # type: ignore[assignment]
    try:
        svc.clean(temp=True)
    finally:
        svc.close()
    assert recorder.sent == [], "a preview reports nothing happened, so it says nothing"


def test_healthy_runs_stay_silent(service: MaintenanceService) -> None:
    recorder = _RecordingNotifier(threshold=0)  # nothing is ever unhealthy enough
    service.notifier = recorder  # type: ignore[assignment]
    service.monitor()
    assert recorder.sent == []


def test_notification_body_caps_the_error_list(service: MaintenanceService) -> None:
    from maintenance.service import RunOutcome

    recorder = _RecordingNotifier()
    service.notifier = recorder  # type: ignore[assignment]
    outcome = RunOutcome(
        run_id="abc123", command="clean", errors=[f"error {i}" for i in range(50)]
    )

    service.notify(outcome)

    body = recorder.sent[0].body  # type: ignore[attr-defined]
    assert "and 40 more" in body
    assert body.count("  - error") == 10


def test_a_failing_channel_does_not_fail_the_run(service: MaintenanceService) -> None:
    from maintenance.service import RunOutcome

    class _Exploding(_RecordingNotifier):
        def dispatch(self, notification: object) -> dict[str, bool]:
            raise RuntimeError("webhook unreachable")

    service.notifier = _Exploding()  # type: ignore[assignment]
    assert service.notify(RunOutcome(run_id="x", command="clean", errors=["boom"])) == {}

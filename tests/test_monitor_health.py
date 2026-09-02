"""Tests for the monitor snapshot and health scoring."""

from __future__ import annotations

from maintenance.config import MonitorConfig
from maintenance.health import compute_health
from maintenance.monitor import DiskUsage, MonitorSnapshot, SystemMonitor


def test_snapshot_has_core_fields() -> None:
    monitor = SystemMonitor(MonitorConfig(cpu_sample_interval=0.05, top_processes=3))
    snap = monitor.snapshot()
    assert 0.0 <= snap.cpu_percent <= 100.0 * (len(snap.per_cpu_percent) or 1)
    assert 0.0 <= snap.memory_percent <= 100.0
    assert isinstance(snap.disks, list)
    assert len(snap.top_cpu_processes) <= 3
    data = snap.to_dict()
    assert "memory_total_human" in data


def _fake_snapshot(**overrides: float) -> MonitorSnapshot:
    base = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "cpu_percent": 10.0,
        "per_cpu_percent": [10.0],
        "load_average": None,
        "memory_percent": 20.0,
        "memory_used": 1,
        "memory_total": 10,
        "swap_percent": 0.0,
        "disks": [DiskUsage("/", "ext4", 100, 10, 90, 10.0)],
        "net_bytes_sent": 0,
        "net_bytes_recv": 0,
        "top_cpu_processes": [],
        "top_memory_processes": [],
    }
    base.update(overrides)
    return MonitorSnapshot(**base)  # type: ignore[arg-type]


def test_health_perfect_when_idle() -> None:
    snap = _fake_snapshot()
    report = compute_health(snap, MonitorConfig())
    assert report.score == 100
    assert report.grade == "A"


def test_health_penalises_high_cpu() -> None:
    snap = _fake_snapshot(cpu_percent=100.0)
    report = compute_health(snap, MonitorConfig(cpu_threshold=50.0))
    assert report.score < 100
    assert any("CPU" in f for f in report.factors)


def test_health_penalises_full_disk() -> None:
    snap = _fake_snapshot(disks=[DiskUsage("/", "ext4", 100, 99, 1, 99.0)])
    report = compute_health(snap, MonitorConfig(disk_threshold=80.0))
    assert report.score < 90
    assert report.suggestions


def test_health_error_penalty() -> None:
    snap = _fake_snapshot()
    report = compute_health(snap, MonitorConfig(), error_count=2)
    assert report.score == 90  # 100 - 5*2


def test_grades_cover_range() -> None:
    snap = _fake_snapshot(cpu_percent=100.0, memory_percent=100.0, swap_percent=100.0,
                          disks=[DiskUsage("/", "ext4", 100, 100, 0, 100.0)])
    report = compute_health(snap, MonitorConfig(cpu_threshold=1, memory_threshold=1,
                                                swap_threshold=1, disk_threshold=1))
    assert report.grade == "F"

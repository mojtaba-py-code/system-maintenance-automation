"""System health scoring and optimisation suggestions.

Turns a :class:`~maintenance.monitor.MonitorSnapshot` (plus a run's error count)
into a single 0-100 *health score*, a letter grade, the factors that drove the
score down, and actionable suggestions. This is the number surfaced in reports,
the web dashboard, and notification thresholds.

Scoring model (transparent and deterministic):
    start at 100, subtract weighted penalties for each resource that exceeds
    its configured threshold (penalty scales with how far past the threshold
    the metric is), and subtract a fixed amount per error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import MonitorConfig
from .monitor import MonitorSnapshot
from .utils import clamp

_WEIGHTS = {"cpu": 20.0, "memory": 25.0, "swap": 10.0, "disk": 30.0}
_ERROR_PENALTY = 5.0


@dataclass(slots=True)
class HealthReport:
    score: int
    grade: str
    factors: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "grade": self.grade,
            "factors": list(self.factors),
            "suggestions": list(self.suggestions),
        }


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _penalty(value: float, threshold: float, weight: float) -> float:
    """Penalty that is zero below the threshold and grows past it.

    At ``threshold`` the penalty is 0; at 100% the full ``weight`` is applied,
    scaled linearly across the remaining headroom.
    """
    if value < threshold:
        return 0.0
    headroom = max(1.0, 100.0 - threshold)
    return weight * clamp((value - threshold) / headroom, 0.0, 1.0)


def compute_health(
    snapshot: MonitorSnapshot,
    config: MonitorConfig,
    *,
    error_count: int = 0,
) -> HealthReport:
    """Compute a :class:`HealthReport` from a monitor snapshot."""
    score = 100.0
    factors: list[str] = []
    suggestions: list[str] = []

    cpu_pen = _penalty(snapshot.cpu_percent, config.cpu_threshold, _WEIGHTS["cpu"])
    if cpu_pen:
        score -= cpu_pen
        factors.append(f"High CPU ({snapshot.cpu_percent:.0f}%)")
        top = snapshot.top_cpu_processes[0].name if snapshot.top_cpu_processes else "unknown"
        suggestions.append(f"Investigate CPU-heavy process: {top}")

    mem_pen = _penalty(snapshot.memory_percent, config.memory_threshold, _WEIGHTS["memory"])
    if mem_pen:
        score -= mem_pen
        factors.append(f"High memory ({snapshot.memory_percent:.0f}%)")
        suggestions.append("Close unused applications or add RAM; check for leaks.")

    swap_pen = _penalty(snapshot.swap_percent, config.swap_threshold, _WEIGHTS["swap"])
    if swap_pen:
        score -= swap_pen
        factors.append(f"High swap ({snapshot.swap_percent:.0f}%)")
        suggestions.append("Swap thrashing detected; reduce memory pressure.")

    worst_disk = max(snapshot.disks, key=lambda d: d.percent, default=None)
    if worst_disk is not None:
        disk_pen = _penalty(worst_disk.percent, config.disk_threshold, _WEIGHTS["disk"])
        if disk_pen:
            score -= disk_pen
            factors.append(f"Disk {worst_disk.path} at {worst_disk.percent:.0f}%")
            suggestions.append(
                f"Free space on {worst_disk.path}: run 'maintenance clean' and review large files."
            )

    if error_count:
        score -= _ERROR_PENALTY * error_count
        factors.append(f"{error_count} error(s) during run")
        suggestions.append("Review errors.log for failed operations.")

    final = round(clamp(score, 0.0, 100.0))
    if not factors:
        suggestions.append("System is healthy. No action required.")
    return HealthReport(score=final, grade=_grade(final), factors=factors, suggestions=suggestions)

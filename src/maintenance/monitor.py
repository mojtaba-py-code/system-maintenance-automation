"""Live system-resource monitoring built on :mod:`psutil`.

Produces a :class:`MonitorSnapshot` combining CPU, memory, swap, per-volume
disk usage, network counters and the top resource-consuming processes. Each
metric is compared against its configured threshold, and any breach is recorded
as an ``alert`` -- these feed the health score and notifications.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import psutil

from .config import MonitorConfig
from .logger import get_logger
from .utils import human_size, iso_now

logger = get_logger("monitor")


@dataclass(slots=True)
class DiskUsage:
    path: str
    fstype: str
    total: int
    used: int
    free: int
    percent: float


@dataclass(slots=True)
class ProcessInfo:
    pid: int
    name: str
    cpu_percent: float
    memory_percent: float
    memory_rss: int


@dataclass(slots=True)
class MonitorSnapshot:
    """A point-in-time view of system resource utilisation."""

    timestamp: str
    cpu_percent: float
    per_cpu_percent: list[float]
    load_average: list[float] | None
    memory_percent: float
    memory_used: int
    memory_total: int
    swap_percent: float
    disks: list[DiskUsage]
    net_bytes_sent: int
    net_bytes_recv: int
    top_cpu_processes: list[ProcessInfo]
    top_memory_processes: list[ProcessInfo]
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["memory_used_human"] = human_size(self.memory_used)
        data["memory_total_human"] = human_size(self.memory_total)
        return data


class SystemMonitor:
    """Collects resource snapshots and evaluates them against thresholds."""

    def __init__(self, config: MonitorConfig) -> None:
        self._cfg = config

    # -- individual metrics ------------------------------------------------ #
    def cpu_percent(self) -> float:
        return float(psutil.cpu_percent(interval=self._cfg.cpu_sample_interval))

    def per_cpu(self) -> list[float]:
        return [float(x) for x in psutil.cpu_percent(interval=None, percpu=True)]

    @staticmethod
    def load_average() -> list[float] | None:
        try:
            return list(psutil.getloadavg())
        except (AttributeError, OSError, RuntimeError):
            # Windows emulates load average via PDH counters, which can raise
            # RuntimeError on some hosts; treat as "unavailable".
            return None

    def disks(self) -> list[DiskUsage]:
        results: list[DiskUsage] = []
        watch = self._cfg.watch_paths
        if watch:
            for path in watch:
                try:
                    usage = psutil.disk_usage(path)
                    results.append(
                        DiskUsage(path, "", usage.total, usage.used, usage.free, usage.percent)
                    )
                except OSError as exc:
                    logger.debug("Cannot read disk usage for %s: %s", path, exc)
            return results
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            results.append(
                DiskUsage(
                    part.mountpoint,
                    part.fstype,
                    usage.total,
                    usage.used,
                    usage.free,
                    usage.percent,
                )
            )
        return results

    def top_processes(self) -> tuple[list[ProcessInfo], list[ProcessInfo]]:
        """Return ``(top_by_cpu, top_by_memory)`` process lists."""
        procs: list[ProcessInfo] = []
        # Prime cpu_percent counters (first call always returns 0.0).
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                proc.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for proc in psutil.process_iter(["pid", "name", "memory_percent", "memory_info"]):
            try:
                info = proc.info
                mem = info.get("memory_info")
                procs.append(
                    ProcessInfo(
                        pid=info["pid"],
                        name=info.get("name") or "?",
                        cpu_percent=proc.cpu_percent(None),
                        memory_percent=round(info.get("memory_percent") or 0.0, 2),
                        memory_rss=int(getattr(mem, "rss", 0) or 0),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        n = self._cfg.top_processes
        top_cpu = sorted(procs, key=lambda p: p.cpu_percent, reverse=True)[:n]
        top_mem = sorted(procs, key=lambda p: p.memory_rss, reverse=True)[:n]
        return top_cpu, top_mem

    # -- snapshot ---------------------------------------------------------- #
    def snapshot(self) -> MonitorSnapshot:
        """Collect a full snapshot and compute threshold alerts."""
        vmem = psutil.virtual_memory()
        try:
            swap = psutil.swap_memory().percent
        except Exception:  # pragma: no cover
            swap = 0.0
        net = psutil.net_io_counters()
        disks = self.disks()
        top_cpu, top_mem = self.top_processes()
        cpu = self.cpu_percent()

        snap = MonitorSnapshot(
            timestamp=iso_now(),
            cpu_percent=cpu,
            per_cpu_percent=self.per_cpu(),
            load_average=self.load_average(),
            memory_percent=vmem.percent,
            memory_used=int(vmem.used),
            memory_total=int(vmem.total),
            swap_percent=float(swap),
            disks=disks,
            net_bytes_sent=int(getattr(net, "bytes_sent", 0)),
            net_bytes_recv=int(getattr(net, "bytes_recv", 0)),
            top_cpu_processes=top_cpu,
            top_memory_processes=top_mem,
        )
        self._evaluate(snap)
        return snap

    def _evaluate(self, snap: MonitorSnapshot) -> None:
        cfg = self._cfg
        if snap.cpu_percent >= cfg.cpu_threshold:
            snap.alerts.append(f"CPU usage {snap.cpu_percent:.0f}% >= {cfg.cpu_threshold:.0f}%")
        if snap.memory_percent >= cfg.memory_threshold:
            snap.alerts.append(
                f"Memory usage {snap.memory_percent:.0f}% >= {cfg.memory_threshold:.0f}%"
            )
        if snap.swap_percent >= cfg.swap_threshold:
            snap.alerts.append(f"Swap usage {snap.swap_percent:.0f}% >= {cfg.swap_threshold:.0f}%")
        for disk in snap.disks:
            if disk.percent >= cfg.disk_threshold:
                snap.alerts.append(
                    f"Disk {disk.path} at {disk.percent:.0f}% >= {cfg.disk_threshold:.0f}%"
                )
        if snap.alerts:
            logger.warning("Monitor alerts: %s", "; ".join(snap.alerts))

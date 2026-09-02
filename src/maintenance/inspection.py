"""System inspection: services, startup items, scheduled tasks, updates, scan.

These are read-only, best-effort collectors that shell out to native tools
(``sc``/``systemctl``, ``schtasks``/``crontab``, ``winget``/``apt``...). Every
call is sandboxed with a timeout and degrades gracefully: an unavailable tool
yields an empty result and a debug log line, never an exception that aborts a
maintenance run.

Nothing here modifies system state -- inspection only. Applying updates or
changing services is deliberately out of scope (least-privilege).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

import psutil

from .logger import get_logger

logger = get_logger("inspection")


def _run(cmd: list[str], *, timeout: int) -> str:
    """Run a command, returning stdout (empty string on any failure)."""
    if not shutil.which(cmd[0]):
        logger.debug("Command not found: %s", cmd[0])
        return ""
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return completed.stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Command failed %s: %s", cmd, exc)
        return ""


@dataclass(slots=True)
class ListeningPort:
    port: int
    address: str
    pid: int | None
    process: str | None


@dataclass(slots=True)
class InspectionResult:
    listening_ports: list[ListeningPort] = field(default_factory=list)
    services_sample: list[str] = field(default_factory=list)
    startup_items: list[str] = field(default_factory=list)
    scheduled_tasks: list[str] = field(default_factory=list)
    available_updates: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["listening_port_count"] = len(self.listening_ports)
        return data


def listening_ports() -> list[ListeningPort]:
    """Return sockets in LISTEN state with their owning process (best-effort)."""
    results: list[ListeningPort] = []
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError) as exc:  # pragma: no cover - perms
        logger.debug("net_connections denied: %s", exc)
        return results
    for conn in connections:
        if conn.status != psutil.CONN_LISTEN or conn.laddr == ():
            continue
        name: str | None = None
        if conn.pid:
            try:
                name = psutil.Process(conn.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name = None
        results.append(
            ListeningPort(
                port=conn.laddr.port,
                address=conn.laddr.ip,
                pid=conn.pid,
                process=name,
            )
        )
    results.sort(key=lambda p: p.port)
    return results


def running_services(timeout: int = 30, *, limit: int = 25) -> list[str]:
    """Return a sample of running services / units (platform-specific)."""
    if os.name == "nt":
        out = _run(["sc", "query", "state=", "all"], timeout=timeout)
        names = [
            line.split(":", 1)[1].strip()
            for line in out.splitlines()
            if line.strip().startswith("SERVICE_NAME")
        ]
    else:
        out = _run(["systemctl", "list-units", "--type=service", "--state=running", "--no-pager"], timeout=timeout)
        names = [
            line.split()[0]
            for line in out.splitlines()
            if line.strip().endswith(".service") or ".service" in line
        ]
    return names[:limit]


def startup_programs(timeout: int = 30, *, limit: int = 25) -> list[str]:
    """Return startup/autostart entries (platform-specific, best-effort)."""
    items: list[str] = []
    if os.name == "nt":
        out = _run(
            ["reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"],
            timeout=timeout,
        )
        items = [line.strip().split()[0] for line in out.splitlines() if line.strip() and "REG_" in line]
    else:
        from pathlib import Path

        autostart = Path.home() / ".config/autostart"
        if autostart.is_dir():
            items = [p.name for p in autostart.glob("*.desktop")]
    return items[:limit]


def scheduled_tasks(timeout: int = 30, *, limit: int = 25) -> list[str]:
    """Return scheduled tasks / cron entries (platform-specific)."""
    if os.name == "nt":
        out = _run(["schtasks", "/query", "/fo", "LIST"], timeout=timeout)
        tasks = [
            line.split(":", 1)[1].strip()
            for line in out.splitlines()
            if line.strip().startswith("TaskName")
        ]
    else:
        out = _run(["crontab", "-l"], timeout=timeout)
        tasks = [line for line in out.splitlines() if line.strip() and not line.startswith("#")]
    return tasks[:limit]


def check_updates(timeout: int = 60, *, limit: int = 50) -> list[str]:
    """Return a list of available package updates (best-effort, read-only)."""
    updates: list[str] = []
    if os.name == "nt":
        out = _run(["winget", "upgrade"], timeout=timeout)
        updates = [line.strip() for line in out.splitlines()[2:] if line.strip()]
    elif shutil.which("apt"):
        out = _run(["apt", "list", "--upgradable"], timeout=timeout)
        updates = [line for line in out.splitlines() if "/" in line and "upgradable" in line]
    elif shutil.which("dnf"):
        out = _run(["dnf", "check-update", "-q"], timeout=timeout)
        updates = [line for line in out.splitlines() if line.strip()]
    elif shutil.which("brew"):
        out = _run(["brew", "outdated"], timeout=timeout)
        updates = [line for line in out.splitlines() if line.strip()]
    return updates[:limit]


def security_scan(timeout: int = 60, *, deep: bool = False) -> InspectionResult:
    """Aggregate read-only signals into an :class:`InspectionResult`.

    Findings are heuristic notes (e.g. many listening ports, updates pending),
    intended to prompt human review -- not authoritative security assessments.
    """
    result = InspectionResult()
    result.listening_ports = listening_ports()
    result.services_sample = running_services(timeout)
    result.startup_items = startup_programs(timeout)
    result.scheduled_tasks = scheduled_tasks(timeout)
    if deep:
        result.available_updates = check_updates(timeout)

    # Heuristic findings.
    external = [p for p in result.listening_ports if p.address not in {"127.0.0.1", "::1"}]
    if external:
        result.findings.append(
            f"{len(external)} service(s) listening on non-loopback interfaces "
            "(review exposure)."
        )
    if len(result.startup_items) > 15:
        result.findings.append(
            f"{len(result.startup_items)} startup items detected (may slow boot)."
        )
    if result.available_updates:
        result.findings.append(
            f"{len(result.available_updates)} package update(s) available -- apply security patches."
        )
    if not result.findings:
        result.findings.append("No obvious issues detected by heuristic scan.")
    logger.info("Security scan complete: %d finding(s)", len(result.findings))
    return result

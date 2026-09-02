"""Host and system information collection.

Aggregates static-ish facts about the machine (OS, CPU, memory capacity, boot
time, Python runtime) into a serialisable dataclass. Dynamic metrics (live CPU
%, per-process usage) live in :mod:`maintenance.monitor`.
"""

from __future__ import annotations

import platform
import socket
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import psutil


@dataclass(slots=True)
class SystemInfo:
    """A snapshot of relatively static host information."""

    hostname: str
    os_name: str
    os_release: str
    os_version: str
    architecture: str
    platform_summary: str
    python_version: str
    cpu_physical_cores: int | None
    cpu_logical_cores: int | None
    total_memory_bytes: int
    total_swap_bytes: int
    boot_time: str
    collected_at: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_system_info() -> SystemInfo:
    """Gather a :class:`SystemInfo` snapshot for the current host."""
    uname = platform.uname()
    virtual_mem = psutil.virtual_memory()
    try:
        swap = psutil.swap_memory().total
    except Exception:  # pragma: no cover - some platforms lack swap
        swap = 0
    try:
        boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat()
    except Exception:  # pragma: no cover - defensive
        boot = "unknown"

    return SystemInfo(
        hostname=socket.gethostname(),
        os_name=uname.system,
        os_release=uname.release,
        os_version=uname.version,
        architecture=platform.machine(),
        platform_summary=platform.platform(),
        python_version=sys.version.split()[0],
        cpu_physical_cores=psutil.cpu_count(logical=False),
        cpu_logical_cores=psutil.cpu_count(logical=True),
        total_memory_bytes=int(virtual_mem.total),
        total_swap_bytes=int(swap),
        boot_time=boot,
        collected_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )

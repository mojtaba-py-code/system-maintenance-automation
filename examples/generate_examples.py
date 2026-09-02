"""Regenerate the artifacts in ``examples/`` from a synthetic demo host.

The files in this directory are produced by the project's *real* renderers
(:mod:`maintenance.reports`, :mod:`maintenance.health`) so they always match
what the tool actually emits -- but the input is a fixed, entirely fictional
machine defined below. Nothing here is collected from a live host, so the
examples can be committed to a public repository without disclosing a real
hostname, user account, process list or hardware profile.

Run it after changing any report format::

    python examples/generate_examples.py

Output is deterministic: re-running without code changes produces byte-identical
files, so an unexpected diff in ``git status`` means a renderer changed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:  # allow running straight from a source checkout
    sys.path.insert(0, str(_SRC))

from maintenance.config import MonitorConfig  # noqa: E402
from maintenance.health import compute_health  # noqa: E402
from maintenance.monitor import DiskUsage, MonitorSnapshot, ProcessInfo  # noqa: E402
from maintenance.reports import ReportGenerator, build_report  # noqa: E402
from maintenance.systeminfo import SystemInfo  # noqa: E402

EXAMPLES_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# The fictional demo host. Every value below is invented.
# --------------------------------------------------------------------------- #
RUN_ID = "d3f9a17c4b02"
BASE = datetime(2026, 1, 15, 9, 12, 30, tzinfo=timezone.utc)
DEMO_ROOT = "C:\\Users\\demo\\maintenance-demo"

MEMORY_TOTAL = 17_179_869_184  # 16 GiB
MEMORY_USED = 16_287_141_888
DISK_TOTAL = 512_110_190_592  # 476 GiB
DISK_USED = 268_435_456_000


def _at(seconds: int) -> str:
    """ISO-8601 timestamp ``seconds`` after the fixed demo epoch."""
    return (BASE + timedelta(seconds=seconds)).isoformat()


def _stamp(seconds: int) -> str:
    """Log-file timestamp ``seconds`` after the fixed demo epoch."""
    return (BASE + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def demo_system_info() -> SystemInfo:
    """A fictional workstation -- no value is read from the local machine."""
    return SystemInfo(
        hostname="demo-workstation",
        os_name="Windows",
        os_release="11",
        os_version="10.0.22631",
        architecture="AMD64",
        platform_summary="Windows-11-10.0.22631-SP0",
        python_version="3.12.0",
        cpu_physical_cores=4,
        cpu_logical_cores=8,
        total_memory_bytes=MEMORY_TOTAL,
        total_swap_bytes=4_294_967_296,
        boot_time=_at(-27_000),
        collected_at=_at(105),
    )


def demo_snapshot() -> MonitorSnapshot:
    """A fictional resource snapshot with generic, non-identifying process names."""
    top_cpu = [
        ProcessInfo(4312, "app-server.exe", 84.8, 3.10, 532_676_608),
        ProcessInfo(2288, "web-browser.exe", 37.5, 4.54, 780_140_544),
        ProcessInfo(6104, "code-editor.exe", 20.0, 7.55, 1_297_612_800),
        ProcessInfo(3760, "database.exe", 13.3, 9.82, 1_687_060_480),
        ProcessInfo(5496, "python.exe", 8.4, 0.76, 130_547_712),
        ProcessInfo(1180, "indexer.exe", 4.1, 0.94, 161_480_704),
        ProcessInfo(2044, "backup-agent.exe", 2.4, 0.41, 70_451_200),
        ProcessInfo(1520, "desktop-shell.exe", 2.0, 0.28, 48_103_424),
        ProcessInfo(3684, "antivirus.exe", 1.7, 2.27, 389_939_200),
        ProcessInfo(908, "log-collector.exe", 0.9, 0.19, 32_636_928),
    ]
    top_memory = sorted(top_cpu, key=lambda p: p.memory_percent, reverse=True)
    memory_percent = round(MEMORY_USED / MEMORY_TOTAL * 100, 1)
    disk_percent = round(DISK_USED / DISK_TOTAL * 100, 1)

    return MonitorSnapshot(
        timestamp=_at(103),
        cpu_percent=68.3,
        per_cpu_percent=[62.4, 47.5, 65.6, 49.4, 71.2, 58.8, 74.1, 60.3],
        load_average=None,  # Windows exposes no load average
        memory_percent=memory_percent,
        memory_used=MEMORY_USED,
        memory_total=MEMORY_TOTAL,
        swap_percent=12.5,
        disks=[
            DiskUsage(
                path="C:\\",
                fstype="NTFS",
                total=DISK_TOTAL,
                used=DISK_USED,
                free=DISK_TOTAL - DISK_USED,
                percent=disk_percent,
            )
        ],
        net_bytes_sent=421_215_894,
        net_bytes_recv=375_439_477,
        top_cpu_processes=top_cpu,
        top_memory_processes=top_memory,
        alerts=[f"Memory usage {memory_percent:.0f}% >= 90%"],
    )


AUDIT_ENTRIES: list[tuple[int, str]] = [
    (0, f"quarantine path={DEMO_ROOT}\\cache\\render1.tmp size=13 reason=temp"),
    (0, f"quarantine path={DEMO_ROOT}\\cache\\session.bak size=2 reason=temp"),
    (0, f"quarantine path={DEMO_ROOT}\\cache\\thumbnails\\t1.tmp size=4 reason=temp"),
    (0, f"quarantine path={DEMO_ROOT}\\cache\\thumbnails\\t2.tmp size=4 reason=temp"),
    (0, f"rmdir path={DEMO_ROOT}\\cache\\thumbnails"),
    (7, "empty-recycle-bin"),
    (
        8,
        f"backup source={DEMO_ROOT}\\Documents "
        f"archive={DEMO_ROOT}\\backups\\Documents_20260115T091238Z.zip files=2",
    ),
]

RUN_ENTRIES: list[tuple[int, str, str, str]] = [
    (0, "INFO", "maintenance.cleanup", "Temp cleanup: 4 files, 23 B reclaimed"),
    (0, "INFO", "maintenance.cleanup", "Old-log cleanup: 0 files removed"),
    (0, "INFO", "maintenance.cleanup", "Empty-dir cleanup: 1 directories removed"),
    (
        8,
        "INFO",
        "maintenance.backup",
        "Backup complete: Documents_20260115T091238Z.zip (2 files, 45 B, verified)",
    ),
    (103, "WARNING", "maintenance.monitor", "Monitor alerts: Memory usage 95% >= 90%"),
    (105, "INFO", "maintenance.reports", f"Wrote json report: {DEMO_ROOT}\\reports\\report_{RUN_ID}.json"),
    (105, "INFO", "maintenance.reports", f"Wrote html report: {DEMO_ROOT}\\reports\\report_{RUN_ID}.html"),
    (105, "INFO", "maintenance.reports", f"Wrote markdown report: {DEMO_ROOT}\\reports\\report_{RUN_ID}.md"),
    (105, "INFO", "maintenance.reports", f"Wrote csv report: {DEMO_ROOT}\\reports\\report_{RUN_ID}.csv"),
]


def to_lf(data: bytes) -> bytes:
    """Strip every CR so the artifacts are byte-identical on Windows and POSIX.

    ``csv.writer`` emits ``\\r\\n`` itself, which text-mode writes on Windows
    then translate a second time into ``\\r\\r\\n`` -- so dropping all CR bytes
    is the only normalisation that is correct *and* idempotent here.
    """
    return data.replace(b"\r", b"")


def write_logs() -> list[Path]:
    """Emit the two log excerpts in the exact formats used by :mod:`maintenance.logger`."""
    audit_path = EXAMPLES_DIR / "example-audit.log"
    audit_path.write_text(
        "".join(f"{_stamp(offset)} | AUDIT | {message}\n" for offset, message in AUDIT_ENTRIES),
        encoding="utf-8",
    )

    run_path = EXAMPLES_DIR / "example-run.log"
    run_path.write_text(
        "".join(
            f"{_stamp(offset)} | {level:<8} | {name} | {message}\n"
            for offset, level, name, message in RUN_ENTRIES
        ),
        encoding="utf-8",
    )
    return [audit_path, run_path]


def main() -> int:
    snapshot = demo_snapshot()
    health = compute_health(snapshot, MonitorConfig(), error_count=0)

    report = build_report(
        run_id=RUN_ID,
        command="monitor",
        environment="workstation",
        dry_run=False,
        duration_s=3.336,
        system=demo_system_info().to_dict(),
        health=health.to_dict(),
        sections={"monitor": snapshot.to_dict()},
    )
    # build_report() stamps the current time; pin it so the output is reproducible.
    report["meta"]["generated_at"] = _at(105)

    outputs = ReportGenerator(EXAMPLES_DIR).write(
        report, ["json", "html", "markdown", "csv"], basename="example-report"
    )

    # Normalise to LF so the files are byte-identical on every OS and match the
    # eol=lf rule in .gitattributes.
    for path in list(outputs.values()) + write_logs():
        path.write_bytes(to_lf(path.read_bytes()))
        print(f"wrote {path.name}")

    print(f"\nhealth score: {health.score} (grade {health.grade})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

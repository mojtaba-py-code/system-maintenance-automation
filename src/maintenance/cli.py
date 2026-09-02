"""Command-line interface for System Maintenance Automation.

A thin ``argparse`` front-end over :class:`~maintenance.service.MaintenanceService`.
Global options (``--config``, ``--dry-run``, ``--verbose``, ``--silent``,
``--force``, ``--threads``, ``--output``) apply to every subcommand; each
subcommand adds its own switches.

Exit codes: ``0`` success, ``1`` runtime error, ``2`` usage error (argparse),
``130`` interrupted.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Sequence
from typing import Any

from . import __version__
from .config import ConfigError, Settings, find_default_config, load_settings
from .service import MaintenanceService, RunOutcome


def _enable_utf8_console() -> None:
    """Make stdout/stderr UTF-8 tolerant so Unicode glyphs never crash the CLI.

    Legacy Windows consoles default to a code page (e.g. cp1252) that cannot
    encode characters such as ``⚠`` or ``•``; writing one raises
    ``UnicodeEncodeError`` and aborts the command. Reconfiguring the streams to
    UTF-8 with ``errors="replace"`` keeps the glyphs on capable terminals and
    degrades gracefully (to ``?``) on old ones instead of failing.
    """
    for stream in (sys.stdout, sys.stderr):
        # Non-reconfigurable streams (e.g. pytest captures) are left untouched.
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


_enable_utf8_console()

try:
    from rich.console import Console
    from rich.table import Table

    _console: Console | None = Console()
except Exception:  # pragma: no cover - rich is a core dependency
    _console = None


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
def _print(message: str = "", *, style: str | None = None) -> None:
    if _console is not None:
        _console.print(message, style=style)
    else:  # pragma: no cover
        print(message)


def _error(message: str) -> None:
    if _console is not None:
        _console.print(f"[bold red]error:[/] {message}")
    else:  # pragma: no cover
        print(f"error: {message}", file=sys.stderr)


def _table(title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    if _console is not None:
        table = Table(title=title, show_lines=False, header_style="bold cyan")
        for col in columns:
            table.add_column(str(col))
        for row in rows:
            table.add_row(*(str(c) for c in row))
        _console.print(table)
    else:  # pragma: no cover
        print(title)
        print(" | ".join(columns))
        for row in rows:
            print(" | ".join(str(c) for c in row))


def _confirm(prompt: str, *, force: bool, silent: bool) -> bool:
    """Ask for confirmation unless forced. Refuse in non-interactive sessions."""
    if force:
        return True
    if silent or not sys.stdin.isatty():
        _error("Destructive action requires --force in non-interactive mode.")
        return False
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):  # pragma: no cover - interactive
        return False
    return answer in {"y", "yes"}


# --------------------------------------------------------------------------- #
# Settings bootstrap
# --------------------------------------------------------------------------- #
def _load(args: argparse.Namespace) -> Settings:
    config_path = args.config or find_default_config()
    settings = load_settings(config_path, base_dir=args.data_dir)
    if args.verbose and settings.unresolved_env_vars:
        # Verbose only, on purpose. The stock config references placeholders for
        # every optional integration, so warning unconditionally would print a
        # wall of yellow on every command and teach people to ignore warnings.
        # Every case where an unset variable is actually dangerous -- an enabled
        # channel with no credential, an exposed dashboard with no token --
        # already fails validation outright.
        _print(
            "[yellow]note:[/] unset environment variable(s) referenced by the config: "
            + ", ".join(settings.unresolved_env_vars)
        )
    if args.verbose:
        settings.logging.level = "DEBUG"
    if args.threads:
        settings.performance.threads = args.threads
    return settings


def _maybe_report(
    service: MaintenanceService, outcome: RunOutcome, args: argparse.Namespace
) -> None:
    if args.no_report:
        return
    formats = args.output or None
    paths = service.write_reports(outcome, formats)
    if paths:
        _print(
            "Reports: " + ", ".join(f"{fmt} -> {path}" for fmt, path in paths.items()),
            style="dim",
        )


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
def cmd_clean(service: MaintenanceService, args: argparse.Namespace) -> int:
    do_all = args.all
    temp = args.temp or do_all or not any(
        [args.temp, args.old_logs, args.duplicates, args.empty_dirs, args.recycle_bin]
    )
    old_logs = args.old_logs or do_all
    duplicates = args.duplicates or do_all
    empty_dirs = args.empty_dirs or do_all
    recycle_bin = args.recycle_bin or do_all

    needs_confirm = not service.dry_run and service.settings.app.require_confirmation
    if needs_confirm and not _confirm(
        "Proceed with cleanup (files may be deleted)?", force=service.force, silent=args.silent
    ):
        _print("Aborted.")
        return 1

    outcome, result = service.clean(
        temp=temp,
        old_logs=old_logs,
        duplicates=duplicates,
        empty_dirs=empty_dirs,
        recycle_bin=recycle_bin,
    )
    data = result.to_dict()
    _table(
        "Cleanup" + (" (dry-run)" if service.dry_run else ""),
        ["Metric", "Value"],
        [
            ["Files removed", data["files_deleted"]],
            ["Space reclaimed", data["bytes_reclaimed_human"]],
            ["Duplicates removed", data["duplicates_removed"]],
            ["Empty dirs removed", data["empty_dirs_removed"]],
            ["Quarantine purged", data["quarantine_purged"]],
            ["Skipped", data["skipped"]],
            ["Errors", len(data["errors"])],
        ],
    )
    if data["batch_limit_reached"]:
        _print(
            "[yellow]note:[/] stopped at the deletion batch limit; "
            "re-run to continue, or raise security.max_delete_batch."
        )
    for err in data["errors"][:10]:
        _error(err)
    _maybe_report(service, outcome, args)
    return 0


def cmd_backup(service: MaintenanceService, args: argparse.Namespace) -> int:
    outcome, results = service.backup()
    rows = [
        [
            r.source,
            r.files_count,
            r.to_dict()["bytes_total_human"],
            "yes" if r.verified else "no",
            r.archive_path or "-",
        ]
        for r in results
    ]
    _table(
        "Backup" + (" (dry-run)" if service.dry_run else ""),
        ["Source", "Files", "Size", "Verified", "Archive"],
        rows or [["(no sources configured)", 0, "0 B", "-", "-"]],
    )
    for r in results:
        for err in r.errors:
            _error(err)
    _maybe_report(service, outcome, args)
    return 1 if outcome.errors else 0


def cmd_monitor(service: MaintenanceService, args: argparse.Namespace) -> int:
    outcome, snapshot, health = service.monitor()
    _table(
        "Resource Usage",
        ["Metric", "Value"],
        [
            ["CPU", f"{snapshot.cpu_percent:.1f}%"],
            ["Memory", f"{snapshot.memory_percent:.1f}%"],
            ["Swap", f"{snapshot.swap_percent:.1f}%"],
            ["Health score", f"{health.score}/100 (grade {health.grade})"],
        ],
    )
    _table(
        "Disks",
        ["Mount", "Used %", "Free"],
        [[d.path, f"{d.percent:.0f}%", _human(d.free)] for d in snapshot.disks]
        or [["(none)", "-", "-"]],
    )
    if snapshot.alerts:
        for alert in snapshot.alerts:
            _print(f"⚠ {alert}", style="yellow")
    if health.suggestions:
        _print("\nSuggestions:", style="bold")
        for s in health.suggestions:
            _print(f"  • {s}")
    _maybe_report(service, outcome, args)
    return 0


def cmd_disk(service: MaintenanceService, args: argparse.Namespace) -> int:
    paths = args.paths or ["."]
    outcome, analyses = service.analyze_disk(paths)
    for analysis in analyses:
        data = analysis.to_dict()
        _print(f"\n[bold]{data['root']}[/] — {data['total_size_human']} in {data['file_count']} files")
        _table(
            "Largest sub-directories",
            ["Directory", "Size", "Files"],
            [[d["path"], d["size_human"], d["file_count"]] for d in data["largest_dirs"][:10]]
            or [["(none)", "-", "-"]],
        )
        if data["large_files"]:
            _table(
                "Largest files",
                ["File", "Size"],
                [[f["path"], f["size_human"]] for f in data["large_files"][:10]],
            )
    _maybe_report(service, outcome, args)
    return 0


def cmd_scan(service: MaintenanceService, args: argparse.Namespace) -> int:
    outcome, inspection = service.scan(deep=args.deep)
    _table(
        "Listening ports",
        ["Port", "Address", "Process"],
        [[p.port, p.address, p.process or "?"] for p in inspection.listening_ports[:15]]
        or [["(none)", "-", "-"]],
    )
    _print("\n[bold]Findings:[/]")
    for finding in inspection.findings:
        _print(f"  • {finding}")
    _maybe_report(service, outcome, args)
    return 0


def cmd_verify(service: MaintenanceService, args: argparse.Namespace) -> int:
    outcome, section = service.verify_backups()
    _table(
        "Backup verification",
        ["Archive", "OK"],
        [[r["archive"], "✓" if r["ok"] else "✗"] for r in section["results"]]
        or [["(no archives found)", "-"]],
    )
    _maybe_report(service, outcome, args)
    return 1 if outcome.errors else 0


def cmd_hash(service: MaintenanceService, args: argparse.Namespace) -> int:
    try:
        digest = service.file_hash(args.path)
    except OSError as exc:
        _error(str(exc))
        return 1
    _print(f"{digest}  {args.path}")
    return 0


def cmd_info(service: MaintenanceService, args: argparse.Namespace) -> int:
    from .systeminfo import collect_system_info

    info = collect_system_info().to_dict()
    _table(
        "System Information",
        ["Field", "Value"],
        [
            ["Hostname", info["hostname"]],
            ["OS", f"{info['os_name']} {info['os_release']}"],
            ["Platform", info["platform_summary"]],
            ["Architecture", info["architecture"]],
            ["Python", info["python_version"]],
            ["CPU cores", f"{info['cpu_physical_cores']} physical / {info['cpu_logical_cores']} logical"],
            ["Total memory", _human(info["total_memory_bytes"])],
            ["Boot time", info["boot_time"]],
        ],
    )
    return 0


def cmd_report(service: MaintenanceService, args: argparse.Namespace) -> int:
    summary = service.db.summary()
    _table(
        "History Summary",
        ["Metric", "Value"],
        [
            ["Total runs", summary["total_runs"]],
            ["Space reclaimed", _human(summary["total_bytes_reclaimed"])],
            ["Backups", summary["total_backups"]],
            ["Errors logged", summary["total_errors"]],
            ["Avg health", summary["avg_health_score"] if summary["avg_health_score"] is not None else "n/a"],
        ],
    )
    runs = service.db.recent_runs(limit=args.limit)
    if runs:
        _table(
            f"Recent runs (last {len(runs)})",
            ["Run", "Command", "Started", "Status", "Health"],
            [[r["run_id"], r["command"], r["started_at"], r["status"], r["health_score"] or "-"] for r in runs],
        )
    return 0


def cmd_update(service: MaintenanceService, args: argparse.Namespace) -> int:
    from .inspection import check_updates

    updates = check_updates(service.settings.performance.command_timeout)
    if not updates:
        _print("No package updates detected (or package manager unavailable).")
        return 0
    _print(f"[bold]{len(updates)} update(s) available:[/]")
    for line in updates[:40]:
        _print(f"  {line}")
    return 0


def cmd_scheduler(service: MaintenanceService, args: argparse.Namespace) -> int:
    from .scheduler import Scheduler, generate_cron_line, windows_task_command

    if args.list:
        _print("[bold]Equivalent native scheduler entries:[/]")
        for job in service.settings.scheduler.jobs:
            cmd = f"maintenance {job.task}"
            _print(f"# {job.name}")
            _print("  cron:     " + generate_cron_line(job.interval, cmd, at=job.at))
            _print("  windows:  " + windows_task_command(job.name, job.interval, cmd, at=job.at or "02:30"))
        return 0

    def runner(task: str, task_args: dict[str, Any]) -> None:
        _dispatch_scheduled(service, task, task_args)

    scheduler = Scheduler(service.settings.scheduler, runner)
    scheduler.run_forever()
    return 0


def cmd_web(service: MaintenanceService, args: argparse.Namespace) -> int:
    try:
        from .web.server import run_server
    except ImportError as exc:
        _error(f"Web dependencies not installed: {exc}. Install with: pip install -e \".[web]\"")
        return 1
    host = args.host or service.settings.web.host
    port = args.port or service.settings.web.port
    if service.settings.web.auth_required:
        _print("Dashboard authentication is [green]enabled[/]; sign in at /login.")
    else:
        _print("Dashboard authentication is [yellow]disabled[/] (loopback bind only).")
    _print(f"Starting dashboard on http://{host}:{port} (Ctrl+C to stop)")
    try:
        run_server(service.settings, host=host, port=port)
    except ValueError as exc:
        # Raised when a --host override would expose an unauthenticated server.
        _error(str(exc))
        return 1
    return 0


def _dispatch_scheduled(service: MaintenanceService, task: str, task_args: dict[str, Any]) -> None:
    """Execute a task requested by the scheduler."""
    if task == "clean":
        service.clean(temp=True, old_logs=True, duplicates=bool(task_args.get("duplicates")))
    elif task == "backup":
        service.backup()
    elif task == "monitor":
        service.monitor()
    elif task == "scan":
        service.scan()
    elif task == "report":
        outcome, *_ = service.monitor()
        service.write_reports(outcome)


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def _human(num: Any) -> str:
    from .utils import human_size

    try:
        return human_size(int(num))
    except (TypeError, ValueError):  # pragma: no cover
        return str(num)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maintenance",
        description="System Maintenance Automation — cross-platform maintenance toolkit.",
        epilog="Run 'maintenance <command> --help' for command-specific options.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", metavar="PATH", help="Path to config YAML/TOML file.")
    parser.add_argument("--data-dir", metavar="DIR", help="Override the base output directory.")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without changing anything.")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging.")
    parser.add_argument("--silent", action="store_true", help="Suppress console logging.")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompts.")
    parser.add_argument("--threads", type=int, metavar="N", help="Worker threads for hashing/scanning.")
    parser.add_argument(
        "--output",
        action="append",
        choices=["json", "csv", "html", "markdown"],
        help="Report format(s) to write (repeatable). Defaults to config.",
    )
    parser.add_argument("--no-report", action="store_true", help="Do not write report files.")

    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    p_clean = sub.add_parser("clean", help="Clean temp/cache/logs, duplicates and empty folders.")
    p_clean.add_argument("--all", action="store_true", help="Run every cleanup operation.")
    p_clean.add_argument("--temp", action="store_true", help="Clean temporary files.")
    p_clean.add_argument("--old-logs", action="store_true", help="Remove logs older than the age threshold.")
    p_clean.add_argument("--duplicates", action="store_true", help="Remove duplicate files.")
    p_clean.add_argument("--empty-dirs", action="store_true", help="Remove empty directories.")
    p_clean.add_argument("--recycle-bin", action="store_true", help="Empty the OS recycle bin / trash.")
    p_clean.set_defaults(func=cmd_clean)

    sub.add_parser("backup", help="Create verified, rotating backups.").set_defaults(func=cmd_backup)
    sub.add_parser("monitor", help="Report CPU/memory/disk/network and health.").set_defaults(func=cmd_monitor)

    p_disk = sub.add_parser("disk", help="Analyse disk usage of one or more paths.")
    p_disk.add_argument("paths", nargs="*", help="Paths to analyse (default: current directory).")
    p_disk.set_defaults(func=cmd_disk)

    p_scan = sub.add_parser("scan", help="Read-only security/system inspection.")
    p_scan.add_argument("--deep", action="store_true", help="Also check for available package updates.")
    p_scan.set_defaults(func=cmd_scan)

    sub.add_parser("verify", help="Verify integrity of existing backup archives.").set_defaults(func=cmd_verify)

    p_hash = sub.add_parser("hash", help="Print the SHA-256 of a file.")
    p_hash.add_argument("path", help="File to hash.")
    p_hash.set_defaults(func=cmd_hash)

    sub.add_parser("info", help="Show host/system information.").set_defaults(func=cmd_info)

    p_report = sub.add_parser("report", help="Show maintenance history and statistics.")
    p_report.add_argument("--limit", type=int, default=15, help="Number of recent runs to show.")
    p_report.set_defaults(func=cmd_report)

    sub.add_parser("update", help="Check for available package updates (read-only).").set_defaults(func=cmd_update)

    p_sched = sub.add_parser("scheduler", help="Run the periodic scheduler, or list native entries.")
    p_sched.add_argument("--list", action="store_true", help="Print cron / schtasks equivalents and exit.")
    p_sched.set_defaults(func=cmd_scheduler)

    p_web = sub.add_parser("web", help="Launch the web dashboard + REST API.")
    p_web.add_argument("--host", help="Bind host (default from config).")
    p_web.add_argument("--port", type=int, help="Bind port (default from config).")
    p_web.set_defaults(func=cmd_web)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Program entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = _load(args)
    except ConfigError as exc:
        _error(str(exc))
        return 1

    try:
        with MaintenanceService(
            settings,
            dry_run=args.dry_run,
            force=args.force,
            silent=args.silent,
        ) as service:
            return int(args.func(service, args))
    except KeyboardInterrupt:  # pragma: no cover - interactive
        _error("Interrupted.")
        return 130
    except Exception as exc:
        _error(f"{type(exc).__name__}: {exc}")
        if args.verbose and _console is not None:
            _console.print_exception()
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

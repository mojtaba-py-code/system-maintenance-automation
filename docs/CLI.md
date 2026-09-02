# CLI Guide

```
maintenance [global options] <command> [command options]
```

## Global options

| Option | Description |
|--------|-------------|
| `--config PATH` | Config file (YAML/TOML). Auto-discovered if omitted. |
| `--data-dir DIR` | Override the base output directory. |
| `--dry-run` | Preview actions without changing anything. |
| `--verbose` | DEBUG logging (and full tracebacks on error). |
| `--silent` | Suppress console logging (files still written). |
| `--force` | Skip confirmation prompts. |
| `--threads N` | Worker threads for hashing/scanning. |
| `--output {json,csv,html,markdown}` | Report format(s); repeatable. |
| `--no-report` | Do not write report files. |
| `--version` | Print version and exit. |

Exit codes: `0` success · `1` runtime error · `2` usage error · `130` interrupted.

## Commands

### `clean`
Clean temporary files, old logs, duplicates and empty folders.

| Option | Description |
|--------|-------------|
| `--all` | Run every cleanup operation. |
| `--temp` | Temporary files (default when no flags given). |
| `--old-logs` | Logs older than the age threshold. |
| `--duplicates` | Remove duplicate files (keep the oldest). |
| `--empty-dirs` | Remove empty directories. |
| `--recycle-bin` | Empty the OS recycle bin / trash. |

```bash
maintenance --dry-run clean --all      # preview
maintenance clean --temp --duplicates  # targeted
```

### `backup`
Create verified, rotating backups of the configured sources.

```bash
maintenance backup
maintenance --dry-run backup           # see what would be archived
```

### `monitor`
Report CPU/memory/disk/network usage, alerts and a health score.

```bash
maintenance monitor
maintenance --output html monitor      # also write an HTML report
```

### `disk`
Analyse disk usage of one or more paths (default: current directory).

```bash
maintenance disk ~/Downloads /var/log
```

### `scan`
Read-only security/system inspection (listening ports, services, startup items,
scheduled tasks, and — with `--deep` — available updates).

```bash
maintenance scan
maintenance scan --deep
```

### `verify`
Verify the integrity of existing backup archives in the destination.

### `hash`
Print the SHA-256 of a file.

```bash
maintenance hash path/to/file.iso
```

### `info`
Show host/system information (OS, CPU, memory, boot time, Python).

### `report`
Show maintenance history and statistics from the SQLite store.

```bash
maintenance report --limit 30
```

### `update`
Check for available package updates (read-only; `winget`/`apt`/`dnf`/`brew`).

### `scheduler`
Run the periodic scheduler, or print native scheduler equivalents.

```bash
maintenance scheduler          # run jobs from config
maintenance scheduler --list   # print cron + schtasks lines
```

### `web`
Launch the FastAPI dashboard + REST API (requires the `web` extra).

```bash
maintenance web --host 127.0.0.1 --port 8787
```

## Reports

Any command that produces an outcome can also write reports:

```bash
maintenance --output json --output html --output markdown monitor
```

Reports land in `report.directory` (default `reports/`) named
`report_<run_id>.<ext>`.

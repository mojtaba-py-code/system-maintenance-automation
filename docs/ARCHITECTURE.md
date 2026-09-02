# Architecture

## Goals

* **Cross-platform** — one code path for Windows, Linux and macOS; OS-specific
  behaviour is isolated behind small helpers.
* **Safe by default** — every destructive operation is validated by a security
  guard and defaults to recoverable quarantine + dry-run previews.
* **Testable** — no global state; configuration and collaborators are injected.
* **Extensible** — engines are independent and discoverable; adding a new
  maintenance capability means adding a module, not editing a monolith.

## Layers

```
             ┌─────────────┐   ┌──────────────┐   ┌───────────────┐
   entry     │   cli.py    │   │ web/server.py│   │ scheduler.py  │
   points    │ (argparse)  │   │  (FastAPI)   │   │  (schedule)   │
             └──────┬──────┘   └──────┬───────┘   └──────┬────────┘
                    └──────────────┬──┴──────────────────┘
                                   ▼
                        ┌───────────────────────┐
   orchestration        │      service.py       │  MaintenanceService
                        │  (composition + DI)   │
                        └───────────┬───────────┘
              ┌──────────┬──────────┼───────────┬───────────┐
              ▼          ▼          ▼           ▼           ▼
          cleanup    backup     monitor       disk      inspection   ← engines
              │          │          │           │           │
              └──────────┴────┬─────┴───────────┴───────────┘
                              ▼
              security.py (PathGuard) · hashing.py · utils.py       ← primitives
                              ▼
          config.py (pydantic) · logger.py · database.py (SQLite)   ← infrastructure
```

## Key components

### `config.py`
A tree of pydantic models validated at load time. Secrets are referenced via
`${ENV:VAR}` placeholders and resolved from the environment. `Settings` also
computes absolute output paths (`log_dir`, `report_dir`, `backup_dir`, …) from a
single `base_dir`.

### `security.py` — `PathGuard`
The single choke point for "may I touch this path?". It knows OS-critical
directories, honours user protected paths and allow-lists, blocks path
traversal (`is_within`), and treats filesystem/drive anchors (`/`, `C:\`)
specially so protecting the root does not protect *every* file on the volume.

### `service.py` — `MaintenanceService`
The orchestrator. It owns the database connection and the guard, constructs the
right engine for each operation, records history, and returns a uniform
`RunOutcome` that the report generator can serialise. This is the seam the CLI,
web layer and scheduler all share.

### Engines
Each engine has one responsibility and returns a serialisable result dataclass:

| Engine | Result | Notes |
|--------|--------|-------|
| `CleanupEngine` | `CleanupResult` | dry-run, quarantine, duplicate detection |
| `BackupEngine` | `BackupResult` | incremental manifest, verify, rotation |
| `SystemMonitor` | `MonitorSnapshot` | threshold alerts feed the health score |
| `DiskAnalyzer` | `DiskAnalysis` | usage tree, large/old files |
| `inspection` | `InspectionResult` | read-only; ports/services/updates |

### `database.py`
A thin SQLite repository with an idempotent schema. A `NullDatabase` no-op is
returned when persistence is disabled, so callers never branch on availability.

## Data flow (a `clean` run)

1. CLI parses args → builds `Settings` → constructs `MaintenanceService`.
2. Service starts a `runs` row, builds a `CleanupEngine` with the shared guard.
3. Engine walks temp dirs, and for each candidate checks the run's deletion
   budget (`security.max_delete_batch`) and calls `guard.check_deletable`
   before moving it to quarantine (or previewing in dry-run).
4. Engine purges quarantined entries past `quarantine_retention_days`, closing
   the recovery window on earlier runs.
5. Service records `cleanup_history`, finishes the run, and optionally renders
   reports and dispatches a notification if the health gate is crossed.

## Concurrency

Hashing and scanning are I/O-bound, so a `ThreadPoolExecutor` (sized from
`performance.threads`, default `os.cpu_count()`) parallelises them effectively
despite the GIL.

## Extending

Add a new engine module returning a dataclass with `to_dict()`, wire a method on
`MaintenanceService`, and add a subcommand in `cli.py`. Because the guard and
config are injected, the new engine is unit-testable without touching the disk
beyond a `tmp_path`.

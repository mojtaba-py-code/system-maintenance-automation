# Developer Guide

## Setup

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev,web,notify]"
```

## Quality gates

Every change must keep all three green:

```bash
ruff check src tests examples   # lint & import order
mypy                      # strict type-check (src/maintenance)
pytest --cov              # tests + coverage
```

Configuration for all three lives in `pyproject.toml`. mypy runs in strict mode
(`disallow_untyped_defs`, `warn_return_any`, …). ruff enforces a curated rule
set (`E,F,W,I,N,UP,B,C4,SIM,PTH,RUF`).

## Project layout

```
src/maintenance/
  config.py       typed settings (pydantic) + env resolution
  logger.py       console / rotating / error / audit logging
  utils.py        formatting, timing, retry, file iteration
  security.py     PathGuard — the destructive-action choke point
  hashing.py      chunked, parallel SHA-256
  systeminfo.py   static host facts
  database.py     SQLite repository (+ NullDatabase)
  cleanup.py      CleanupEngine
  backup.py       BackupEngine
  monitor.py      SystemMonitor
  disk.py         DiskAnalyzer
  inspection.py   read-only service/port/update collectors
  health.py       health scoring
  reports.py      JSON/CSV/HTML/Markdown
  scheduler.py    periodic execution + cron/schtasks emitters
  service.py      MaintenanceService orchestrator
  cli.py          argparse front-end
  notifications/  telegram/discord/slack/email + manager
  web/            FastAPI app + dashboard template
```

## Conventions

* **No global state.** Pass `Settings` and collaborators explicitly.
* **Return dataclasses** with a `to_dict()` from engines; the report layer and
  API serialise them uniformly.
* **Guard every deletion** through `PathGuard.check_deletable`.
* **Honour `dry_run`** in any mutating path — short-circuit before the side
  effect but still populate the result so previews are accurate.
* **Type everything.** Public functions have annotations and docstrings.

## Testing

Tests live in `tests/`, run against an isolated `tmp_path`, and never touch the
real system. Shared fixtures are in `tests/conftest.py`:

* `settings` — a valid `Settings` confined to `tmp_path`
* `service` — a ready `MaintenanceService`
* `junk_dir` / `source_dir` — sample trees for cleanup/backup/disk
* `cli_config` — a minimal on-disk config for driving `main()`

Markers: `integration`, `performance`, `slow` (see `pyproject.toml`).

```bash
pytest -m integration        # only integration tests
pytest tests/test_backup.py  # a single module
pytest --cov --cov-report=html && open htmlcov/index.html
```

## Adding a new maintenance capability

1. Create `src/maintenance/<feature>.py` with an engine returning a dataclass
   that has `to_dict()`.
2. Inject `Settings` and (if it deletes) a `PathGuard`.
3. Add a method to `MaintenanceService` that runs it, records history and
   returns a `RunOutcome`.
4. Add a subcommand + handler in `cli.py`.
5. Add unit tests (engine in isolation) and an integration test (via the
   service). Keep ruff/mypy/pytest green.

## Releasing

Bump `__version__` in `src/maintenance/__init__.py` and `project.version` in
`pyproject.toml`, update the changelog section of the README, tag, and push —
CI runs the full matrix on Windows/Linux/macOS.

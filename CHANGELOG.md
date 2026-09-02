# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-09-02

Initial release.

### Cleanup
- Temporary/cache file removal, stale-log pruning, duplicate detection
  (size-grouped, then SHA-256) and empty-directory removal.
- Quarantine by default: deletions are moved, not unlinked, and purged only
  after `cleanup.quarantine_retention_days`.
- `--dry-run` previews every operation, including the deletion batch cap.
- `security.max_delete_batch` caps how many files a single run may remove.

### Backup
- Incremental archives driven by a per-source SHA-256 manifest.
- `zip`, `tar.gz` and `tar.bz2` with configurable compression level.
- Post-write verification that re-hashes every member as a stream, so archive
  size does not drive memory use.
- Rotation to `keep_last` archives per source; archives that fail verification
  are set aside as `.corrupt` rather than counted as good backups.

### Monitoring and health
- CPU, memory, swap, per-volume disk, network and top-process sampling with
  threshold alerts.
- A transparent 0–100 health score with letter grade and suggestions.

### Analysis and inspection
- Disk usage tree, largest sub-directories, large-file and old-file detection.
- Read-only inspection of services, startup items, scheduled tasks, listening
  ports and available package updates.

### Persistence and reporting
- SQLite history of every run, cleanup/backup statistics, disk metrics and errors.
- JSON, CSV, self-contained HTML and Markdown reports.

### Scheduling
- Built-in interval scheduler, plus generated `cron` and Task Scheduler snippets.

### Web dashboard and API (optional)
- FastAPI dashboard and read-only REST API.
- Token authentication covering the HTML dashboard as well as `/api/*`, with
  session-cookie login for browsers and bearer tokens for scripts.
- Failed-login lockout, nonce-based CSP and a full set of security headers.
- Binding to a non-loopback address without a token is refused at startup.

### Notifications (optional)
- Telegram, Discord, Slack and e-mail, gated by health score so healthy runs
  stay silent.
- HTTPS-only webhooks, no redirect following, verified STARTTLS, and retry with
  exponential backoff on transient failures only.

### Security
- `PathGuard` guardrails: built-in OS protections, user protected paths,
  allow-lists, symlink refusal and Zip-Slip defence.
- Secrets referenced as `${ENV:VAR}` and resolved from the environment; unset
  variables are reported, and an enabled integration with an empty credential
  is a hard configuration error.
- Append-only audit log of every destructive action.
- The sample artifacts in `examples/` are rendered from a fixed, fictional
  host by `examples/generate_examples.py`, never from a real machine, so
  publishing them cannot disclose a hostname, user account, process list or
  hardware profile. A test suite enforces this and fails if the committed
  files drift from the generator.
- See [SECURITY.md](SECURITY.md) for the full model.

### Project
- Cross-platform: Windows, Linux and macOS.
- Python 3.10–3.12, verified in CI on all three operating systems.
- 243 tests, ~86% coverage, `ruff` clean, `mypy` clean, `bandit` clean and
  `pip-audit` reporting no known vulnerabilities.

[1.0.0]: https://github.com/mojtaba-py-code/system-maintenance-automation/releases/tag/v1.0.0

# Troubleshooting

## "Destructive action requires --force in non-interactive mode."
The command would delete files, `require_confirmation` is `true`, and there is
no interactive terminal (e.g. cron, CI, a pipe). Either run it interactively,
add `--force`, or use `--dry-run` to preview. This is a safety feature.

## "Refusing to modify protected path: …"
The target is an OS-critical directory or one you listed in
`security.protected_paths`. This is intentional. If it is a legitimate custom
location, remove it from `protected_paths` — but never disable protection for
system directories.

## "Path is outside allowed roots: …"
You set `security.allowed_roots`, and the target is not inside any of them. Add
the appropriate root or clear the allow-list.

## Cleanup deleted nothing
* Check `cleanup.temp_directories` actually exist on this machine (the example
  lists both Windows and Unix paths; only the relevant ones apply).
* Confirm the files match `cleanup.temp_extensions`.
* If `security.allowed_roots` is set, the temp dirs must be inside it.
* Files matching `exclude_patterns` are skipped by design.

## Recovering a wrongly deleted file
With `cleanup.quarantine: true` (the default), deletions are **moved** to
`quarantine_dir` (default `backups/quarantine`), not destroyed. Move the file
back within `quarantine_retention_days`.

## Backup verification failed
The archive could not be re-read or a member's hash did not match. Causes:
* The source changed while the backup was running — re-run it.
* Disk full or permission error in the destination — check `logs/errors.log`.
The manifest is only committed after a successful, verified write, so a failed
verification does not corrupt incremental state.

## `getloadavg` / PDH errors on Windows
Windows emulates load average via performance counters, which can be
unavailable on some hosts. The tool treats this as "load average unavailable"
and continues; it does not affect other metrics.

## Web dashboard won't start
Install the extra: `pip install -e ".[web]"`. If the port is busy, choose
another with `--port`.

## "Refusing to expose an unauthenticated dashboard"
You asked the server to bind somewhere other than loopback (`0.0.0.0`, a LAN
address, a hostname) without an API token. This is refused deliberately — the
dashboard exposes host inventory, disk layout and run history. Either bind to
`127.0.0.1`, or set a token:

```bash
export MAINTENANCE_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

Then reference it as `api_token: "${ENV:MAINTENANCE_API_TOKEN}"`. Tokens must be
at least 32 characters. Put a TLS-terminating reverse proxy in front for
anything beyond a trusted LAN.

## Locked out of the dashboard
After `web.max_failed_logins` bad tokens, that client is refused for
`web.lockout_seconds` — the correct token included. Wait it out, or restart the
server (sessions and lockouts are in-memory and reset on restart).

## Configuration rejected on startup
The loader fails closed rather than running with a broken security setting:

* *"must use https"* — a Discord/Slack webhook URL was `http`. The URL is a
  credential; plain HTTP would expose it.
* *"enabled but ... are empty"* — a channel is `enabled: true` but its
  credential resolved to nothing, usually an unexported environment variable.
* *"would be sent in plaintext"* — SMTP credentials with `use_tls: false` to a
  remote host.

## Notifications aren't sent
* `notifications.enabled` and the specific channel's `enabled` must be `true`.
* Credentials come from environment variables referenced as `${ENV:VAR}` —
  verify they are exported. Run with `--verbose` to list unset ones.
* Notifications are gated: they only fire when the health score is at/below
  `notify_below_health` or errors occurred.
* Install the extra for web channels: `pip install -e ".[notify]"`.

## High memory while hashing huge trees
Hashing is chunked (constant memory per file), but duplicate detection holds
path metadata in memory. For very large trees, narrow the scanned roots or
raise `large_file_threshold` so fewer candidates are considered.

## Where are the logs?
`logs/maintenance-YYYY-MM-DD.log` (all), `logs/errors.log` (warnings+), and
`logs/audit.log` (every destructive/state-changing action). Run with
`--verbose` for DEBUG detail.

# Configuration Guide

Configuration lives in `config/config.yaml` (copy it from
`config/config.example.yaml`). The example file is fully commented; this guide
summarises each section and its defaults. TOML is also supported — pass
`--config config/config.toml`.

Paths accept `~` and environment variables (`%TEMP%`, `$HOME`). Relative paths
resolve against `app.data_dir`.

## `app`
| Key | Default | Meaning |
|-----|---------|---------|
| `environment` | `production` | Label shown in logs/reports |
| `data_dir` | `.` | Base directory for all runtime output |
| `require_confirmation` | `true` | Prompt before destructive actions |

## `logging`
| Key | Default | Meaning |
|-----|---------|---------|
| `level` | `INFO` | `DEBUG`…`CRITICAL` |
| `console` | `true` | Pretty console logging (rich) |
| `directory` | `logs` | Log output directory |
| `rotate_max_bytes` | `5242880` | Size before rotation |
| `rotate_backup_count` | `7` | Rotated files kept |
| `date_stamped_files` | `true` | One log file per day |
| `audit_log` | `true` | Append-only action trail (`audit.log`) |

## `database`
| Key | Default | Meaning |
|-----|---------|---------|
| `path` | `database/maintenance.db` | SQLite file |
| `enabled` | `true` | Disable to skip persistence entirely |

## `security`
| Key | Default | Meaning |
|-----|---------|---------|
| `protected_paths` | `[]` | Extra directories that may never be touched |
| `allowed_roots` | `[]` | If set, operations must stay inside these roots |
| `max_delete_batch` | `5000` | Hard cap on files removed per run; the run stops and reports on reaching it |
| `follow_symlinks` | `false` | Follow symlinks while scanning |

OS-critical directories are **always** protected regardless of this section.

## `cleanup`
| Key | Default | Meaning |
|-----|---------|---------|
| `temp_directories` | example set | Where to look for temp files |
| `temp_extensions` | `.tmp .temp .bak .dmp` | Extensions treated as disposable |
| `log_directories` / `log_max_age_days` | `[]` / `30` | Old-log pruning |
| `exclude_patterns` | `[]` | Globs never deleted |
| `exclude_dirs` | `.git node_modules …` | Directories never descended |
| `remove_empty_dirs` | `true` | Delete empties after cleanup |
| `quarantine` | `true` | Move deletions to a recoverable folder |
| `quarantine_dir` / `quarantine_retention_days` | `backups/quarantine` / `7` | Quarantine location & retention |
| `empty_recycle_bin` | `false` | Empty OS trash during `clean --all` |

## `backup`
| Key | Default | Meaning |
|-----|---------|---------|
| `sources` | `[]` | Directories to back up |
| `destination` | `backups` | Archive output |
| `archive_format` | `zip` | `zip` \| `tar.gz` \| `tar.bz2` |
| `compression_level` | `6` | 0–9 |
| `incremental` | `true` | Only archive changed files (SHA-256 manifest) |
| `keep_last` | `10` | Rotation depth per source |
| `verify_after_backup` | `true` | Re-hash every member after writing |
| `exclude_patterns` | `[]` | Globs excluded from backups |

## `monitor`
| Key | Default | Meaning |
|-----|---------|---------|
| `cpu_threshold` | `85` | % before "high CPU" alert |
| `memory_threshold` | `90` | % RAM |
| `swap_threshold` | `80` | % swap |
| `disk_threshold` | `90` | % of any volume |
| `watch_paths` | `[]` | Specific mounts (empty = all) |
| `cpu_sample_interval` | `1.0` | Seconds to sample CPU |
| `top_processes` | `10` | Processes listed by CPU/memory |

## `disk`
| Key | Default | Meaning |
|-----|---------|---------|
| `large_file_threshold` | `104857600` (100 MiB) | "large" cut-off |
| `old_file_age_days` | `365` | "old" cut-off |
| `max_depth` | `6` | Usage-tree depth |

## `report`
| Key | Default | Meaning |
|-----|---------|---------|
| `formats` | `[json, html]` | Default output formats |
| `directory` | `reports` | Report output |
| `include_file_lists` | `false` | Embed full file lists |

## `performance`
| Key | Default | Meaning |
|-----|---------|---------|
| `threads` | `0` (auto) | Worker threads for hashing/scanning |
| `command_timeout` | `120` | Timeout for external commands |
| `retry_attempts` / `retry_backoff_seconds` | `2` / `0.5` | Retries for transient failures (network errors, HTTP 429/5xx). Backoff doubles each attempt; permanent 4xx is not retried |

## `scheduler`
`enabled` plus a list of `jobs`, each with `name`, `task`
(`clean`/`backup`/`monitor`/`report`/`scan`), `interval`
(`hourly`/`daily`/`weekly`/`monthly`/`every N minutes`), optional `at` (`HH:MM`)
and `args`.

## `notifications`
`enabled` plus `notify_below_health` (only notify when the score is at/below
this or errors occurred) and per-channel blocks for `telegram`, `discord`,
`slack`, `email`. **Never** hard-code tokens — use `${ENV:VAR}`.

Validation is fail-closed:

* Webhook URLs must be `https`. A webhook URL is itself a credential.
* A channel with `enabled: true` and an empty credential is a **configuration
  error**, so a missing environment variable cannot silently disable it.
* `email` with a username but `use_tls: false` is rejected for remote SMTP
  hosts — the password would travel in plaintext. Local relays are exempt.

## `web`

| Key | Default | Meaning |
|-----|---------|---------|
| `host` | `127.0.0.1` | Bind address |
| `port` | `8787` | Bind port |
| `api_token` | `""` | Protects **both** the dashboard and `/api/*`. Minimum 32 characters |
| `session_ttl_minutes` | `60` | Browser session lifetime after `/login` |
| `max_failed_logins` | `5` | Bad tokens before a client is locked out |
| `lockout_seconds` | `300` | Lockout duration |

An empty `api_token` is only permitted while `host` is loopback. Binding to any
other address without a token is **rejected at startup** — including via a
`--host` override — rather than silently serving host inventory to the network.

Generate a token with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Environment variables

Any value written as `${ENV:NAME}` is replaced by the environment variable
`NAME` at load time. A referenced variable that is not exported resolves to an
empty string; run with `--verbose` to list them. Cases where that is actually
dangerous — an enabled notification channel with no credential, a
network-exposed dashboard with no token — fail validation outright rather than
running in a degraded state. Example:

```bash
export TELEGRAM_BOT_TOKEN="123:abc"
export TELEGRAM_CHAT_ID="456"
maintenance --output html monitor
```

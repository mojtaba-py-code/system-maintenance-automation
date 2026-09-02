# Security Policy

This tool deletes files, reads system inventory and can be exposed over HTTP.
Its security model is documented here so you can judge whether the defaults
match your risk tolerance before pointing it at anything you care about.

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.x     | ✅        |

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue.

- Open a [private security advisory](https://github.com/mojtaba-py-code/system-maintenance-automation/security/advisories/new), or
- email **mojtaba.python@gmail.com** with `SECURITY` in the subject.

Include the version, platform, a description of the impact and, if possible, a
reproduction. Expect an acknowledgement within a few days. Please give me a
reasonable window to ship a fix before disclosing publicly.

## Threat model

**In scope**

- Escaping the deletion guardrails (removing a protected or out-of-allow-list path).
- Path traversal via archive members, quarantine moves or API parameters.
- Unauthenticated access to the dashboard or REST API.
- Leaking secrets (API tokens, webhook URLs, SMTP passwords) into logs, URLs or reports.
- Denial of service through unbounded reads (huge archive members, unbounded query limits).

**Out of scope**

- An attacker who already has the privileges the tool runs with. This is a
  local administration tool; it cannot defend against its own operator.
- Physical access, or compromise of the OS the tool runs on.
- The accuracy of heuristic findings from `maintenance scan`. Those are prompts
  for human review, not authoritative assessments.

## Controls

### Destructive operations

Every delete, move and overwrite goes through `PathGuard`:

- **Built-in OS protections.** `C:\Windows`, `/etc`, `/usr`, `/boot` and friends
  are refused regardless of configuration. This layer cannot be turned off.
- **User protected paths.** Additional off-limits directories via
  `security.protected_paths`.
- **Allow-list.** When `security.allowed_roots` is non-empty, a target must
  resolve inside one of those roots or the operation is refused.
- **Symlinks are not followed** by default (`security.follow_symlinks: false`),
  so a link planted in a temp directory cannot redirect a deletion.
- **Batch cap.** `security.max_delete_batch` bounds how many files one run may
  remove. A pattern that suddenly matches far more than intended stops at the
  cap and reports it instead of running to completion.
- **Quarantine.** Deletions are *moved*, not unlinked, and stay recoverable for
  `cleanup.quarantine_retention_days` before being purged for real.
- **Dry run.** `--dry-run` reports exactly what would happen, including the
  batch cap, without touching anything.

Paths are normalised (`realpath`) *before* the check and the validated result is
what gets operated on, which narrows the check-to-use window.

### Web dashboard and API

- With `web.api_token` set, **both** the JSON API and the HTML dashboard are
  authenticated. The API accepts `Authorization: Bearer <token>`; browsers post
  the token to `/login` and receive an opaque, expiring session cookie
  (`HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS). The token itself never
  goes into a URL or a cookie.
- Tokens are compared in constant time and must be at least 32 characters.
- Repeated bad tokens lock that client out (`web.max_failed_logins`,
  `web.lockout_seconds`), so a token cannot be brute-forced over the network.
- **Binding to a non-loopback address without a token is refused at startup**,
  including via a `--host` override. This is enforced, not merely defaulted.
- Responses carry a nonce-based CSP plus `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and
  `Cache-Control: no-store`.
- The API is read-only apart from `/api/clean/preview`, which is hard-wired to
  dry-run and can never delete.
- Filesystem-enumerating endpoints fail closed: with no allow-list configured,
  enumeration is confined to the application's own base directory.
- `/api/history?limit=` is bounded, so one request cannot pull the whole table.

### Secrets

- Credentials live in environment variables, referenced from config as
  `${ENV:VAR}` and resolved at load time. They are never written to config files.
- `config/config.yaml`, `.env` and `secrets.*` are git-ignored.
- Webhook URLs must be `https`. A webhook URL is itself a credential; plain HTTP
  would expose it in transit. Redirects are never followed.
- SMTP `STARTTLS` uses a verifying default context (chain **and** hostname), and
  credentials are refused over an unencrypted link to a remote host.
- Failed notification deliveries log a status code and exception *type* only —
  never the URL, which for Telegram embeds the bot token.
- An enabled integration with an empty credential is a hard configuration error,
  so a missing environment variable cannot silently disable a channel.

### Archives

- Extraction is validated against Zip-Slip: a member resolving outside the
  extraction root is refused.
- Verification re-hashes every member as a *stream*, so a multi-gigabyte archive
  is verified in constant memory.
- An archive that fails verification is renamed `.corrupt` so it can never be
  mistaken for a good backup or occupy a slot in the rotation.
- A failed archive write removes its partial file rather than leaving a stub.

### Least privilege

`maintenance scan` and `maintenance update` **inspect** services, startup items,
scheduled tasks and available packages. Nothing there modifies system state —
applying updates and changing services is deliberately out of scope. External
commands run with an argument list (never a shell string) and a timeout.

## Hardening checklist for exposed deployments

1. Set `security.allowed_roots` to the narrowest set of paths that works.
2. Generate a strong token:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Export it as `MAINTENANCE_API_TOKEN` and reference it as
   `${ENV:MAINTENANCE_API_TOKEN}`.
3. Put the dashboard behind a TLS-terminating reverse proxy. The server speaks
   plain HTTP; the `Secure` cookie flag is only set when the request arrives
   over HTTPS.
4. Keep `cleanup.quarantine: true` and a retention window you could actually
   recover within.
5. Run as an unprivileged user. The tool needs no administrator rights for its
   default operations.
6. Review `logs/audit.log` — every destructive action is recorded there.

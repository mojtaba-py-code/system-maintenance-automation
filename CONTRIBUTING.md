# Contributing

Thanks for taking an interest. This is a tool that deletes files, so the bar for
changes is a little higher than usual — read the security notes below before
touching anything under `cleanup/`, `backup/` or `security.py`.

## Getting set up

```bash
git clone https://github.com/mojtaba-py-code/system-maintenance-automation.git
cd system-maintenance-automation

python -m venv .venv
# Windows:   .venv\Scripts\activate
# Linux/mac: source .venv/bin/activate

pip install -e ".[dev,web,notify]"
```

## Quality gates

Every change must keep all three green. CI runs them on Linux, Windows and macOS
against Python 3.10–3.12.

```bash
ruff check src tests
mypy
pytest --cov
```

- **ruff** — lint and import order. `line-length = 120`.
- **mypy** — strict-ish (`disallow_untyped_defs`, `warn_return_any`). New code
  is fully annotated; `Any` needs a reason.
- **pytest** — every test runs against `tmp_path`. A test that touches a real
  system path is a bug in the test.

## Conventions

- **Docstrings explain *why*.** The signature already says what. Reserve the
  prose for the constraint, trade-off or failure mode that made the code look
  the way it does.
- **Dependency injection, not globals.** Engines take their config and their
  `PathGuard`; nothing reads global state. That is what makes them testable.
- **The service layer is the seam.** CLI, web and scheduler are thin callers of
  `MaintenanceService`. New behaviour belongs in an engine plus the service, not
  in `cli.py`.
- **Config options must do something.** If you add a setting, wire it up and
  test it in the same change. A documented option that is never read is worse
  than no option.

## Touching anything destructive

1. **Route it through `PathGuard`.** Nothing gets deleted, moved or overwritten
   without `check_deletable`. Operate on the resolved path the guard *returns*,
   not the one you passed in.
2. **Honour `dry_run`.** A preview must report exactly what the real run would
   do — including hitting the batch cap.
3. **Honour the batch cap.** Any new deletion path goes through
   `_delete_path` (or calls `_budget_exhausted` itself).
4. **Add the negative test.** For a new guardrail, test that it *refuses* the
   dangerous case, not just that it allows the safe one.

## Security-sensitive changes

Changes to authentication, path validation, secret handling or archive
extraction need a test that demonstrates the attack being blocked. Look at
`tests/test_security_hardening.py` for the shape: each test names the accident
it prevents.

If you find a vulnerability, please don't open a public issue —
see [SECURITY.md](SECURITY.md).

## Commits and pull requests

- Present tense, imperative subject: `Enforce batch cap in duplicate removal`.
- One logical change per commit. A refactor and a fix are two commits.
- The PR description should say what breaks if the change is wrong.

# Example outputs

Sample artifacts showing what the tool produces, so you can see the output
formats without running anything yourself.

| File | Produced by |
|------|-------------|
| `example-report.html` | `maintenance --output html monitor` — self-contained HTML report with health score |
| `example-report.json` | `maintenance --output json monitor` — full machine-readable report |
| `example-report.md` | `maintenance --output markdown monitor` — Markdown summary |
| `example-report.csv` | `maintenance --output csv monitor` — flattened `key,value` rows for spreadsheets |
| `example-audit.log` | `logs/audit.log` — append-only trail of every destructive/state-changing action |
| `example-run.log` | `logs/maintenance-YYYY-MM-DD.log` — the run log |

The audit log excerpt shows a `clean --all` (files moved to quarantine, an empty
directory removed, recycle bin emptied) followed by a verified backup — exactly
the sequence a scheduled nightly job would perform.

Open `example-report.html` in a browser to see the styled report; it adapts to
light/dark themes and needs no external assets.

## How these are generated

These files are rendered by the project's **real** report writers
(`maintenance.reports`) and health scorer (`maintenance.health`), so they always
reflect what the tool actually emits. The *input*, however, is a fixed, entirely
fictional host defined in [`generate_examples.py`](generate_examples.py):
`demo-workstation`, the user `demo`, and generic process names such as
`app-server.exe`. No value here is collected from a real machine — deliberately,
so that publishing sample output can never disclose a hostname, user account,
installed-software list or hardware profile.

Regenerate them after changing any report format:

```bash
python examples/generate_examples.py
```

The output is deterministic, so an unexpected diff in `git status` afterwards
means a renderer changed — which is the point.

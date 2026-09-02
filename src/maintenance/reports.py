"""Report generation in JSON, CSV, HTML and Markdown.

A run assembles a plain, JSON-serialisable ``dict`` (via :func:`build_report`)
and hands it to :class:`ReportGenerator`, which writes one file per requested
format into the reports directory. HTML is a self-contained, styled document
(no external assets) suitable for emailing or archiving.
"""

from __future__ import annotations

import csv
import html
import io
import json
from pathlib import Path
from typing import Any

from .logger import get_logger
from .utils import human_duration, iso_now

logger = get_logger("reports")


def build_report(
    *,
    run_id: str,
    command: str,
    environment: str,
    dry_run: bool,
    duration_s: float,
    system: dict[str, Any] | None = None,
    health: dict[str, Any] | None = None,
    sections: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble a normalised report document."""
    return {
        "meta": {
            "run_id": run_id,
            "command": command,
            "environment": environment,
            "dry_run": dry_run,
            "generated_at": iso_now(),
            "duration_s": round(duration_s, 3),
            "duration_human": human_duration(duration_s),
        },
        "system": system or {},
        "health": health or {},
        "sections": sections or {},
        "errors": errors or [],
        "warnings": warnings or [],
    }


def _flatten(prefix: str, obj: Any, rows: list[tuple[str, str]]) -> None:
    """Flatten nested dict/list into ``(key, value)`` rows for CSV output."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), value, rows)
    elif isinstance(obj, list):
        rows.append((prefix, f"[{len(obj)} items]"))
        for index, value in enumerate(obj[:50]):
            _flatten(f"{prefix}[{index}]", value, rows)
    else:
        rows.append((prefix, "" if obj is None else str(obj)))


class ReportGenerator:
    """Writes report documents to disk in the requested formats."""

    def __init__(self, report_dir: Path) -> None:
        self._dir = report_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def write(self, report: dict[str, Any], formats: list[str], *, basename: str | None = None) -> dict[str, Path]:
        """Write ``report`` in each format; return ``{format: path}``."""
        stem = basename or f"report_{report['meta']['run_id']}"
        outputs: dict[str, Path] = {}
        writers = {
            "json": self._write_json,
            "csv": self._write_csv,
            "html": self._write_html,
            "markdown": self._write_markdown,
        }
        for fmt in formats:
            writer = writers.get(fmt)
            if writer is None:
                logger.warning("Unknown report format ignored: %s", fmt)
                continue
            path = writer(report, stem)
            outputs[fmt] = path
            logger.info("Wrote %s report: %s", fmt, path)
        return outputs

    # -- format writers ---------------------------------------------------- #
    def _write_json(self, report: dict[str, Any], stem: str) -> Path:
        path = self._dir / f"{stem}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return path

    def _write_csv(self, report: dict[str, Any], stem: str) -> Path:
        path = self._dir / f"{stem}.csv"
        rows: list[tuple[str, str]] = []
        _flatten("", report, rows)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["key", "value"])
        writer.writerows(rows)
        path.write_text(buffer.getvalue(), encoding="utf-8")
        return path

    def _write_markdown(self, report: dict[str, Any], stem: str) -> Path:
        path = self._dir / f"{stem}.md"
        meta = report["meta"]
        health = report.get("health", {})
        lines = [
            f"# Maintenance Report — {meta['run_id']}",
            "",
            f"- **Command:** `{meta['command']}`",
            f"- **Environment:** {meta['environment']}",
            f"- **Generated:** {meta['generated_at']}",
            f"- **Duration:** {meta['duration_human']}",
            f"- **Dry-run:** {meta['dry_run']}",
        ]
        if health:
            lines += [
                "",
                "## Health",
                f"- **Score:** {health.get('score', 'n/a')} "
                f"(grade {health.get('grade', 'n/a')})",
            ]
            for factor in health.get("factors", []):
                lines.append(f"  - {factor}")
            if health.get("suggestions"):
                lines.append("- **Suggestions:**")
                for suggestion in health["suggestions"]:
                    lines.append(f"  - {suggestion}")

        for name, payload in report.get("sections", {}).items():
            lines += ["", f"## {name.title()}"]
            lines += self._markdown_section(payload)

        if report.get("errors"):
            lines += ["", "## Errors"]
            lines += [f"- {html.escape(str(e))}" for e in report["errors"]]

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _markdown_section(payload: Any) -> list[str]:
        out: list[str] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(value, (dict, list)):
                    out.append(f"- **{key}:** {_summarise(value)}")
                else:
                    out.append(f"- **{key}:** {value}")
        elif isinstance(payload, list):
            out.append(f"- {len(payload)} item(s)")
        else:
            out.append(f"- {payload}")
        return out

    def _write_html(self, report: dict[str, Any], stem: str) -> Path:
        path = self._dir / f"{stem}.html"
        path.write_text(render_html(report), encoding="utf-8")
        return path


def _summarise(value: Any) -> str:
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in list(value.items())[:6])
    return str(value)


def _health_colour(score: int) -> str:
    if score >= 80:
        return "#1a7f37"
    if score >= 60:
        return "#9a6700"
    return "#cf222e"


def render_html(report: dict[str, Any]) -> str:
    """Render a self-contained HTML document for a report dict."""
    meta = report["meta"]
    health = report.get("health", {})
    score = int(health.get("score", 0)) if health else 0
    esc = html.escape

    section_html: list[str] = []
    for name, payload in report.get("sections", {}).items():
        rows: list[tuple[str, str]] = []
        _flatten("", payload, rows)
        body = "".join(
            f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in rows[:200]
        )
        section_html.append(
            f"<section><h2>{esc(name.title())}</h2>"
            f"<table>{body}</table></section>"
        )

    errors_html = ""
    if report.get("errors"):
        items = "".join(f"<li>{esc(str(e))}</li>" for e in report["errors"])
        errors_html = f"<section><h2>Errors</h2><ul class='errors'>{items}</ul></section>"

    suggestions_html = ""
    if health.get("suggestions"):
        items = "".join(f"<li>{esc(str(s))}</li>" for s in health["suggestions"])
        suggestions_html = f"<h3>Suggestions</h3><ul>{items}</ul>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maintenance Report {esc(meta['run_id'])}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0;
         background: #f6f8fa; color: #1f2328; }}
  header {{ background: #24292f; color: #fff; padding: 24px 32px; }}
  header h1 {{ margin: 0 0 4px; font-size: 20px; }}
  header p {{ margin: 0; opacity: .8; font-size: 13px; }}
  main {{ max-width: 960px; margin: 24px auto; padding: 0 16px; }}
  .score {{ display: inline-block; font-size: 40px; font-weight: 700;
            color: {_health_colour(score)}; }}
  .grade {{ font-size: 16px; color: #57606a; }}
  section {{ background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
             padding: 16px 20px; margin: 16px 0; }}
  h2 {{ font-size: 16px; border-bottom: 1px solid #d0d7de; padding-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  td {{ padding: 4px 8px; border-bottom: 1px solid #eaeef2;
        word-break: break-word; vertical-align: top; }}
  td:first-child {{ color: #57606a; width: 40%; font-family: ui-monospace, monospace; }}
  ul.errors li {{ color: #cf222e; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0d1117; color: #e6edf3; }}
    section {{ background: #161b22; border-color: #30363d; }}
    td {{ border-color: #21262d; }} h2 {{ border-color: #30363d; }}
  }}
</style>
</head>
<body>
<header>
  <h1>System Maintenance Report</h1>
  <p>Run {esc(meta['run_id'])} &middot; {esc(meta['command'])} &middot;
     {esc(meta['generated_at'])} &middot; {esc(meta['duration_human'])}</p>
</header>
<main>
  <section>
    <div class="score">{score}</div>
    <span class="grade">/ 100 &middot; grade {esc(str(health.get('grade', 'n/a')))}</span>
    {suggestions_html}
  </section>
  {''.join(section_html)}
  {errors_html}
</main>
</body>
</html>
"""

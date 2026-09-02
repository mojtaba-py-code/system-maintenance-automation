"""Tests for report generation."""

from __future__ import annotations

import json
from pathlib import Path

from maintenance.reports import ReportGenerator, build_report, render_html


def _sample_report() -> dict:
    return build_report(
        run_id="abc123",
        command="clean",
        environment="test",
        dry_run=False,
        duration_s=2.5,
        health={"score": 82, "grade": "B", "factors": ["High CPU (95%)"], "suggestions": ["Investigate"]},
        sections={"cleanup": {"files_deleted": 12, "bytes_reclaimed": 4096}},
        errors=["one error"],
    )


def test_build_report_structure() -> None:
    report = _sample_report()
    assert report["meta"]["run_id"] == "abc123"
    assert report["meta"]["duration_human"]
    assert report["health"]["score"] == 82


def test_write_all_formats(tmp_path: Path) -> None:
    gen = ReportGenerator(tmp_path)
    paths = gen.write(_sample_report(), ["json", "csv", "html", "markdown"])
    assert set(paths) == {"json", "csv", "html", "markdown"}
    for path in paths.values():
        assert path.exists() and path.stat().st_size > 0


def test_json_report_roundtrips(tmp_path: Path) -> None:
    gen = ReportGenerator(tmp_path)
    paths = gen.write(_sample_report(), ["json"])
    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert data["sections"]["cleanup"]["files_deleted"] == 12


def test_html_contains_score() -> None:
    html = render_html(_sample_report())
    assert "82" in html
    assert "grade B" in html
    assert "<!doctype html>" in html


def test_markdown_has_headings(tmp_path: Path) -> None:
    gen = ReportGenerator(tmp_path)
    paths = gen.write(_sample_report(), ["markdown"])
    text = paths["markdown"].read_text(encoding="utf-8")
    assert text.startswith("# Maintenance Report")
    assert "## Health" in text


def test_unknown_format_ignored(tmp_path: Path) -> None:
    gen = ReportGenerator(tmp_path)
    paths = gen.write(_sample_report(), ["json", "xml"])
    assert "xml" not in paths
    assert "json" in paths

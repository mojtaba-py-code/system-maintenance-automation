"""Tests for system information and (read-only) inspection collectors."""

from __future__ import annotations

from maintenance.inspection import ListeningPort, listening_ports, security_scan
from maintenance.systeminfo import collect_system_info


def test_collect_system_info_fields() -> None:
    info = collect_system_info()
    assert info.hostname
    assert info.os_name
    assert info.total_memory_bytes > 0
    assert info.cpu_logical_cores is None or info.cpu_logical_cores >= 1
    data = info.to_dict()
    assert data["python_version"]


def test_listening_ports_returns_list() -> None:
    ports = listening_ports()
    assert isinstance(ports, list)
    assert all(isinstance(p, ListeningPort) for p in ports)


def test_security_scan_produces_findings() -> None:
    result = security_scan(timeout=10, deep=False)
    assert result.findings  # always at least the "no issues" note
    data = result.to_dict()
    assert "listening_port_count" in data

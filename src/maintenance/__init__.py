"""System Maintenance Automation.

A cross-platform, production-grade toolkit that automates common system
maintenance tasks: temporary/cache cleanup, backups, resource monitoring,
disk analysis, integrity verification, reporting and scheduling.

The package is organised around small, single-responsibility modules that are
composed together by the :mod:`maintenance.cli` entry point:

* :mod:`maintenance.config`      -- typed, validated configuration (pydantic)
* :mod:`maintenance.logger`      -- enterprise logging (console/rotating/audit)
* :mod:`maintenance.security`    -- path guardrails & destructive-action safety
* :mod:`maintenance.hashing`     -- SHA-256 hashing utilities
* :mod:`maintenance.systeminfo`  -- host/system information collection
* :mod:`maintenance.database`    -- SQLite history & statistics store
* :mod:`maintenance.cleanup`     -- safe temporary/duplicate/empty cleanup
* :mod:`maintenance.backup`      -- incremental, compressed, verified backups
* :mod:`maintenance.monitor`     -- CPU/memory/disk/network/process monitoring
* :mod:`maintenance.disk`        -- disk usage / large & old file analysis
* :mod:`maintenance.health`      -- system health scoring
* :mod:`maintenance.reports`     -- JSON/CSV/HTML/Markdown report generation
* :mod:`maintenance.scheduler`   -- periodic execution
"""

from __future__ import annotations

__all__ = ["__author__", "__version__"]

__version__ = "1.0.0"
__author__ = "Mojtaba Karimi"

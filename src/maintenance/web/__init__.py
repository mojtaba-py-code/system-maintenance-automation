"""Optional web dashboard and REST API (FastAPI).

Import this package's :mod:`~maintenance.web.server` only when the ``web``
extra is installed (``pip install -e ".[web]"``). Nothing in the core package
imports it eagerly, so the CLI works without FastAPI present.
"""

from __future__ import annotations

__all__ = ["server"]

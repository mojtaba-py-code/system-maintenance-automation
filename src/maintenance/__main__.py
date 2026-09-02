"""Enable ``python -m maintenance`` as an alias for the console script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

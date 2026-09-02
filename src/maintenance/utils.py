"""Small, dependency-light helpers shared across the package.

Kept deliberately generic: formatting, timing, retry, and safe filesystem
primitives. Nothing here reaches out to the network or mutates global state.
"""

from __future__ import annotations

import fnmatch
import functools
import os
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def human_size(num_bytes: float) -> str:
    """Render a byte count as a human-readable string (binary units).

    >>> human_size(0)
    '0 B'
    >>> human_size(1536)
    '1.50 KiB'
    """
    value = float(num_bytes)
    sign = "-" if value < 0 else ""
    value = abs(value)
    for unit in _UNITS:
        if value < 1024.0 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{sign}{int(value)} {unit}"
            return f"{sign}{value:.2f} {unit}"
        value /= 1024.0
    return f"{sign}{value:.2f} {_UNITS[-1]}"  # pragma: no cover


def human_duration(seconds: float) -> str:
    """Render a duration in a compact human form (e.g. ``1h 02m 03s``)."""
    seconds = max(0.0, float(seconds))
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {sec:02d}s"
    if minutes:
        return f"{minutes}m {sec:02d}s"
    return f"{sec}s"


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """ISO-8601 UTC timestamp, second precision."""
    return utcnow().replace(microsecond=0).isoformat()


class Timer:
    """Context manager measuring wall-clock elapsed seconds.

    >>> with Timer() as t:
    ...     pass
    >>> t.elapsed >= 0
    True
    """

    def __init__(self) -> None:
        self.start = 0.0
        self.elapsed = 0.0

    def __enter__(self) -> Timer:
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed = time.perf_counter() - self.start


def retry(
    attempts: int = 2,
    *,
    backoff: float = 0.5,
    exceptions: tuple[type[BaseException], ...] = (OSError,),
    logger: object | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that retries a callable on transient failures.

    ``attempts`` is the number of *additional* tries after the first, so
    ``attempts=2`` means up to 3 total invocations.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> T:
            last_exc: BaseException | None = None
            for attempt in range(attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < attempts:
                        if logger is not None and hasattr(logger, "warning"):
                            logger.warning(
                                "%s failed (attempt %d/%d): %s; retrying",
                                getattr(func, "__name__", "call"),
                                attempt + 1,
                                attempts + 1,
                                exc,
                            )
                        time.sleep(backoff * (2**attempt))
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


def matches_any(name: str, patterns: Iterable[str]) -> bool:
    """True if ``name`` matches any of the glob ``patterns`` (case-insensitive)."""
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pat.lower()) for pat in patterns)


def iter_files(
    root: Path,
    *,
    exclude_dirs: Iterable[str] = (),
    exclude_patterns: Iterable[str] = (),
    follow_symlinks: bool = False,
    max_depth: int | None = None,
) -> Iterator[Path]:
    """Yield files beneath ``root`` honouring exclusions and depth.

    Directory names in ``exclude_dirs`` are pruned entirely (their subtrees are
    skipped). Files whose *name* matches any glob in ``exclude_patterns`` are
    skipped. Symlinks are not followed unless ``follow_symlinks`` is True.
    """
    exclude_dir_set = {d.lower() for d in exclude_dirs}
    patterns = tuple(exclude_patterns)
    root = Path(root)
    base_depth = len(root.parts)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        current = Path(dirpath)
        if max_depth is not None and (len(current.parts) - base_depth) >= max_depth:
            dirnames[:] = []
        # Prune excluded directories in-place so os.walk skips them.
        dirnames[:] = [d for d in dirnames if d.lower() not in exclude_dir_set]
        for filename in filenames:
            if patterns and matches_any(filename, patterns):
                continue
            path = current / filename
            if not follow_symlinks and path.is_symlink():
                continue
            yield path


def safe_stat_size(path: Path) -> int:
    """Return a file's size in bytes, or 0 if it cannot be stat-ed."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def file_mtime(path: Path) -> datetime | None:
    """Return a file's modification time as an aware UTC datetime, or None."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def age_in_days(path: Path, *, reference: datetime | None = None) -> float | None:
    """Age of ``path`` in days based on modification time, or None if unknown."""
    mtime = file_mtime(path)
    if mtime is None:
        return None
    ref = reference or utcnow()
    return (ref - mtime).total_seconds() / 86400.0


def clamp(value: float, low: float, high: float) -> float:
    """Constrain ``value`` to the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))

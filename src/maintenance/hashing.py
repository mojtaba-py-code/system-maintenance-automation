"""SHA-256 hashing utilities for integrity verification and duplicate detection.

Hashing is chunked (constant memory regardless of file size) and can run in
parallel across a thread pool -- I/O-bound work benefits from threads despite
the GIL.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import IO

_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_DEFAULT_ALGO = "sha256"


def hash_file(path: str | Path, *, algorithm: str = _DEFAULT_ALGO, chunk_size: int = _CHUNK_SIZE) -> str:
    """Return the hex digest of a file, reading it in ``chunk_size`` blocks.

    Raises
    ------
    OSError
        If the file cannot be read.
    """
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:  # noqa: PTH123 - binary streaming read
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_bytes(data: bytes, *, algorithm: str = _DEFAULT_ALGO) -> str:
    """Return the hex digest of an in-memory byte string."""
    return hashlib.new(algorithm, data).hexdigest()


def hash_stream(
    stream: IO[bytes], *, algorithm: str = _DEFAULT_ALGO, chunk_size: int = _CHUNK_SIZE
) -> str:
    """Return the hex digest of a binary stream, read in ``chunk_size`` blocks.

    Used to verify archive members without materialising them in memory -- a
    20 GiB member is hashed in 1 MiB steps, not loaded whole.
    """
    digest = hashlib.new(algorithm)
    for block in iter(lambda: stream.read(chunk_size), b""):
        digest.update(block)
    return digest.hexdigest()


def try_hash_file(path: str | Path, *, algorithm: str = _DEFAULT_ALGO) -> str | None:
    """Like :func:`hash_file` but returns ``None`` instead of raising on error."""
    try:
        return hash_file(path, algorithm=algorithm)
    except OSError:
        return None


def hash_many(
    paths: Iterable[str | Path],
    *,
    algorithm: str = _DEFAULT_ALGO,
    workers: int = 4,
) -> dict[Path, str | None]:
    """Hash many files concurrently.

    Returns a mapping of ``Path -> digest``; unreadable files map to ``None``.
    """
    path_list = [Path(p) for p in paths]
    if not path_list:
        return {}
    workers = max(1, workers)
    results: dict[Path, str | None] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for path, digest in zip(
            path_list,
            pool.map(lambda p: try_hash_file(p, algorithm=algorithm), path_list),
            strict=False,
        ):
            results[path] = digest
    return results


def verify_file(path: str | Path, expected: str, *, algorithm: str = _DEFAULT_ALGO) -> bool:
    """Return True if the file's digest matches ``expected`` (case-insensitive)."""
    actual = try_hash_file(path, algorithm=algorithm)
    return actual is not None and actual.lower() == expected.lower()

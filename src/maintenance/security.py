"""Security guardrails for destructive operations.

Everything that can delete, move, or overwrite files funnels through
:class:`PathGuard`. It enforces three layers of protection:

1. **Built-in OS protections** -- critical system directories (``C:\\Windows``,
   ``/etc``, ``/usr`` ...) can never be targeted, regardless of configuration.
2. **User protected paths** -- additional directories the operator declares
   off-limits in ``security.protected_paths``.
3. **Allow-list** -- when ``security.allowed_roots`` is non-empty, an operation
   target must resolve *inside* one of those roots or it is refused.

The guard also provides :meth:`is_within` for path-traversal defence (used when
extracting archives or moving files into quarantine) and helpers to reason
about symlinks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import SecurityConfig, expand_path


class SecurityError(Exception):
    """Raised when an operation would violate a safety guardrail."""


def _system_protected_roots() -> list[Path]:
    """Return OS-critical directories that must never be modified."""
    roots: list[Path] = []
    if os.name == "nt":
        system_drive = os.environ.get("SystemDrive", "C:")
        windir = os.environ.get("SystemRoot", rf"{system_drive}\Windows")
        candidates = [
            f"{system_drive}\\",
            windir,
            rf"{system_drive}\Program Files",
            rf"{system_drive}\Program Files (x86)",
            rf"{system_drive}\ProgramData",
            os.environ.get("SystemRoot", windir),
        ]
    else:
        candidates = [
            "/",
            "/bin",
            "/sbin",
            "/boot",
            "/dev",
            "/etc",
            "/lib",
            "/lib64",
            "/proc",
            "/sys",
            "/usr",
            "/var",
            "/root",
            "/System",  # macOS
            "/Library",  # macOS
        ]
    for candidate in candidates:
        try:
            roots.append(Path(candidate).expanduser())
        except (OSError, ValueError):  # pragma: no cover - defensive
            continue
    return roots


def _scratch_exemptions() -> list[Path]:
    """Scratch areas that sit *inside* a protected root but must stay cleanable.

    macOS places every per-user temporary and cache directory under
    ``/var/folders`` -- which ``resolve()`` turns into ``/private/var/folders``.
    ``/var`` is protected as system state, so without this carve-out the guard
    would classify the entire macOS temp/cache tree as untouchable, and the
    single most valuable cleanup target on that platform would be refused.

    Only *descendants* are exempted: ``/var/folders`` itself remains protected,
    so the tree can be cleaned but never removed wholesale.
    """
    if sys.platform == "darwin":
        return [Path("/var/folders")]
    return []


class PathGuard:
    """Validates paths before destructive or state-changing operations."""

    def __init__(self, config: SecurityConfig) -> None:
        self._config = config
        self._follow_symlinks = config.follow_symlinks
        self._protected = self._collect_protected(config)
        self._allowed_roots = [self._normalise(expand_path(p)) for p in config.allowed_roots]
        self._scratch = [self._normalise(p) for p in _scratch_exemptions()]

    # -- construction helpers --------------------------------------------- #
    @staticmethod
    def _normalise(path: Path) -> Path:
        """Absolute, symlink-resolved-if-possible, normalised path.

        Uses ``os.path.realpath`` semantics via ``Path.resolve`` but tolerates
        non-existent paths (needed because we validate targets that may be
        about to be created).
        """
        try:
            return path.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):  # pragma: no cover - defensive
            return path.expanduser().absolute()

    def _collect_protected(self, config: SecurityConfig) -> list[Path]:
        protected = list(_system_protected_roots())
        protected.extend(expand_path(p) for p in config.protected_paths)
        return [self._normalise(p) for p in protected]

    # -- public API -------------------------------------------------------- #
    def resolve(self, path: str | os.PathLike[str]) -> Path:
        """Return the normalised absolute form of ``path``."""
        return self._normalise(Path(path))

    @staticmethod
    def is_within(child: Path, parent: Path) -> bool:
        """True if ``child`` is ``parent`` or lives beneath it (traversal-safe).

        Both paths are normalised first, so ``../`` tricks cannot escape.
        """
        child_n = PathGuard._normalise(child)
        parent_n = PathGuard._normalise(parent)
        if child_n == parent_n:
            return True
        return parent_n in child_n.parents

    @staticmethod
    def _is_filesystem_anchor(path: Path) -> bool:
        """True for a filesystem root or drive root (``/``, ``C:\\``).

        Their ``parent`` is themselves. Such anchors must protect only exact
        deletion of the root itself -- never every descendant -- otherwise the
        entire drive would be considered off-limits.
        """
        return path.parent == path

    def is_protected(self, path: str | os.PathLike[str]) -> bool:
        """True if ``path`` is protected (equal to, inside, or containing a root).

        A filesystem/drive anchor (``/`` or ``C:\\``) protects only itself and
        anything that *contains* it; it does not make every file on the volume
        protected. Named system directories (``/etc``, ``C:\\Windows`` ...) also
        protect their descendants.
        """
        target = self._normalise(Path(path))
        # A descendant of a scratch area is never system-protected, even though
        # the scratch area itself lives under a protected root.
        if any(scratch in target.parents for scratch in self._scratch):
            return False
        for root in self._protected:
            if target == root:
                return True
            if target in root.parents:  # target contains (is an ancestor of) a root
                return True
            if not self._is_filesystem_anchor(root) and root in target.parents:
                return True  # target lives inside a protected (non-anchor) root
        return False

    def within_allowed_roots(self, path: str | os.PathLike[str]) -> bool:
        """True if no allow-list is configured, or ``path`` is inside one root."""
        if not self._allowed_roots:
            return True
        target = self._normalise(Path(path))
        return any(self.is_within(target, root) for root in self._allowed_roots)

    def check_deletable(self, path: str | os.PathLike[str]) -> Path:
        """Validate that ``path`` may be deleted; return its normalised form.

        Raises
        ------
        SecurityError
            If the path is protected, outside the allow-list, or an unsafe
            symlink when symlink-following is disabled.
        """
        target = self._normalise(Path(path))
        if self.is_protected(target):
            raise SecurityError(f"Refusing to modify protected path: {target}")
        if not self.within_allowed_roots(target):
            raise SecurityError(f"Path is outside allowed roots: {target}")
        if not self._follow_symlinks and Path(path).is_symlink():
            raise SecurityError(f"Refusing to follow symlink for deletion: {path}")
        return target

    def assert_extraction_safe(self, member_target: Path, extraction_root: Path) -> None:
        """Guard archive extraction against path traversal (Zip-Slip)."""
        if not self.is_within(member_target, extraction_root):
            raise SecurityError(
                f"Archive member escapes extraction root: {member_target} !< {extraction_root}"
            )

    @property
    def protected_roots(self) -> list[Path]:
        """A copy of the effective protected-root list (for diagnostics)."""
        return list(self._protected)

"""Guards against publishing real host data in ``examples/``.

The sample artifacts in ``examples/`` are committed to a public repository, so
they must never contain anything collected from a real machine -- a hostname, a
user account, an installed-software list or a hardware profile all identify the
author's workstation, and once pushed they are permanent.

Two properties are enforced here:

1. **The committed files match the generator.** Re-running
   ``examples/generate_examples.py`` into a temp directory must reproduce them
   byte-for-byte. That makes the generator -- whose input is a fixed fictional
   host -- the single source of truth, so nobody can hand-edit real output back
   in, and it proves the generator is deterministic.
2. **No host-identifying pattern appears**, checked independently of the
   generator so the test still fails if someone edits both.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
GENERATOR = EXAMPLES_DIR / "generate_examples.py"

#: Artifacts that must be reproducible from the generator.
GENERATED_FILES = [
    "example-report.json",
    "example-report.html",
    "example-report.md",
    "example-report.csv",
    "example-audit.log",
    "example-run.log",
]

#: The only host identity the examples may ever refer to.
DEMO_HOSTNAME = "demo-workstation"
DEMO_USER = "demo"

#: Process names the fictional snapshot is allowed to contain. Anything else
#: suggests a real ``psutil`` process list leaked in.
ALLOWED_PROCESS_NAMES = {
    "app-server.exe",
    "web-browser.exe",
    "code-editor.exe",
    "database.exe",
    "python.exe",
    "indexer.exe",
    "backup-agent.exe",
    "desktop-shell.exe",
    "antivirus.exe",
    "log-collector.exe",
}

#: Patterns that indicate real host data. Each is (regex, why it matters).
FORBIDDEN_PATTERNS = [
    (r"DESKTOP-[A-Z0-9]+", "Windows auto-generated hostname"),
    (r"LAPTOP-[A-Z0-9]+", "Windows auto-generated hostname"),
    (r"[Cc]:\\[Uu]sers\\(?!demo\\)[^\\\s\"']+", "real Windows user profile path"),
    (r"/home/(?!demo/)[^/\s\"']+", "real POSIX home directory"),
    (r"/Users/(?!demo/)[^/\s\"']+", "real macOS home directory"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "email address"),
    (r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", "IP address"),
    (r"OneDrive", "cloud-sync folder from the author's machine"),
    (r"(?i)\b(api[_-]?key|secret|passwd|password|bearer)\b", "credential-like token"),
]


def _load_generator() -> ModuleType:
    """Import ``examples/generate_examples.py`` as a module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("generate_examples", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _committed(name: str) -> bytes:
    return (EXAMPLES_DIR / name).read_bytes()


# --------------------------------------------------------------------------- #
# The committed artifacts are reproducible from the generator
# --------------------------------------------------------------------------- #
def test_generator_exists() -> None:
    assert GENERATOR.is_file(), "examples/generate_examples.py is the source of truth"


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run the generator into a temp directory and return it."""
    out = tmp_path_factory.mktemp("examples")
    module = _load_generator()
    module.EXAMPLES_DIR = out
    assert module.main() == 0
    return out


@pytest.mark.parametrize("name", GENERATED_FILES)
def test_committed_file_matches_generator(regenerated: Path, name: str) -> None:
    produced = (regenerated / name).read_bytes()
    assert produced == _committed(name), (
        f"examples/{name} differs from generator output. "
        f"Run 'python examples/generate_examples.py' and commit the result."
    )


def test_generator_output_is_deterministic(tmp_path: Path) -> None:
    """A second run must produce identical bytes -- no timestamps or random ids."""
    module = _load_generator()
    first, second = tmp_path / "a", tmp_path / "b"
    digests = []
    for out in (first, second):
        out.mkdir()
        module.EXAMPLES_DIR = out
        module.main()
        digests.append({n: (out / n).read_bytes() for n in GENERATED_FILES})
    assert digests[0] == digests[1]


# --------------------------------------------------------------------------- #
# No host-identifying data, checked independently of the generator
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", GENERATED_FILES)
@pytest.mark.parametrize("pattern,reason", FORBIDDEN_PATTERNS, ids=lambda v: str(v)[:28])
def test_no_host_identifying_data(name: str, pattern: str, reason: str) -> None:
    text = (EXAMPLES_DIR / name).read_text(encoding="utf-8")
    hits = re.findall(pattern, text)
    assert not hits, f"examples/{name} contains a {reason}: {hits[:3]}"


@pytest.mark.parametrize("name", GENERATED_FILES)
def test_only_the_demo_host_is_named(name: str) -> None:
    text = (EXAMPLES_DIR / name).read_text(encoding="utf-8")
    for hostname in re.findall(r'"hostname":\s*"([^"]+)"', text):
        assert hostname == DEMO_HOSTNAME
    for user in re.findall(r"[Cc]:\\\\?[Uu]sers\\\\?([^\\\s\"']+)", text):
        assert user == DEMO_USER


def test_process_names_are_generic() -> None:
    """Every process name in the JSON report comes from the fictional allowlist."""
    text = (EXAMPLES_DIR / "example-report.json").read_text(encoding="utf-8")
    names = set(re.findall(r'"name":\s*"([^"]+)"', text))
    assert names, "expected process entries in the sample report"
    assert names <= ALLOWED_PROCESS_NAMES, f"non-generic process names: {names - ALLOWED_PROCESS_NAMES}"

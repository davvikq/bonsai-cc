"""The v1 ↔ v2 architectural seam, enforced as an import-allow-list.

Per DESIGN.md §1.1 the growth engine talks to the rest of the system
*only* through :data:`bonsai_cc.events.bus.event_bus`. It must never
reach into the event-production layer
(:mod:`bonsai_cc.events.journal`, :mod:`bonsai_cc.events.watcher`).

Phase 11 deleted ``bonsai_cc.ipc`` and ``bonsai_cc.events.ingest``;
those entries stay in the forbidden list as a tripwire — should a
future refactor accidentally restore them, the growth/render code
must still not depend on them.

The same scan is also applied to ``render/`` (phase 3) — the
renderer reads validated state but must not parse hook payloads itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "bonsai_cc"


# Modules that the named subpackage must not import (directly or via
# ``from X import Y``). Transitive imports are not enforced — only
# what each file textually references — which is the right level for
# an architectural rule: it lets ``bus`` stay shared because both
# sides of the seam are *supposed* to know about the queue.
_FORBIDDEN_FOR_GROWTH = frozenset(
    {
        # Phase 11: ipc/* + events.ingest deleted, but the rule still
        # forbids them — should a future refactor accidentally
        # resurrect either, this test catches it.
        "bonsai_cc.ipc",
        "bonsai_cc.ipc.server",
        "bonsai_cc.ipc.transport",
        "bonsai_cc.ipc.client",
        "bonsai_cc.events.ingest",
        "bonsai_cc.events.journal",
        "bonsai_cc.events.watcher",
    }
)

_FORBIDDEN_FOR_RENDER = frozenset(
    {
        "bonsai_cc.ipc",
        "bonsai_cc.ipc.server",
        "bonsai_cc.ipc.transport",
        "bonsai_cc.events.ingest",
        "bonsai_cc.events.journal",
        "bonsai_cc.events.watcher",
    }
)


def _python_files_under(pkg: Path) -> list[Path]:
    if not pkg.exists():
        return []
    return sorted(p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts)


def _imports_in(path: Path) -> set[str]:
    """Return the set of module names imported by ``path``.

    Handles both ``import X.Y`` and ``from X.Y import Z`` forms. For
    relative imports we ignore (the test is about absolute dependencies
    crossing the seam, and relative imports stay inside the package).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0:
                continue  # relative import; stays inside the package
            if node.module:
                out.add(node.module)
    return out


def _display_path(p: Path) -> Path:
    """Render a path relative to the project root when possible.

    Falls back to the absolute path for files outside the project
    tree (the self-test below synthesises files in a tmp dir).
    """
    try:
        return p.relative_to(PROJECT_ROOT)
    except ValueError:
        return p


def _assert_no_forbidden_imports(
    package_dir: Path, forbidden: frozenset[str]
) -> None:
    """Walk every .py under ``package_dir`` and assert no import begins
    with any prefix in ``forbidden``."""
    violations: list[tuple[Path, str]] = []
    for f in _python_files_under(package_dir):
        for mod in _imports_in(f):
            for bad in forbidden:
                if mod == bad or mod.startswith(bad + "."):
                    violations.append((_display_path(f), mod))
                    break
    if violations:
        rendered = "\n".join(f"  {p}: imports {m}" for p, m in violations)
        pytest.fail(
            "Architectural seam violation. The following files import "
            "modules they must not touch:\n" + rendered
        )


def test_growth_has_no_forbidden_imports() -> None:
    """``growth/`` must not import IPC or ingest internals.

    Passes vacuously while ``growth/`` does not exist yet. Once phase
    4 lands a single file under ``growth/`` that imports
    ``bonsai_cc.ipc`` or ``bonsai_cc.events.ingest``, this test
    fails — which is the whole point.
    """
    _assert_no_forbidden_imports(SRC_ROOT / "growth", _FORBIDDEN_FOR_GROWTH)


def test_render_has_no_forbidden_imports() -> None:
    """``render/`` must consume validated state, never raw payloads."""
    _assert_no_forbidden_imports(SRC_ROOT / "render", _FORBIDDEN_FOR_RENDER)


def test_seam_scan_is_actually_running() -> None:
    """Sanity: prove the scanner detects a forbidden import when one exists.

    Without this assertion the two tests above would pass vacuously
    forever (no growth/ + no render/ + no imports = empty violation
    list). We synthesize a directory of toy files and confirm the
    scanner catches the bad one.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        pkg = Path(d) / "fake_growth"
        pkg.mkdir()
        (pkg / "good.py").write_text(
            "from bonsai_cc.events.bus import event_bus\n", encoding="utf-8"
        )
        (pkg / "bad.py").write_text(
            "from bonsai_cc.events.watcher import JournalWatcher\n",
            encoding="utf-8",
        )

        with pytest.raises(pytest.fail.Exception) as ei:
            _assert_no_forbidden_imports(pkg, _FORBIDDEN_FOR_GROWTH)
        assert "bad.py" in str(ei.value)
        assert "bonsai_cc.events.watcher" in str(ei.value)

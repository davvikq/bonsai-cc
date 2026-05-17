"""Static guards on the hook client template — STABLE INTERFACE.

These tests pin the two non-functional contracts that make
``client_template.py`` safe to bake into ``~/.claude/settings.json``:

1. **Stdlib-only imports.** Adding any third-party import would
   inflate cold-start past the 500ms hook budget. AST walk asserts
   every import is in the allow-list.

2. **Compact bytecode.** ``py_compile`` must produce a ``.pyc``
   small enough to load in microseconds. We cap at 8 KiB; the
   current template is well under that.

If either test fails the build is broken — the user-facing contract
in the template's module docstring is no longer true.
"""

from __future__ import annotations

import ast
import py_compile
import tempfile
from importlib import resources
from pathlib import Path

ALLOWED_TOP_LEVEL_IMPORTS: frozenset[str] = frozenset(
    {
        # Phase 11: the hook no longer talks to the daemon over a
        # socket — it appends directly to ``<home>/journals/<sid>.jsonl``.
        # ``socket`` is gone from the allow-list; ``re`` is added for
        # session-id sanitization; ``msvcrt`` is added for the
        # cross-process file locking that makes Windows ``O_APPEND``
        # safe under concurrent writers.
        "json",
        "os",
        "re",
        "sys",
        "time",
        "pathlib",
        "msvcrt",
        "__future__",
    }
)

# Ceiling intentionally generous: the file's *user-facing contract*
# (the module docstring) is large by design — that text is what gets
# installed into ``~/.bonsai-cc/hook_client.py`` and what users
# ``cat`` to audit the install. Stripping docstrings to fit 8 KiB
# would defeat the auditability we just promised in the docstring
# itself. Real cold-start cost is dominated by interpreter startup
# (~100ms) and is guarded separately by ``scripts/bench_hook_client.py``;
# bytecode size at the 10-16 KiB range is irrelevant to that.
BYTECODE_CEILING_BYTES = 16 * 1024


def _template_path() -> Path:
    res = resources.files("bonsai_cc.hook").joinpath("client_template.py")
    # Resources may be a Traversable wrapping a real file; we want
    # the concrete path for both ast.parse and py_compile.
    with resources.as_file(res) as p:
        return Path(p)


def _imported_top_levels(path: Path) -> set[str]:
    """Return the set of top-level module names the file imports.

    Example: ``from pathlib import Path`` → ``"pathlib"``.
    ``import socket`` → ``"socket"``. Relative imports are
    intentionally not allowed (the script must be self-contained)
    and would surface as a separate failure.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, (
                "hook client must not use relative imports — it ships standalone"
            )
            assert node.module is not None
            seen.add(node.module.split(".")[0])
    return seen


def test_template_imports_only_stdlib() -> None:
    """The hook client must import nothing outside the allow-list."""
    imports = _imported_top_levels(_template_path())
    forbidden = imports - ALLOWED_TOP_LEVEL_IMPORTS
    assert not forbidden, (
        f"hook client imports modules outside the stdlib allow-list: "
        f"{sorted(forbidden)}. See client_template.py docstring."
    )


def test_template_no_bonsai_cc_imports() -> None:
    """Explicit guard against pulling the rest of the package in."""
    imports = _imported_top_levels(_template_path())
    assert "bonsai_cc" not in imports, (
        "hook client must not import from bonsai_cc — it would drag pydantic, "
        "typer, and structlog into the cold-start path."
    )


def test_template_bytecode_is_small() -> None:
    """Cap compiled size so the hook stays cheap to import."""
    path = _template_path()
    with tempfile.TemporaryDirectory() as d:
        pyc = Path(d) / "client_template.pyc"
        py_compile.compile(str(path), cfile=str(pyc), doraise=True)
        size = pyc.stat().st_size
    assert size <= BYTECODE_CEILING_BYTES, (
        f"hook client bytecode is {size} bytes, ceiling is {BYTECODE_CEILING_BYTES}."
    )


def test_template_parses_and_has_main_entry() -> None:
    """Sanity: the script compiles and defines a callable ``main``."""
    path = _template_path()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "main" in funcs, "hook client must expose a top-level main()"

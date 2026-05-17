"""Project-language detection for the language-themed palette.

Detection order:

1. **Manifest / lockfile** at the project root -- authoritative.
   Ordered so that a more-specific match wins over a less-specific
   one (TypeScript before JavaScript, Java/Kotlin before any other
   JVM heuristic).
2. **File extension histogram** across the top two directory
   levels -- fallback when no manifest matches.
3. **Default** when nothing dominates.

The detector is best-effort and **never raises**. A bad project
path returns ``"default"`` so the renderer always has a theme.

Override
--------
``BONSAI_CC_FORCE_THEME=python`` (or any known theme name) short-
circuits detection. Useful for screenshots and for users who want
the python silhouette in a polyglot repo.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "KNOWN_THEMES",
    "RULES",
    "LanguageRule",
    "detect_language",
]


@dataclass(frozen=True, slots=True)
class LanguageRule:
    """One row of the priority table.

    * ``theme`` -- the palette key (matches :mod:`bonsai_cc.render.palette`).
    * ``manifest_names`` -- filenames at the project root that prove
      this is the language. Wildcards expand via :meth:`Path.glob`.
    * ``extensions`` -- file extensions counted in the histogram
      fallback.
    * ``requires_keyword_in_package_json`` -- set for TypeScript so
      the rule fires even when only ``package.json`` exists, by
      looking for the substring ``typescript`` in it.
    """

    theme: str
    manifest_names: tuple[str, ...]
    extensions: tuple[str, ...]
    requires_keyword_in_package_json: str | None = None


# Order matters -- more-specific rules win the tie-break.
RULES: tuple[LanguageRule, ...] = (
    LanguageRule(
        theme="typescript",
        manifest_names=("tsconfig.json",),
        extensions=(".ts", ".tsx"),
        requires_keyword_in_package_json="typescript",
    ),
    LanguageRule(
        theme="rust",
        manifest_names=("Cargo.toml",),
        extensions=(".rs",),
    ),
    LanguageRule(
        theme="go",
        manifest_names=("go.mod",),
        extensions=(".go",),
    ),
    LanguageRule(
        theme="swift",
        manifest_names=("Package.swift", "*.xcodeproj", "*.xcworkspace"),
        extensions=(".swift",),
    ),
    LanguageRule(
        theme="ruby",
        manifest_names=("Gemfile", "*.gemspec"),
        extensions=(".rb",),
    ),
    LanguageRule(
        theme="zig",
        manifest_names=("build.zig", "build.zig.zon"),
        extensions=(".zig",),
    ),
    LanguageRule(
        theme="haskell",
        manifest_names=("*.cabal", "stack.yaml", "dune-project", "elm.json"),
        extensions=(".hs", ".ml", ".mli", ".elm"),
    ),
    LanguageRule(
        theme="java",
        manifest_names=(
            "pom.xml", "build.gradle", "build.gradle.kts",
            "settings.gradle", "settings.gradle.kts",
        ),
        extensions=(".java", ".kt", ".kts"),
    ),
    LanguageRule(
        theme="cpp",
        manifest_names=("CMakeLists.txt", "meson.build", "*.vcxproj", "Makefile"),
        extensions=(".c", ".h", ".cc", ".cpp", ".hpp", ".cxx"),
    ),
    LanguageRule(
        theme="python",
        manifest_names=("pyproject.toml", "setup.py", "requirements.txt", "Pipfile"),
        extensions=(".py",),
    ),
    LanguageRule(
        theme="javascript",
        manifest_names=("package.json",),
        extensions=(".js", ".mjs", ".cjs", ".jsx"),
    ),
)


KNOWN_THEMES: frozenset[str] = frozenset({r.theme for r in RULES} | {"default"})


_ENV_FORCE = "BONSAI_CC_FORCE_THEME"


def detect_language(project_root: str | None) -> str:
    """Return the best-guess theme for ``project_root``.

    Always returns a theme in :data:`KNOWN_THEMES`. Honest about
    uncertainty: when nothing matches we return ``"default"``
    rather than guessing wildly. Setting ``BONSAI_CC_FORCE_THEME``
    short-circuits everything and lets the user override.

    Example:
        >>> import tempfile, pathlib
        >>> with tempfile.TemporaryDirectory() as d:
        ...     (pathlib.Path(d) / "Cargo.toml").write_text("[package]")
        ...     detect_language(d)
        'rust'
    """
    override = os.environ.get(_ENV_FORCE)
    if override and override in KNOWN_THEMES:
        return override

    if not project_root:
        return "default"
    root = Path(project_root)
    try:
        if not root.exists() or not root.is_dir():
            return "default"
    except OSError:
        return "default"

    by_manifest = _by_manifest(root)
    if by_manifest is not None:
        return by_manifest

    by_histogram = _by_histogram(root)
    if by_histogram is not None:
        return by_histogram

    return "default"


# ---------------------------------------------------------------------------
# Step 1: manifest match
# ---------------------------------------------------------------------------


def _by_manifest(root: Path) -> str | None:
    for rule in RULES:
        if _manifest_matches(root, rule):
            return rule.theme
    return None


def _manifest_matches(root: Path, rule: LanguageRule) -> bool:
    for name in rule.manifest_names:
        if "*" in name or "?" in name:
            try:
                if any(root.glob(name)):
                    return True
            except OSError:
                continue
            continue
        if (root / name).exists():
            # Special case for TypeScript-via-package.json: only the
            # explicit ``tsconfig.json`` rule lands here; the keyword
            # fallback handles "package.json mentions typescript".
            return True
    if rule.requires_keyword_in_package_json:
        return _package_json_mentions(root, rule.requires_keyword_in_package_json)
    return False


def _package_json_mentions(root: Path, keyword: str) -> bool:
    pkg = root / "package.json"
    if not pkg.exists():
        return False
    try:
        text = pkg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return keyword in text


# ---------------------------------------------------------------------------
# Step 2: extension histogram across the top two directory levels
# ---------------------------------------------------------------------------


_HISTOGRAM_DEPTH = 2
_HISTOGRAM_MAX_FILES = 1000  # cap so very large projects don't stall


def _by_histogram(root: Path) -> str | None:
    counts: Counter[str] = Counter()
    seen = 0
    try:
        for entry in _walk(root, depth=_HISTOGRAM_DEPTH):
            if seen >= _HISTOGRAM_MAX_FILES:
                break
            seen += 1
            ext = entry.suffix.lower()
            if ext:
                counts[ext] += 1
    except OSError:
        return None
    if not counts:
        return None
    best_theme = None
    best_count = 0
    for rule in RULES:
        score = sum(counts.get(ext, 0) for ext in rule.extensions)
        if score > best_count:
            best_count = score
            best_theme = rule.theme
    return best_theme


def _walk(root: Path, *, depth: int) -> list[Path]:
    """Files under ``root`` to a maximum recursion depth (1 = root itself)."""
    out: list[Path] = []
    if depth < 1:
        return out
    try:
        for child in root.iterdir():
            if child.is_file():
                out.append(child)
            elif child.is_dir() and depth > 1:
                if child.name.startswith(".") or child.name in {
                    "node_modules", "venv", ".venv", "__pycache__", "target", "build", "dist",
                }:
                    continue
                out.extend(_walk(child, depth=depth - 1))
    except OSError:
        return out
    return out

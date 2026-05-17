"""Language / theme detection.

We exercise each rule in the table — manifest hit, manifest with
keyword check (TS via package.json), extension histogram fallback,
the ``BONSAI_CC_FORCE_THEME`` env override, and the safe defaults
for missing or non-existent project roots.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bonsai_cc.growth.language import KNOWN_THEMES, detect_language


@pytest.mark.parametrize(
    "manifest_filename,expected",
    [
        ("Cargo.toml", "rust"),
        ("go.mod", "go"),
        ("Package.swift", "swift"),
        ("Gemfile", "ruby"),
        ("build.zig", "zig"),
        ("dune-project", "haskell"),
        ("pom.xml", "java"),
        ("CMakeLists.txt", "cpp"),
        ("pyproject.toml", "python"),
        ("package.json", "javascript"),
    ],
)
def test_manifest_hits(tmp_path: Path, manifest_filename: str, expected: str) -> None:
    (tmp_path / manifest_filename).write_text("", encoding="utf-8")
    assert detect_language(str(tmp_path)) == expected


def test_tsconfig_picks_typescript_over_javascript(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    assert detect_language(str(tmp_path)) == "typescript"


def test_package_json_with_typescript_keyword(tmp_path: Path) -> None:
    """A package.json that depends on TypeScript counts as TS."""
    (tmp_path / "package.json").write_text(
        '{"devDependencies": {"typescript": "^5"}}',
        encoding="utf-8",
    )
    assert detect_language(str(tmp_path)) == "typescript"


def test_package_json_without_keyword_is_javascript(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18"}}',
        encoding="utf-8",
    )
    assert detect_language(str(tmp_path)) == "javascript"


def test_extension_histogram_fallback(tmp_path: Path) -> None:
    """No manifest — pick by dominant extension."""
    (tmp_path / "a.rs").write_text("", encoding="utf-8")
    (tmp_path / "b.rs").write_text("", encoding="utf-8")
    (tmp_path / "c.rs").write_text("", encoding="utf-8")
    (tmp_path / "single.py").write_text("", encoding="utf-8")
    assert detect_language(str(tmp_path)) == "rust"


# ---------------------------------------------------------------------------
# Histogram corner cases — the four paths from the bug report.
# ---------------------------------------------------------------------------


def test_histogram_only_py_picks_python(tmp_path: Path) -> None:
    """Pure-Python directory with no pyproject (e.g. a scratch
    folder of ``.py`` scripts and a README) must still pick the
    python palette. This is the live-session case that produced
    ``theme=default`` in earlier diagnostics."""
    (tmp_path / "hello.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "math_utils.py").write_text("", encoding="utf-8")
    (tmp_path / "string_utils.py").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("# project", encoding="utf-8")
    assert detect_language(str(tmp_path)) == "python"


def test_histogram_only_rs_picks_rust(tmp_path: Path) -> None:
    (tmp_path / "main.rs").write_text("fn main() {}", encoding="utf-8")
    (tmp_path / "lib.rs").write_text("", encoding="utf-8")
    assert detect_language(str(tmp_path)) == "rust"


def test_histogram_mixed_picks_highest_count(tmp_path: Path) -> None:
    """Tie-break is by raw count, not rule order."""
    for i in range(3):
        (tmp_path / f"r{i}.rs").write_text("", encoding="utf-8")
    for i in range(7):
        (tmp_path / f"g{i}.go").write_text("", encoding="utf-8")
    (tmp_path / "stray.py").write_text("", encoding="utf-8")
    assert detect_language(str(tmp_path)) == "go"


def test_histogram_empty_returns_default(tmp_path: Path) -> None:
    """No source files at all — fall back rather than guess."""
    (tmp_path / "README").write_text("hello", encoding="utf-8")  # no extension
    (tmp_path / "LICENSE").write_text("MIT", encoding="utf-8")
    assert detect_language(str(tmp_path)) == "default"


def test_runner_uses_detected_language_from_session_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
) -> None:
    """End-to-end pin: the live runner must detect language at
    session-bind time, not leave the state at ``default``.

    Captures the exact failure mode in the bug report — a project
    with ``hello.py`` + ``README.md`` and no manifest.
    """
    import asyncio

    from bonsai_cc.events.bus import IngestedEvent, reset_event_bus_for_tests
    from bonsai_cc.events.models import parse_event as _parse_event
    from bonsai_cc.runner import GrowthRunner, build_initial_state
    from tests.conftest import RecorderApp

    project = tmp_path / "proj"
    project.mkdir()
    (project / "hello.py").write_text("print('hi')", encoding="utf-8")
    (project / "README.md").write_text("# x", encoding="utf-8")

    async def run() -> str:
        bus = reset_event_bus_for_tests()
        app = RecorderApp(build_initial_state("s"))
        runner = GrowthRunner(app, bus, theme="default")
        ev = _parse_event({
            "session_id": "s",
            "hook_event_name": "SessionStart",
            "source": "startup",
            "cwd": str(project),
        })
        await runner.start()
        await bus.publish(IngestedEvent(idx=0, event=ev))
        for _ in range(40):
            await asyncio.sleep(0)
            if bus.qsize() == 0:
                break
        await asyncio.sleep(0)
        await runner.stop()
        assert runner.state is not None
        return runner.state.theme

    theme = asyncio.run(run())
    assert theme == "python", (
        f"expected python theme from histogram detection, got {theme!r}"
    )


def test_skips_unhelpful_directories(tmp_path: Path) -> None:
    """node_modules / venv / .git etc. don't pollute the histogram."""
    nm = tmp_path / "node_modules" / "weirdpkg"
    nm.mkdir(parents=True)
    for i in range(50):
        (nm / f"f{i}.js").write_text("", encoding="utf-8")
    (tmp_path / "x.py").write_text("", encoding="utf-8")
    assert detect_language(str(tmp_path)) == "python"


def test_force_theme_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("BONSAI_CC_FORCE_THEME", "swift")
    assert detect_language(str(tmp_path)) == "swift"


def test_force_theme_with_unknown_value_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Garbage in the env var must not break detection."""
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("BONSAI_CC_FORCE_THEME", "not-a-real-theme")
    assert detect_language(str(tmp_path)) == "rust"


def test_missing_root_returns_default() -> None:
    assert detect_language(None) == "default"
    assert detect_language("") == "default"
    assert detect_language("/does/not/exist/anywhere") == "default"


def test_empty_dir_returns_default(tmp_path: Path) -> None:
    assert detect_language(str(tmp_path)) == "default"


def test_every_returned_theme_is_known(tmp_path: Path) -> None:
    """Sanity: anything we ever return must have a palette entry."""
    for manifest in ("Cargo.toml", "go.mod", "build.zig"):
        (tmp_path / manifest).write_text("", encoding="utf-8")
        theme = detect_language(str(tmp_path))
        assert theme in KNOWN_THEMES
        (tmp_path / manifest).unlink()

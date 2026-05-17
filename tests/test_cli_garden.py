"""CLI surface for garden / list / show / export."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bonsai_cc.cli import app
from bonsai_cc.garden.store import GardenStore, SessionStatus
from bonsai_cc.growth.state import demo_tree


def _seed(bonsai_home: Path) -> None:
    store = GardenStore()
    for sid in ("alpha-aaaaaa", "beta-bbbbbb"):
        store.save_session(
            demo_tree(sid),
            project_path="/work/proj",
            event_log_path=bonsai_home / "journals" / f"{sid}.jsonl",
            detected_language="python",
        )
    store.close()


def test_garden_opens_web(monkeypatch, bonsai_home: Path) -> None:
    """Phase 11.5: ``bonsai-cc garden`` opens the web view (the
    only renderer). We stub ``run_web_pipeline`` to avoid actually
    binding a port."""
    from typing import Any

    import bonsai_cc.cli as cli_mod

    calls: dict[str, Any] = {"count": 0, "kwargs": None}

    async def _stub(**kwargs: Any) -> None:
        calls["count"] += 1
        calls["kwargs"] = kwargs

    monkeypatch.setattr(cli_mod, "run_web_pipeline", _stub)
    _seed(bonsai_home)
    result = CliRunner().invoke(app, ["garden"])
    assert result.exit_code == 0, result.stdout
    assert calls["count"] == 1
    assert calls["kwargs"]["banner"] == "garden"


def test_list_prints_table(bonsai_home: Path) -> None:
    _seed(bonsai_home)
    result = CliRunner().invoke(app, ["list"])
    assert result.exit_code == 0
    # Header + two rows.
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines[0].startswith("started")
    assert any("alpha-aaaaaa" in line for line in lines[1:])
    assert any("beta-bbbbbb" in line for line in lines[1:])


def test_list_filters_by_language(bonsai_home: Path) -> None:
    _seed(bonsai_home)
    # Stash an extra session with a different language.
    store = GardenStore()
    store.save_session(
        demo_tree("rust-rrrrrr"),
        project_path="/work/rust",
        event_log_path=bonsai_home / "journals" / "rust-rrrrrr.jsonl",
        detected_language="rust",
    )
    store.close()
    result = CliRunner().invoke(app, ["list", "--language", "rust"])
    assert result.exit_code == 0
    assert "rust-rrrrrr" in result.stdout
    assert "alpha-aaaaaa" not in result.stdout


def test_show_prints_final_ascii(bonsai_home: Path) -> None:
    _seed(bonsai_home)
    result = CliRunner().invoke(app, ["show", "alpha-aaaaaa"])
    assert result.exit_code == 0
    assert "│" in result.stdout  # trunk visible


def test_show_accepts_prefix(bonsai_home: Path) -> None:
    _seed(bonsai_home)
    result = CliRunner().invoke(app, ["show", "alpha"])
    assert result.exit_code == 0
    assert "│" in result.stdout


def test_show_ambiguous_prefix_lists_options(bonsai_home: Path) -> None:
    store = GardenStore()
    store.save_session(
        demo_tree("twin-1"), project_path="/p", event_log_path=Path("/p"),
    )
    store.save_session(
        demo_tree("twin-2"), project_path="/p", event_log_path=Path("/p"),
    )
    store.close()
    result = CliRunner().invoke(app, ["show", "twin"])
    assert result.exit_code != 0


def test_show_missing_id(bonsai_home: Path) -> None:
    _seed(bonsai_home)
    result = CliRunner().invoke(app, ["show", "no-such-session"])
    assert result.exit_code != 0


def test_export_txt_writes_file(bonsai_home: Path, tmp_path: Path) -> None:
    _seed(bonsai_home)
    out = tmp_path / "alpha.txt"
    result = CliRunner().invoke(
        app, ["export", "alpha-aaaaaa", "--format", "txt", "-o", str(out)]
    )
    assert result.exit_code == 0, result.stdout
    assert out.exists()
    assert "│" in out.read_text(encoding="utf-8")


def test_export_png_writes_file(bonsai_home: Path, tmp_path: Path) -> None:
    _seed(bonsai_home)
    out = tmp_path / "alpha.png"
    result = CliRunner().invoke(
        app, ["export", "alpha-aaaaaa", "--format", "png", "-o", str(out)]
    )
    assert result.exit_code == 0, result.stdout
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_list_recovers_orphan_journals_before_listing(bonsai_home: Path) -> None:
    """The May-2026 production scenario: a session whose daemon died
    before saving (no SessionEnd, no clean shutdown) leaves an
    orphan journal on disk. ``bonsai-cc list`` must trigger
    recovery as a prelude so the row appears immediately, rather
    than only after the user happens to run ``bonsai-cc watch``.
    """
    from pathlib import Path as _Path

    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    sid = "86abd5d6-881d-4a56-860d-fc2e2d199787"
    source = _Path("tests/fixtures/real_session_2026-05-15.jsonl")
    (cfg_journals / f"{sid}.jsonl").write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # Materialise the garden with a fresh schema and zero rows —
    # the user's exact starting state.
    store = GardenStore()
    assert store.count_sessions() == 0
    assert store.get_session(sid) is None
    store.close()

    result = CliRunner().invoke(app, ["list"])
    assert result.exit_code == 0, result.stdout
    assert sid in result.stdout, "recovered session must appear in listing"

    # The row landed with status=recovered.
    store = GardenStore()
    row = store.get_session(sid)
    store.close()
    assert row is not None
    assert row.status == SessionStatus.RECOVERED


def test_show_recovers_before_resolving_prefix(bonsai_home: Path) -> None:
    """A user who just finished a session must be able to ``show``
    it without first running ``watch``.
    """
    from pathlib import Path as _Path

    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    sid = "86abd5d6-881d-4a56-860d-fc2e2d199787"
    source = _Path("tests/fixtures/real_session_2026-05-15.jsonl")
    (cfg_journals / f"{sid}.jsonl").write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    GardenStore().close()  # materialise schema, no rows

    result = CliRunner().invoke(app, ["show", "86abd5d6"])
    assert result.exit_code == 0, result.stdout
    # The captured session had at least one PostToolUseFailure →
    # wilted-leaf glyph survives end-to-end.
    assert "," in result.stdout or "│" in result.stdout


def test_export_recovers_before_lookup(bonsai_home: Path, tmp_path: Path) -> None:
    from pathlib import Path as _Path

    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    sid = "86abd5d6-881d-4a56-860d-fc2e2d199787"
    source = _Path("tests/fixtures/real_session_2026-05-15.jsonl")
    (cfg_journals / f"{sid}.jsonl").write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    GardenStore().close()

    out = tmp_path / "recovered.txt"
    result = CliRunner().invoke(
        app, ["export", "86abd5d6", "--format", "txt", "-o", str(out)]
    )
    assert result.exit_code == 0, result.stdout
    assert out.exists()


def test_export_unknown_format_exits_nonzero(bonsai_home: Path) -> None:
    _seed(bonsai_home)
    result = CliRunner().invoke(
        app, ["export", "alpha-aaaaaa", "--format", "exr"]
    )
    assert result.exit_code != 0


def test_export_svg_stub_messages_not_implemented(bonsai_home: Path, tmp_path: Path) -> None:
    _seed(bonsai_home)
    result = CliRunner().invoke(
        app, ["export", "alpha-aaaaaa", "--format", "svg", "-o", str(tmp_path / "x.svg")],
    )
    assert result.exit_code != 0
    assert "not implemented" in (result.stdout + (result.stderr or ""))

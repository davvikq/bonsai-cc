"""Export pipeline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from bonsai_cc.export import ExportError, export_png, export_text
from bonsai_cc.export.image import export_gif_stub, export_svg_stub
from bonsai_cc.garden.store import GardenStore, render_final_ascii
from bonsai_cc.growth.state import demo_tree


def _saved_row(bonsai_home: Path):  # type: ignore[no-untyped-def]
    store = GardenStore()
    state = demo_tree("export-test")
    store.save_session(
        state, project_path="/p", event_log_path=Path("/p/x.jsonl"),
        detected_language="python",
    )
    row = store.get_session("export-test")
    store.close()
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------


def test_text_export_writes_cached_ascii(bonsai_home: Path, tmp_path: Path) -> None:
    row = _saved_row(bonsai_home)
    out = tmp_path / "out.txt"
    written = export_text(row, out)
    assert written == out.resolve()
    text = written.read_text(encoding="utf-8")
    assert text == row.final_ascii


def test_text_export_regenerates_when_cache_missing(
    bonsai_home: Path, tmp_path: Path
) -> None:
    """Older rows may have ``final_ascii=NULL`` — we recover from state JSON."""
    from dataclasses import replace as _replace

    row = _saved_row(bonsai_home)
    row_without_ascii = _replace(row, final_ascii=None)
    out = tmp_path / "regen.txt"
    written = export_text(row_without_ascii, out)
    assert written.exists()
    # Regenerated ASCII matches what render_final_ascii would produce
    # for the JSON state we stored.
    from bonsai_cc.garden.store import load_state_from_row

    state = load_state_from_row(row_without_ascii)
    assert state is not None
    assert written.read_text(encoding="utf-8") == render_final_ascii(state)


def test_text_export_raises_when_unrecoverable(tmp_path: Path) -> None:
    from dataclasses import replace as _replace

    from bonsai_cc.garden.store import SessionRow

    empty = SessionRow(
        id="x",
        seed_hex="0" * 16,
        started_at=0,
        ended_at=None,
        project_path="",
        detected_language=None,
        theme="default",
        tool_call_count=0,
        error_count=0,
        file_branch_count=0,
        final_ascii=None,
        final_state_json=None,
        event_log_path="",
        tags=None,
    )
    with pytest.raises(ExportError):
        export_text(empty, tmp_path / "x.txt")
    _ = _replace  # silence unused-import warning


# ---------------------------------------------------------------------------
# png
# ---------------------------------------------------------------------------


def test_png_export_produces_a_valid_png(bonsai_home: Path, tmp_path: Path) -> None:
    row = _saved_row(bonsai_home)
    out = tmp_path / "out.png"
    written = export_png(row, out)
    assert written.exists()
    data = written.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG signature
    # The file must have some real content (>1 KiB even at a small size).
    assert written.stat().st_size > 1024


def test_png_export_requires_cached_ascii(tmp_path: Path) -> None:
    from bonsai_cc.garden.store import SessionRow

    blank = SessionRow(
        id="x",
        seed_hex="0" * 16,
        started_at=0,
        ended_at=None,
        project_path="",
        detected_language=None,
        theme="default",
        tool_call_count=0,
        error_count=0,
        file_branch_count=0,
        final_ascii=None,
        final_state_json=None,
        event_log_path="",
        tags=None,
    )
    with pytest.raises(ExportError):
        export_png(blank, tmp_path / "x.png")


# ---------------------------------------------------------------------------
# svg / gif stubs
# ---------------------------------------------------------------------------


def test_svg_and_gif_stubs_raise_not_implemented(tmp_path: Path) -> None:
    from bonsai_cc.garden.store import SessionRow

    row = SessionRow(
        id="x", seed_hex="0" * 16, started_at=0, ended_at=None,
        project_path="", detected_language=None, theme="default",
        tool_call_count=0, error_count=0, file_branch_count=0,
        final_ascii="hello", final_state_json=None, event_log_path="",
        tags=None,
    )
    with pytest.raises(ExportError, match="not implemented"):
        export_svg_stub(row, tmp_path / "x.svg")
    with pytest.raises(ExportError, match="not implemented"):
        export_gif_stub(row, tmp_path / "x.gif")

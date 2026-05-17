"""Pure projection from ``TreeState`` to a 2D ``Cell`` grid.

These tests don't spin up Textual; they exercise the pure function
that the Textual widget wraps. That keeps the projection unit-
testable on CI without a TTY.
"""

from __future__ import annotations

from bonsai_cc.growth.state import (
    Branch,
    Flower,
    Root,
    Segment,
    TreeState,
    demo_tree,
)
from bonsai_cc.render.projection import BLANK_CELL, project, tree_bounds


def _empty_state() -> TreeState:
    return TreeState(
        session_id="empty",
        seed_hex="0" * 16,
        started_at_ms=0,
        theme="default",
    )


def test_empty_state_renders_blank_grid_with_ground() -> None:
    grid = project(_empty_state(), 20, 6)
    assert len(grid) == 6
    assert all(len(row) == 20 for row in grid)
    # Exactly one row should be the ground line.
    ground_rows = [
        i for i, row in enumerate(grid)
        if all(cell.char == "~" for cell in row)
    ]
    assert len(ground_rows) == 1


def test_demo_tree_has_visible_trunk_and_branches() -> None:
    grid = project(demo_tree(), 80, 24)
    chars = {cell.char for row in grid for cell in row}
    # Trunk + at least one branch glyph + at least one leaf + ground.
    assert "│" in chars, "trunk glyph missing"
    assert "~" in chars, "ground glyph missing"
    # At least one branch direction.
    assert "\\" in chars or "/" in chars, "no branch glyphs"
    # At least one leaf glyph.
    assert any(c in chars for c in ("&", "*", "|")), "no leaf glyphs"


def test_projection_centres_trunk_horizontally() -> None:
    """Trunk-base logical x=0 should land at column ``width // 2``."""
    state = TreeState(
        session_id="t", seed_hex="0" * 16, started_at_ms=0, theme="default",
        trunk=[Segment(x=0, y=0, glyph="│")],
    )
    width = 40
    grid = project(state, width, 12)
    # The trunk base sits at row_origin = height - ground_clearance = 12 - 3 = 9
    trunk_row = grid[9]
    # The trunk glyph must appear at the centre column.
    assert trunk_row[width // 2].char == "│"


def test_projection_clips_off_screen_content_safely() -> None:
    """A branch that extends past the viewport must not crash or wrap."""
    state = TreeState(
        session_id="t", seed_hex="0" * 16, started_at_ms=0, theme="default",
        trunk=[Segment(x=0, y=y, glyph="│") for y in range(1, 4)],
        branches=[
            Branch(
                file_path="far.py",
                angle_deg=90.0,
                segments=[Segment(x=200, y=2, glyph="\\")],
                leaves=[Segment(x=210, y=3, glyph="&")],
            )
        ],
    )
    grid = project(state, 20, 6)
    # No row should be longer than width.
    assert all(len(row) == 20 for row in grid)
    # Off-screen segment shouldn't have left any "&" or "\\" in the grid.
    chars = {cell.char for row in grid for cell in row}
    assert "&" not in chars
    # Trunk still present (it's at x=0 → centre column → not clipped).
    assert "│" in chars


def test_negative_y_roots_fit_in_viewport() -> None:
    """Deep roots increase the ground clearance automatically."""
    state = TreeState(
        session_id="t", seed_hex="0" * 16, started_at_ms=0, theme="default",
        trunk=[Segment(x=0, y=1, glyph="│")],
        roots=[
            Root(
                cwd="/p",
                angle_deg=-90.0,
                segments=[
                    Segment(x=0, y=-1, glyph="|"),
                    Segment(x=0, y=-2, glyph="|"),
                    Segment(x=0, y=-3, glyph="|"),
                ],
            )
        ],
    )
    grid = project(state, 20, 12)
    pipe_rows = [
        i for i, row in enumerate(grid)
        if any(cell.char == "|" for cell in row)
    ]
    # All three root segments should be present.
    assert len(pipe_rows) >= 3


def test_tree_bounds_of_empty_state_is_origin() -> None:
    assert tree_bounds(_empty_state()) == (0, 0, 0, 0)


def test_tree_bounds_covers_every_element() -> None:
    s = TreeState(
        session_id="t", seed_hex="0" * 16, started_at_ms=0, theme="default",
        trunk=[Segment(x=0, y=5, glyph="│")],
        branches=[
            Branch(
                file_path="x.py", angle_deg=20.0,
                segments=[Segment(x=3, y=4, glyph="\\")],
                leaves=[Segment(x=4, y=5, glyph="&")],
            )
        ],
        roots=[
            Root(
                cwd="/p", angle_deg=-90.0,
                segments=[Segment(x=-2, y=-1, glyph="/")],
            )
        ],
        flowers=[Flower(x=5, y=7, glyph="❀")],
    )
    min_x, max_x, min_y, max_y = tree_bounds(s)
    assert min_x == -2 and max_x == 5
    assert min_y == -1 and max_y == 7


def test_zero_size_viewport_returns_empty() -> None:
    assert project(_empty_state(), 0, 10) == []
    assert project(_empty_state(), 10, 0) == []


def test_blank_cell_is_shared_singleton() -> None:
    """BLANK_CELL is exported and is the same object filling the grid."""
    grid = project(_empty_state(), 4, 4)
    # The four corners should be blanks (no tree, no ground there).
    assert grid[0][0] is BLANK_CELL
    assert grid[0][-1] is BLANK_CELL

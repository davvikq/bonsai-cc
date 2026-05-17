"""Pure projection: ``TreeState`` to a 2D ``Cell`` grid.

This is the only part of the renderer that touches geometry. The
``bonsai-cc show`` / ``export --format txt`` paths wrap it; tests
exercise it directly.

The projection is deliberately framebuffer-free: each call returns
a fresh ``list[list[Cell]]`` from scratch.

Coordinate translation
----------------------
Logical y grows upward (canopy), screen row grows downward (terminal
convention). The trunk base sits one row above the ground line so
roots have somewhere to grow into.
"""

from __future__ import annotations

from bonsai_cc.growth.state import Cell, TreeState
from bonsai_cc.render import glyphs
from bonsai_cc.render.palette import palette_for
from bonsai_cc.render.seasons import Overlay

__all__ = ["BLANK_CELL", "project", "tree_bounds"]


BLANK_CELL = Cell(char=" ")


def tree_bounds(state: TreeState) -> tuple[int, int, int, int]:
    """Return ``(min_x, max_x, min_y, max_y)`` for everything in ``state``.

    Used by the renderer to centre the tree and by tests to assert
    geometry without rendering. Returns ``(0, 0, 0, 0)`` when the
    tree is empty -- e.g. a freshly-seeded session that has only seen
    ``SessionStart``.
    """
    xs: list[int] = []
    ys: list[int] = []

    def add(x: int, y: int) -> None:
        xs.append(x)
        ys.append(y)

    for s in state.trunk:
        add(s.x, s.y)
    for b in state.branches:
        for s in b.segments:
            add(s.x, s.y)
        for s in b.leaves:
            add(s.x, s.y)
    for r in state.roots:
        for s in r.segments:
            add(s.x, s.y)
    for f in state.flowers:
        add(f.x, f.y)
    for o in state.offshoots:
        for s in o.segments:
            add(s.x, s.y)

    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), max(xs), min(ys), max(ys))


def project(
    state: TreeState,
    width: int,
    height: int,
    *,
    overlay: Overlay | None = None,
) -> list[list[Cell]]:
    """Render ``state`` to a fresh ``height x width`` grid of cells.

    Cells outside the viewport are dropped (no exception, no resize
    of state). Trunk-base x is centred horizontally; trunk-base y
    sits a few rows above the bottom so roots fit. A ``~`` ground
    line is drawn just below the trunk base.

    Example:
        >>> from bonsai_cc.growth.state import demo_tree
        >>> grid = project(demo_tree(), 60, 20)
        >>> len(grid), len(grid[0])
        (20, 60)
    """
    if width <= 0 or height <= 0:
        return []

    grid: list[list[Cell]] = [
        [BLANK_CELL for _ in range(width)] for _ in range(height)
    ]
    palette = palette_for(state.theme)

    min_x, max_x, min_y, max_y = tree_bounds(state)

    # Centre the trunk horizontally. Logical x=0 → col = width // 2.
    col_origin = width // 2

    # Place the trunk base ``ground_clearance`` rows above the bottom
    # of the viewport so roots have room. If the tree's negative-y
    # extent exceeds that clearance, we'll just clip the deepest roots.
    ground_clearance = max(3, -min_y + 1) if min_y < 0 else 3
    row_origin = height - ground_clearance  # screen row of logical y=0

    def plot(x: int, y: int, char: str, fg: str | None) -> None:
        col = col_origin + x
        row = row_origin - y
        if 0 <= col < width and 0 <= row < height:
            grid[row][col] = Cell(char=char, fg=fg)

    # 1. Ground line (drawn first so trunk/roots paint over it).
    if 0 <= row_origin + 1 < height:
        for col in range(width):
            grid[row_origin + 1][col] = Cell(char=glyphs.GROUND_LINE, fg=palette.ground)

    # 2. Roots (drawn under the ground line going further down).
    for root in state.roots:
        for seg in root.segments:
            plot(seg.x, seg.y, seg.glyph, seg.color or palette.root)

    # 3. Trunk.
    for seg in state.trunk:
        plot(seg.x, seg.y, seg.glyph, seg.color or palette.trunk)

    # 4. Subagent offshoots (small stalks from the trunk).
    for off in state.offshoots:
        for seg in off.segments:
            plot(seg.x, seg.y, seg.glyph, seg.color or palette.accent)

    # 5. Branches.
    for branch in state.branches:
        for seg in branch.segments:
            plot(seg.x, seg.y, seg.glyph, seg.color or palette.branch)

    # 6. Leaves (after branches so they cover branch endpoints).
    for branch in state.branches:
        for leaf in branch.leaves:
            plot(leaf.x, leaf.y, leaf.glyph, leaf.color or palette.leaf)

    # 7. Flowers (highest layer; should never be obscured).
    for flower in state.flowers:
        plot(flower.x, flower.y, flower.glyph, flower.color or palette.flower)

    # 8. Overlay (ambient effects -- moon, fireflies, dew, snowflakes).
    #    Painted last so it sits above everything else. Stays optional
    #    so tests of the pure projection don't need a fake clock.
    if overlay is not None:
        for cell in overlay.ambient:
            plot(cell.x, cell.y, cell.glyph, cell.color)

    # Bounds are only used by callers wanting layout info; we return
    # the grid. Suppress "unused" lint locally -- they're documented
    # for the test suite to assert against.
    _ = (min_x, max_x, max_y)
    return grid

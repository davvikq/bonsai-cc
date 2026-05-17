"""``render_willow_ts`` -- the typescript theme.

Willow silhouette plus small TS-blue fruits (#3D7AB8) dotted on
the branch tips. Subtle: 5-8 fruits total across the whole tree.

The willow body is rendered first, then the fruits are overlaid at
the same tip / apex / mid positions the willow renderer used for
its foliage clusters. Without this anchoring, fruits drifted in
mid-air anywhere inside the canopy bounding box and read as bugs.
"""

from __future__ import annotations

from bonsai_cc.growth.state import TreeState
from bonsai_cc.web.render import canvas, tokens
from bonsai_cc.web.render.willow import (
    _branch_mid,
    _branch_tip,
    _spine,
    render_willow,
)

__all__ = ["render_willow_ts"]


def _ts_fruit(x: float, y: float, *, r: float) -> str:
    """One blue dot + a soft underglow."""
    return (
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" '
        f'fill="{tokens.TS_FRUIT}" />'
        f'<circle cx="{x + 0.6:.1f}" cy="{y + r * 0.8:.1f}" '
        f'r="{r * 0.6:.1f}" '
        f'fill="{canvas.shade(tokens.TS_FRUIT, -0.35)}" opacity="0.45" />'
    )


def _ts_fruits(state: TreeState) -> str:
    """5-8 blue dots placed exactly on the same foliage anchors
    willow uses: branch tips, branch midpoints, and the apex.

    Deterministic per-branch via session seed. When the session has
    no branches yet, a single fruit sits at the apex so the theme is
    still identifiable.
    """
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    parts: list[str] = []
    # Apex fruit -- always present so the theme reads even with zero
    # branches.
    apex_x, apex_y = _spine(state)[-1]
    h0, _, _, _ = canvas.quad_lcg(seed)
    apex_r = 4.2 + (h0 / 255.0) * 1.5
    parts.append(_ts_fruit(apex_x + 6, apex_y - 4, r=apex_r))

    if not state.branches:
        return "".join(parts)

    # Per-branch fruits: 1-2 per branch on tip / mid. Cap at ~8 total.
    fruits_left = 7
    for i, branch in enumerate(state.branches):
        if fruits_left <= 0:
            break
        if not branch.segments:
            continue
        tip_x, tip_y = _branch_tip(branch, state, i)
        h1, h2, h3, _ = canvas.quad_lcg(seed * 11 + i + 1)
        # Tip fruit -- always.
        parts.append(_ts_fruit(
            tip_x + (h1 / 255.0 - 0.5) * 6,
            tip_y - 6 + (h2 / 255.0 - 0.5) * 4,
            r=3.5 + (h3 / 255.0) * 1.5,
        ))
        fruits_left -= 1
        # Mid fruit on every other branch.
        if fruits_left > 0 and i % 2 == 0:
            mid = _branch_mid(branch, state, i)
            if mid is not None:
                mx, my = mid
                h4, h5, h6, _ = canvas.quad_lcg(seed * 19 + i + 7)
                parts.append(_ts_fruit(
                    mx + (h4 / 255.0 - 0.5) * 6,
                    my - 4 + (h5 / 255.0 - 0.5) * 4,
                    r=3.0 + (h6 / 255.0) * 1.2,
                ))
                fruits_left -= 1
    return "".join(parts)


def render_willow_ts(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    return render_willow(state, ctx) + _ts_fruits(state)

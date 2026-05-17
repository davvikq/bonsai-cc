"""``render_birch`` -- the zig theme.

The birch silhouette: pale slender trunk (BIRCH_BARK off-white)
with horizontal lenticels (the dark slash marks) every ~50px.
Taller and slenderer than other themes. Foliage is small fresh-
green heart-shaped leaves.

This is the one theme where horizontal bark marks are correct --
birch lenticels run perpendicular to the grain, which is the
species' visual signature.
"""

from __future__ import annotations

from bonsai_cc.growth.state import TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_birch"]


_TRUNK_MAX_PX: float = 220.0  # taller than other themes -- birch signature
_TRUNK_MIN_PX: float = 120.0


def _trunk_top_y(state: TreeState) -> float:
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 7.0)
    return canvas.TREE_BASE_Y - (
        _TRUNK_MIN_PX + (_TRUNK_MAX_PX - _TRUNK_MIN_PX) * growth
    )


def _spine(state: TreeState) -> list[tuple[float, float]]:
    top_y = _trunk_top_y(state)
    base_x = float(canvas.ORIGIN_X)
    base_y = float(canvas.TREE_BASE_Y)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    lean = ((h0 / 255.0) - 0.5) * 22.0  # only slight curve
    return [
        (base_x, base_y),
        (base_x + lean * 0.20, base_y - (base_y - top_y) * 0.30),
        (base_x + lean, (base_y + top_y) / 2),
        (base_x + lean * 0.30, top_y),
    ]


def _trunk_path(state: TreeState) -> str:
    spine = _spine(state)
    return canvas.tapered_ribbon_path(
        spine, w_start=22.0, w_end=5.0,  # slender
        fill=tokens.BIRCH_BARK, taper_curve=1.5,
    )


def _bark(state: TreeState) -> str:
    """Horizontal lenticels -- birch signature, the one allowed
    exception to the "no horizontal trunk ribs" rule."""
    spine = _spine(state)
    return canvas.horizontal_bark_marks(
        spine, count=8, color=tokens.INK, width_estimator=18.0,
    )


def _spine_point_at(spine: list[tuple[float, float]], t: float) -> tuple[float, float]:
    if t <= 0:
        return spine[0]
    if t >= 1:
        return spine[-1]
    n = len(spine) - 1
    pos = t * n
    i = int(pos)
    u = pos - i
    p0 = spine[i]
    p1 = spine[i + 1]
    return p0[0] + (p1[0] - p0[0]) * u, p0[1] + (p1[1] - p0[1]) * u


def _heart_leaf(
    cx: float, cy: float, *, scale: float, angle_deg: float, fill: str
) -> str:
    """A small heart-shaped leaf -- two lobes meeting at the stem."""
    w = 6.0 * scale
    h = 8.0 * scale
    path = (
        f"M0,0 "
        f"C{-w * 0.5:.1f},{-h * 0.3:.1f} {-w:.1f},{-h * 0.85:.1f} 0,{-h:.1f} "
        f"C{w:.1f},{-h * 0.85:.1f} {w * 0.5:.1f},{-h * 0.3:.1f} 0,0 Z"
    )
    return (
        f'<g transform="translate({cx:.1f},{cy:.1f}) rotate({angle_deg:.1f})">'
        f'<path d="{path}" fill="{fill}" />'
        f"</g>"
    )


def _heart_cluster(
    cx: float, cy: float, *, seed: int, count: int, radius: float
) -> str:
    parts: list[str] = []
    for i in range(count):
        h0, h1, h2, h3 = canvas.quad_lcg(seed * 100 + i)
        dx = (h0 / 255.0 - 0.5) * 2 * radius
        dy = (h1 / 255.0 - 0.5) * 2 * radius * 0.75
        scale = 0.8 + (h2 / 255.0) * 0.5
        angle = (h3 / 255.0 - 0.5) * 80.0
        fill = (
            tokens.BIRCH_LEAF
            if (h3 & 0x40)
            else canvas.shade(tokens.BIRCH_LEAF, -0.15)
        )
        parts.append(_heart_leaf(cx + dx, cy + dy, scale=scale, angle_deg=angle, fill=fill))
    return "<g>" + "".join(parts) + "</g>"


def _branches_with_foliage(state: TreeState) -> str:
    out: list[str] = []
    spine = _spine(state)
    n_trunk = max(1, len(state.trunk))
    ab = canvas.abundance(state.event_count)
    for i, branch in enumerate(state.branches):
        if not branch.segments:
            continue
        t = min(0.92, max(0.30, branch.attach_point[1] / float(n_trunk)))
        ax, ay = _spine_point_at(spine, t)
        side = -1.0 if i % 2 == 0 else 1.0
        length = min(200.0, 95.0 + 14.0 * len(branch.segments))
        seed = max(1, branch.attach_point[1] * 23 + i + 1)
        h0, h1, _, _ = canvas.quad_lcg(seed)
        near = (ax + side * 14, ay - 2)
        mid = (ax + side * length * 0.55, ay - length * 0.12 + (h0 / 255.0 - 0.5) * 8)
        tip = (ax + side * length, ay - length * 0.30 + (h1 / 255.0 - 0.5) * 8)
        # Birch branches: thinner and paler.
        out.append(
            canvas.tapered_ribbon_path(
                [(ax, ay), near, mid, tip],
                w_start=7.0, w_end=2.0,
                fill=tokens.BIRCH_BARK, taper_curve=1.4,
            )
        )
        out.append(_heart_cluster(
            tip[0], tip[1], seed=seed * 5,
            count=int(11 * ab), radius=22 * ab,
        ))
        for li, _leaf in enumerate(branch.leaves):
            out.append(
                _heart_cluster(
                    mid[0] + (li - 1) * 10, mid[1] + 10 + li * 4,
                    seed=seed * 11 + li,
                    count=int(5 * ab), radius=12 * ab,
                )
            )
    return "".join(out)


def _apex(state: TreeState) -> str:
    spine = _spine(state)
    apex_x, apex_y = spine[-1]
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    flower_bonus = min(4, len(state.flowers))
    ab = canvas.abundance(state.event_count)
    return _heart_cluster(
        apex_x, apex_y - 6,
        seed=seed + 7,
        count=int((13 + flower_bonus) * ab),
        radius=(26 + flower_bonus * 2) * ab,
    )


def _offshoots(state: TreeState) -> str:
    if not state.offshoots:
        return ""
    out: list[str] = []
    for i, off in enumerate(state.offshoots):
        if not off.segments:
            continue
        ax, ay = canvas.project_xy(off.attach_point[0], off.attach_point[1])
        side = -1.0 if i % 2 == 0 else 1.0
        tx = ax + side * 50
        ty = ay + 4
        out.append(
            f'<path d="M{ax:.1f},{ay:.1f} Q{ax + side * 22:.1f},{ay - 5:.1f} '
            f'{tx:.1f},{ty:.1f}" stroke="{tokens.BIRCH_BARK}" stroke-width="1.8" '
            f'fill="none" stroke-linecap="round" />'
        )
        out.append(
            f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="4" fill="{tokens.CRAIL}" />'
        )
    return "".join(out)


def _roots(state: TreeState) -> str:
    if not state.roots:
        return ""
    base = canvas.TREE_BASE_Y - 2
    out: list[str] = []
    for i, _r in enumerate(state.roots):
        side = -1.0 if i % 2 == 0 else 1.0
        length = 22 + i * 5
        x0 = canvas.ORIGIN_X + side * 6
        x1 = canvas.ORIGIN_X + side * length
        out.append(
            f'<path d="M{x0:.1f},{base:.1f} Q{x0 + side * 10:.1f},{base + 4:.1f} '
            f'{x1:.1f},{base + 2:.1f}" stroke="{tokens.BARK_DEEP}" '
            f'stroke-width="2.6" fill="none" stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(out)


def render_birch(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    _ = ctx
    return (
        _trunk_path(state)
        + _bark(state)
        + _branches_with_foliage(state)
        + _apex(state)
        + _offshoots(state)
        + _roots(state)
    )

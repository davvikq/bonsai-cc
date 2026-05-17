"""``render_maple`` -- the ruby theme.

Medium trunk with a gentle S-curve. Branches spread wide and arc
slightly upward. Foliage is five-lobed maple leaves in a warm
autumn palette: MAPLE_RED + MAPLE_GOLD + CRAIL, interleaved within
each cluster. Some fallen leaves scatter on the soil inside the pot.
"""

from __future__ import annotations

import math

from bonsai_cc.growth.state import TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_maple"]


_TRUNK_MAX_PX: float = 190.0
_TRUNK_MIN_PX: float = 95.0


def _trunk_top_y(state: TreeState) -> float:
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 7.0)
    return canvas.TREE_BASE_Y - (
        _TRUNK_MIN_PX + (_TRUNK_MAX_PX - _TRUNK_MIN_PX) * growth
    )


def _spine(state: TreeState) -> list[tuple[float, float]]:
    """Gentle S: sway one way at the lower third, back the other at
    the upper third. Less dramatic than sakura's C-curve."""
    top_y = _trunk_top_y(state)
    base_x = float(canvas.ORIGIN_X)
    base_y = float(canvas.TREE_BASE_Y)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    side = 1.0 if (h0 & 1) else -1.0
    h_total = base_y - top_y
    return [
        (base_x, base_y),
        (base_x + side * 22.0, base_y - h_total * 0.30),
        (base_x - side * 18.0, base_y - h_total * 0.65),
        (base_x - side * 4.0, top_y),
    ]


def _trunk_path(state: TreeState) -> str:
    spine = _spine(state)
    return canvas.tapered_ribbon_path(
        spine, w_start=34.0, w_end=8.0,
        fill=tokens.BARK, taper_curve=1.6,
    )


def _bark(state: TreeState) -> str:
    spine = _spine(state)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    sway = spine[1][0] - spine[0][0]
    lit = 1.0 if sway >= 0 else -1.0
    return (
        canvas.bark_inner_shadow(
            spine, shadow_side=-lit,
            color=tokens.BARK_DEEP, width=3.5, opacity=0.35,
        )
        + canvas.vertical_bark_striations(
            spine, seed=seed * 7, side=lit, count=4,
            color=tokens.BARK_DEEP, opacity=0.40,
        )
        + canvas.vertical_bark_striations(
            spine, seed=seed * 11, side=-lit, count=3,
            color=tokens.BARK_DEEP, opacity=0.28,
        )
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


def _maple_leaf(
    cx: float, cy: float, *, scale: float, angle_deg: float, fill: str
) -> str:
    """Five-lobed maple leaf -- palmate shape via five small rotated
    triangles around a centre, plus a stem.

    Approximate but recognisable at thumbnail size. Each lobe is a
    pointed teardrop.
    """
    parts: list[str] = []
    # Five lobes spanning ~180° (the leaf has a stem at the bottom).
    for i in range(5):
        lobe_angle = -90.0 + (i - 2) * 36.0  # -90, -54, -18, 18, 54
        ln = 8.5 * scale
        wd = 4.0 * scale
        # Outer tip is along lobe_angle from leaf-local origin.
        rad = math.radians(lobe_angle)
        tip_x = math.cos(rad) * ln
        tip_y = math.sin(rad) * ln
        # Two side anchors midway.
        side_off = math.radians(lobe_angle + 90)
        sx = math.cos(rad) * ln * 0.45 + math.cos(side_off) * wd
        sy = math.sin(rad) * ln * 0.45 + math.sin(side_off) * wd
        sx2 = math.cos(rad) * ln * 0.45 - math.cos(side_off) * wd
        sy2 = math.sin(rad) * ln * 0.45 - math.sin(side_off) * wd
        parts.append(
            f'<path d="M0,0 Q{sx:.1f},{sy:.1f} {tip_x:.1f},{tip_y:.1f} '
            f'Q{sx2:.1f},{sy2:.1f} 0,0 Z" fill="{fill}" />'
        )
    # Stem.
    parts.append(
        f'<line x1="0" y1="0" x2="0" y2="{4.5 * scale:.1f}" '
        f'stroke="{tokens.BARK_DEEP}" stroke-width="{0.8 * scale:.1f}" />'
    )
    return (
        f'<g transform="translate({cx:.1f},{cy:.1f}) rotate({angle_deg:.1f})">'
        + "".join(parts)
        + "</g>"
    )


def _maple_cluster(
    cx: float, cy: float, *, seed: int, count: int, radius: float
) -> str:
    """Cluster of maple leaves with three-color autumn mix."""
    colors = (tokens.MAPLE_RED, tokens.CRAIL, tokens.MAPLE_GOLD)
    parts: list[str] = []
    for i in range(count):
        h0, h1, h2, h3 = canvas.quad_lcg(seed * 100 + i)
        dx = (h0 / 255.0 - 0.5) * 2 * radius
        dy = (h1 / 255.0 - 0.5) * 2 * radius * 0.8
        scale = 0.85 + (h2 / 255.0) * 0.5
        angle = (h3 / 255.0 - 0.5) * 90.0
        fill = colors[i % 3] if (h3 & 0x20) else colors[(i + 1) % 3]
        parts.append(_maple_leaf(cx + dx, cy + dy, scale=scale, angle_deg=angle, fill=fill))
    return "<g>" + "".join(parts) + "</g>"


def _fallen_leaves(state: TreeState) -> str:
    """A few maple leaves scattered on the soil inside the pot.

    Subtle -- only 5-8 small leaves at low opacity. Deterministic
    positions from the seed.
    """
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    # The pot rim is at ORIGIN_Y. Soil sits at y = ORIGIN_Y + 2.
    soil_y = canvas.ORIGIN_Y + 2
    half_w = 145.0  # inside the rim
    parts: list[str] = []
    colors = (tokens.MAPLE_RED, tokens.CRAIL, tokens.MAPLE_GOLD)
    count = 6 + min(2, len(state.flowers))
    for i in range(count):
        h0, h1, h2, h3 = canvas.quad_lcg(seed * 19 + i + 7)
        x = canvas.ORIGIN_X + (h0 / 255.0 - 0.5) * 2 * half_w
        y = soil_y + (h1 / 255.0) * 6
        scale = 0.7 + (h2 / 255.0) * 0.4
        angle = (h3 / 255.0) * 360.0
        fill = colors[i % 3]
        parts.append(
            '<g opacity="0.65">'
            + _maple_leaf(x, y, scale=scale, angle_deg=angle, fill=fill)
            + "</g>"
        )
    return "".join(parts)


def _branches_with_foliage(state: TreeState) -> str:
    out: list[str] = []
    spine = _spine(state)
    n_trunk = max(1, len(state.trunk))
    ab = canvas.abundance(state.event_count)
    for i, branch in enumerate(state.branches):
        if not branch.segments:
            continue
        t = min(0.90, max(0.25, branch.attach_point[1] / float(n_trunk)))
        ax, ay = _spine_point_at(spine, t)
        side = -1.0 if i % 2 == 0 else 1.0
        length = min(220.0, 110.0 + 15.0 * len(branch.segments))
        seed = max(1, branch.attach_point[1] * 23 + i + 1)
        h0, h1, _, _ = canvas.quad_lcg(seed)
        near = (ax + side * 18, ay + 2)
        mid = (ax + side * length * 0.55, ay - length * 0.12 + (h0 / 255.0 - 0.5) * 10)
        tip = (ax + side * length, ay - length * 0.28 + (h1 / 255.0 - 0.5) * 8)
        out.append(
            canvas.tapered_ribbon_path(
                [(ax, ay), near, mid, tip],
                w_start=10.0, w_end=2.5,
                fill=tokens.BARK, taper_curve=1.4,
            )
        )
        out.append(_maple_cluster(
            tip[0], tip[1], seed=seed * 7,
            count=int(11 * ab), radius=22 * ab,
        ))
        for li, _leaf in enumerate(branch.leaves):
            sx = mid[0] + (li - 1) * 12
            sy = mid[1] + 10 + li * 4
            out.append(_maple_cluster(
                sx, sy, seed=seed * 11 + li,
                count=int(5 * ab), radius=12 * ab,
            ))
    return "".join(out)


def _apex(state: TreeState) -> str:
    spine = _spine(state)
    apex_x, apex_y = spine[-1]
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    flower_bonus = min(4, len(state.flowers))
    ab = canvas.abundance(state.event_count)
    return _maple_cluster(
        apex_x, apex_y - 8,
        seed=seed + 7,
        count=int((14 + flower_bonus) * ab),
        radius=(30 + flower_bonus * 2) * ab,
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
        tx = ax + side * 55
        ty = ay + 4
        out.append(
            f'<path d="M{ax:.1f},{ay:.1f} Q{ax + side * 22:.1f},{ay - 6:.1f} '
            f'{tx:.1f},{ty:.1f}" stroke="{tokens.BARK}" stroke-width="2.2" '
            f'fill="none" stroke-linecap="round" />'
        )
        # Small soft Crail berry -- circle, never a star glyph.
        out.append(
            f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="4.5" fill="{tokens.CRAIL}" />'
        )
    return "".join(out)


def _roots(state: TreeState) -> str:
    if not state.roots:
        return ""
    base = canvas.TREE_BASE_Y - 2
    out: list[str] = []
    for i, _r in enumerate(state.roots):
        side = -1.0 if i % 2 == 0 else 1.0
        length = 26 + i * 6
        x0 = canvas.ORIGIN_X + side * 8
        x1 = canvas.ORIGIN_X + side * length
        out.append(
            f'<path d="M{x0:.1f},{base:.1f} Q{x0 + side * 12:.1f},{base + 6:.1f} '
            f'{x1:.1f},{base + 2:.1f}" stroke="{tokens.BARK_DEEP}" '
            f'stroke-width="3.2" fill="none" stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(out)


def render_maple(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    _ = ctx
    return (
        _trunk_path(state)
        + _bark(state)
        + _branches_with_foliage(state)
        + _apex(state)
        + _offshoots(state)
        + _roots(state)
        + _fallen_leaves(state)
    )

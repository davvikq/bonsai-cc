"""``render_ginkgo`` -- the haskell theme.

Slender upright trunk with a very slight curve -- barely more
asymmetric than a column. The leaves are the standout: fan-shaped
(like small folded paper fans) in gold (GINKGO_GOLD with
GINKGO_GOLD_LIGHT highlights). A few fans drift down through the
air. Autumn ginkgo feel.
"""

from __future__ import annotations

import math

from bonsai_cc.growth.state import TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_ginkgo"]


_TRUNK_MAX_PX: float = 200.0
_TRUNK_MIN_PX: float = 105.0


def _trunk_top_y(state: TreeState) -> float:
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 7.0)
    return canvas.TREE_BASE_Y - (
        _TRUNK_MIN_PX + (_TRUNK_MAX_PX - _TRUNK_MIN_PX) * growth
    )


def _spine(state: TreeState) -> list[tuple[float, float]]:
    """Very slight curve -- ginkgo bonsai are usually formal upright
    (chokkan) or slight informal upright (moyogi). We pick the latter
    so it doesn't read as ramrod stiff."""
    top_y = _trunk_top_y(state)
    base_x = float(canvas.ORIGIN_X)
    base_y = float(canvas.TREE_BASE_Y)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    lean = ((h0 / 255.0) - 0.5) * 22.0  # very gentle
    return [
        (base_x, base_y),
        (base_x + lean * 0.20, base_y - (base_y - top_y) * 0.30),
        (base_x + lean, (base_y + top_y) / 2),
        (base_x + lean * 0.30, top_y),
    ]


def _trunk_path(state: TreeState) -> str:
    spine = _spine(state)
    return canvas.tapered_ribbon_path(
        spine, w_start=26.0, w_end=6.0,
        fill=tokens.BARK, taper_curve=1.5,
    )


def _bark(state: TreeState) -> str:
    spine = _spine(state)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    sway = spine[2][0] - spine[0][0]
    lit = 1.0 if sway >= 0 else -1.0
    return (
        canvas.bark_inner_shadow(
            spine, shadow_side=-lit, color=tokens.BARK_DEEP,
            width=3.0, opacity=0.34,
        )
        + canvas.vertical_bark_striations(
            spine, seed=seed * 7, side=lit, count=4,
            color=tokens.BARK_DEEP, opacity=0.36,
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


def _ginkgo_fan(
    cx: float, cy: float, *, scale: float, angle_deg: float, fill: str
) -> str:
    """One ginkgo leaf -- a small fan with a notched centre.

    Drawn as a wedge from the leaf-local origin spanning ~80° with
    a stem going down. A subtle notch at the top suggests the
    classic ginkgo cleft.
    """
    radius = 10.0 * scale
    half_span = math.radians(40.0)
    # Two side points + a wedge.
    x1 = math.cos(math.radians(-90) - half_span) * radius
    y1 = math.sin(math.radians(-90) - half_span) * radius
    x2 = math.cos(math.radians(-90) + half_span) * radius
    y2 = math.sin(math.radians(-90) + half_span) * radius
    # Notch points along the top edge.
    nx = 0.0
    ny = -radius * 0.85  # slightly less than the full radius
    fan_path = (
        f"M0,0 "
        f"L{x1:.1f},{y1:.1f} "
        f"A{radius:.1f},{radius:.1f} 0 0,1 {nx - 0.5:.1f},{ny:.1f} "
        f"L{nx + 0.5:.1f},{ny:.1f} "
        f"A{radius:.1f},{radius:.1f} 0 0,1 {x2:.1f},{y2:.1f} "
        f"Z"
    )
    stem = (
        f'<line x1="0" y1="0" x2="0" y2="{4.5 * scale:.1f}" '
        f'stroke="{tokens.BARK_DEEP}" stroke-width="{0.7 * scale:.2f}" />'
    )
    return (
        f'<g transform="translate({cx:.1f},{cy:.1f}) rotate({angle_deg:.1f})">'
        f'<path d="{fan_path}" fill="{fill}" />'
        + stem
        + "</g>"
    )


def _ginkgo_cluster(
    cx: float, cy: float, *, seed: int, count: int, radius: float
) -> str:
    parts: list[str] = []
    for i in range(count):
        h0, h1, h2, h3 = canvas.quad_lcg(seed * 100 + i)
        dx = (h0 / 255.0 - 0.5) * 2 * radius
        dy = (h1 / 255.0 - 0.5) * 2 * radius * 0.7
        scale = 0.7 + (h2 / 255.0) * 0.6
        angle = (h3 / 255.0 - 0.5) * 100.0
        fill = tokens.GINKGO_GOLD if (h3 & 0x40) else tokens.GINKGO_GOLD_LIGHT
        parts.append(_ginkgo_fan(cx + dx, cy + dy, scale=scale, angle_deg=angle, fill=fill))
    return "<g>" + "".join(parts) + "</g>"


def _drifting_fans(state: TreeState) -> str:
    """A few fans drifting down through the air -- autumn shedding."""
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    parts: list[str] = []
    for i in range(5):
        h0, h1, h2, h3 = canvas.quad_lcg(seed * 7 + i)
        x = 220 + (h0 / 255.0) * 560
        y = 90 + (h1 / 255.0) * 330
        scale = 0.4 + (h2 / 255.0) * 0.35
        angle = (h3 / 255.0 - 0.5) * 180.0
        fill = tokens.GINKGO_GOLD if (h3 & 0x10) else tokens.GINKGO_GOLD_LIGHT
        parts.append(
            '<g opacity="0.45">'
            + _ginkgo_fan(x, y, scale=scale, angle_deg=angle, fill=fill)
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
        length = min(200.0, 95.0 + 14.0 * len(branch.segments))
        seed = max(1, branch.attach_point[1] * 23 + i + 1)
        h0, h1, _, _ = canvas.quad_lcg(seed)
        near = (ax + side * 16, ay - 2)
        mid = (ax + side * length * 0.55, ay - length * 0.16 + (h0 / 255.0 - 0.5) * 8)
        tip = (ax + side * length, ay - length * 0.34 + (h1 / 255.0 - 0.5) * 8)
        out.append(
            canvas.tapered_ribbon_path(
                [(ax, ay), near, mid, tip],
                w_start=8.0, w_end=2.0,
                fill=tokens.BARK, taper_curve=1.4,
            )
        )
        out.append(_ginkgo_cluster(
            tip[0], tip[1], seed=seed * 5,
            count=int(10 * ab), radius=20 * ab,
        ))
        for li, _leaf in enumerate(branch.leaves):
            out.append(
                _ginkgo_cluster(
                    mid[0] + (li - 1) * 10, mid[1] + 8 + li * 4,
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
    return _ginkgo_cluster(
        apex_x, apex_y - 8,
        seed=seed + 7,
        count=int((13 + flower_bonus) * ab),
        radius=(28 + flower_bonus * 2) * ab,
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
        tx = ax + side * 52
        ty = ay + 4
        out.append(
            f'<path d="M{ax:.1f},{ay:.1f} Q{ax + side * 22:.1f},{ay - 6:.1f} '
            f'{tx:.1f},{ty:.1f}" stroke="{tokens.BARK}" stroke-width="2.0" '
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
        length = 24 + i * 6
        x0 = canvas.ORIGIN_X + side * 8
        x1 = canvas.ORIGIN_X + side * length
        out.append(
            f'<path d="M{x0:.1f},{base:.1f} Q{x0 + side * 11:.1f},{base + 5:.1f} '
            f'{x1:.1f},{base + 2:.1f}" stroke="{tokens.BARK_DEEP}" '
            f'stroke-width="2.8" fill="none" stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(out)


def render_ginkgo(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    _ = ctx
    return (
        _drifting_fans(state)
        + _trunk_path(state)
        + _bark(state)
        + _branches_with_foliage(state)
        + _apex(state)
        + _offshoots(state)
        + _roots(state)
    )

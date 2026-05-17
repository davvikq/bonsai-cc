"""``render_banyan`` -- the java theme.

Medium-width main trunk. The distinguishing signature is aerial
roots: thin curved lines descending from horizontal branch midpoints
all the way to the ground, looking like extra trunks. Banyan bonsai
without aerial roots aren't banyan. We draw 4-6 of them.

Foliage is dark green broad rounded clusters -- similar to oak but
denser and darker.
"""

from __future__ import annotations

from bonsai_cc.growth.state import Branch, TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_banyan"]


_TRUNK_MAX_PX: float = 200.0
_TRUNK_MIN_PX: float = 110.0


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
    lean = ((h0 / 255.0) - 0.5) * 30.0
    return [
        (base_x, base_y),
        (base_x + lean * 0.25, base_y - (base_y - top_y) * 0.30),
        (base_x + lean, (base_y + top_y) / 2),
        (base_x + lean * 0.30, top_y),
    ]


def _trunk_path(state: TreeState) -> str:
    spine = _spine(state)
    return canvas.tapered_ribbon_path(
        spine, w_start=36.0, w_end=10.0,
        fill=tokens.BARK, taper_curve=1.6,
    )


def _bark(state: TreeState) -> str:
    spine = _spine(state)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    sway = spine[2][0] - spine[0][0]
    lit = 1.0 if sway >= 0 else -1.0
    return (
        canvas.bark_inner_shadow(
            spine, shadow_side=-lit, color=tokens.BARK_DEEP,
            width=4.0, opacity=0.38,
        )
        + canvas.vertical_bark_striations(
            spine, seed=seed * 7, side=lit, count=5,
            color=tokens.BARK_DEEP, opacity=0.40,
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


def _branch(branch: Branch, state: TreeState, i: int) -> tuple[str, tuple[float, float], tuple[float, float]]:
    """Returns (svg, midpoint, tip) so the aerial-root pass can hang
    a root from the midpoint."""
    if not branch.segments:
        return "", (0.0, 0.0), (0.0, 0.0)
    spine = _spine(state)
    n_trunk = max(1, len(state.trunk))
    t = min(0.90, max(0.30, branch.attach_point[1] / float(n_trunk)))
    ax, ay = _spine_point_at(spine, t)
    side = -1.0 if i % 2 == 0 else 1.0
    length = min(230.0, 110.0 + 16.0 * len(branch.segments))
    seed = max(1, branch.attach_point[1] * 19 + i + 1)
    h0, h1, _, _ = canvas.quad_lcg(seed)
    near = (ax + side * 18, ay + 2)
    mid = (ax + side * length * 0.55, ay - length * 0.05 + (h0 / 255.0 - 0.5) * 10)
    tip = (ax + side * length, ay - length * 0.18 + (h1 / 255.0 - 0.5) * 8)
    svg = canvas.tapered_ribbon_path(
        [(ax, ay), near, mid, tip],
        w_start=12.0, w_end=2.6,
        fill=tokens.BARK, taper_curve=1.4,
    )
    return svg, mid, tip


def _aerial_root(start: tuple[float, float], state: TreeState, seed: int) -> str:
    """A thin curved line from a branch midpoint down to the soil."""
    sx, sy = start
    end_y = canvas.TREE_BASE_Y - 4
    h0, h1, _, _ = canvas.quad_lcg(seed)
    drift = (h0 / 255.0 - 0.5) * 60.0
    ex = sx + drift
    # Two control points to give the root a gentle S -- like it's
    # been pushed by air or growth, never a straight drop.
    c1 = (sx + drift * 0.25, sy + (end_y - sy) * 0.40)
    c2 = (ex - drift * 0.20, sy + (end_y - sy) * 0.75)
    # Width tapers from 5 (top, where it joins the branch) to 7
    # (bottom, where it thickens into trunk-like girth).
    pts = [(sx, sy), c1, c2, (ex, end_y)]
    body = canvas.tapered_ribbon_path(
        pts, w_start=4.0, w_end=8.0,
        fill=tokens.BARK, taper_curve=0.9,
    )
    _ = h1
    return body


def _branches_with_foliage_and_aerial(state: TreeState) -> tuple[str, str]:
    """Returns (branch+foliage svg, aerial-roots svg).

    Aerial roots are kept separate so the dispatcher can put them
    BEHIND the trunk for depth.
    """
    branches_svg: list[str] = []
    aerial_svg: list[str] = []
    darker = canvas.shade(tokens.MOSS, -0.15)
    # Choose 4-6 branches to host aerial roots; the rest stay clean.
    root_targets = min(6, max(4, len(state.branches) // 2 + 4))
    ab = canvas.abundance(state.event_count)
    for i, branch in enumerate(state.branches):
        if not branch.segments:
            continue
        svg, mid, tip = _branch(branch, state, i)
        branches_svg.append(svg)
        seed = (branch.attach_point[1] + 1) * 91 + i
        if i < root_targets:
            aerial_svg.append(_aerial_root(mid, state, seed * 5))
        # Foliage at the tip; count/radius track session abundance.
        branches_svg.append(
            canvas.leaf_cluster(
                tip[0], tip[1],
                seed=seed,
                base_fill=darker,
                highlight_fill=tokens.MOSS,
                count=int(13 * ab),
                radius=28 * ab,
                leaf_rx=9, leaf_ry=11,
            )
        )
        for li, leaf in enumerate(branch.leaves):
            side = -1.0 if i % 2 == 0 else 1.0
            sx = tip[0] - side * 26 * (1 + li * 0.5)
            sy = tip[1] + 22 + li * 5
            if leaf.color is not None:
                base, hi, ct, rd = leaf.color, canvas.shade(leaf.color, 0.18), 4, 12.0
            else:
                base, hi, ct, rd = darker, tokens.MOSS, 7, 14.0
            branches_svg.append(
                canvas.leaf_cluster(
                    sx, sy,
                    seed=leaf.birth_event_idx * 13 + li + 1,
                    base_fill=base, highlight_fill=hi,
                    count=ct, radius=rd, leaf_rx=7, leaf_ry=10,
                )
            )
    return "".join(branches_svg), "".join(aerial_svg)


def _apex(state: TreeState) -> str:
    spine = _spine(state)
    apex_x, apex_y = spine[-1]
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    darker = canvas.shade(tokens.MOSS, -0.15)
    flower_bonus = min(4, len(state.flowers))
    ab = canvas.abundance(state.event_count)
    return canvas.leaf_cluster(
        apex_x, apex_y - 8,
        seed=seed + 7,
        base_fill=darker,
        highlight_fill=tokens.MOSS,
        count=int((14 + flower_bonus) * ab),
        radius=(34 + flower_bonus * 2) * ab,
        leaf_rx=10, leaf_ry=12,
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
        tx = ax + side * 58
        ty = ay + 6
        out.append(
            f'<path d="M{ax:.1f},{ay:.1f} Q{ax + side * 24:.1f},{ay - 5:.1f} '
            f'{tx:.1f},{ty:.1f}" stroke="{tokens.BARK}" stroke-width="2.4" '
            f'fill="none" stroke-linecap="round" />'
        )
        out.append(
            f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="5" fill="{tokens.CRAIL}" />'
        )
    return "".join(out)


def _roots(state: TreeState) -> str:
    if not state.roots:
        return ""
    base = canvas.TREE_BASE_Y - 2
    out: list[str] = []
    for i, _r in enumerate(state.roots):
        side = -1.0 if i % 2 == 0 else 1.0
        length = 28 + i * 6
        x0 = canvas.ORIGIN_X + side * 10
        x1 = canvas.ORIGIN_X + side * length
        out.append(
            f'<path d="M{x0:.1f},{base:.1f} Q{x0 + side * 12:.1f},{base + 6:.1f} '
            f'{x1:.1f},{base + 2:.1f}" stroke="{tokens.BARK_DEEP}" '
            f'stroke-width="3.4" fill="none" stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(out)


def render_banyan(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    _ = ctx
    branches, aerial = _branches_with_foliage_and_aerial(state)
    # Order: aerial roots paint BEHIND the trunk so the trunk
    # occludes the upper portion where the aerial root joins.
    return (
        aerial
        + _trunk_path(state)
        + _bark(state)
        + branches
        + _apex(state)
        + _offshoots(state)
        + _roots(state)
    )

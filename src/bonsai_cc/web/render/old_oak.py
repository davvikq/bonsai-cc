"""``render_old_oak`` -- the c / cpp theme.

Same general shape as oak but: trunk MUCH thicker (62px base),
visible deadwood patches (jin) where the bark has fallen off
revealing pale weathered wood, a couple of gnarled knots on the
trunk, and one branch deliberately stubby/broken in jin style.
Foliage is darker and denser than regular oak.
"""

from __future__ import annotations

from bonsai_cc.growth.state import Branch, TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_old_oak"]


_TRUNK_MAX_PX: float = 175.0
_TRUNK_MIN_PX: float = 95.0


def _trunk_top_y(state: TreeState) -> float:
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 8.0)
    return canvas.TREE_BASE_Y - (
        _TRUNK_MIN_PX + (_TRUNK_MAX_PX - _TRUNK_MIN_PX) * growth
    )


def _spine(state: TreeState) -> list[tuple[float, float]]:
    """Old oak: still squat, but with a more dramatic mid-trunk
    bulge -- character marks of age."""
    top_y = _trunk_top_y(state)
    base_x = float(canvas.ORIGIN_X)
    base_y = float(canvas.TREE_BASE_Y)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    lean = ((h0 / 255.0) - 0.5) * 38.0
    return [
        (base_x, base_y),
        (base_x + lean * 0.30, base_y - (base_y - top_y) * 0.28),
        (base_x + lean, (base_y + top_y) / 2),
        (base_x + lean * 0.40, top_y),
    ]


def _trunk_path(state: TreeState) -> str:
    spine = _spine(state)
    return canvas.tapered_ribbon_path(
        spine, w_start=62.0, w_end=12.0,  # MUCH thicker base
        fill=tokens.BARK, taper_curve=1.8,
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


def _bark_and_deadwood(state: TreeState) -> str:
    """Bark texture + 2 deadwood patches (jin) + 2 knots."""
    spine = _spine(state)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    sway = spine[2][0] - spine[0][0]
    lit = 1.0 if sway >= 0 else -1.0
    out = [
        canvas.bark_inner_shadow(
            spine, shadow_side=-lit, color=tokens.BARK_DEEP,
            width=6.0, opacity=0.45,
        ),
        canvas.vertical_bark_striations(
            spine, seed=seed * 7, side=lit, count=6,
            color=tokens.BARK_DEEP, opacity=0.50,
        ),
        canvas.vertical_bark_striations(
            spine, seed=seed * 11, side=-lit, count=5,
            color=tokens.BARK_DEEP, opacity=0.35,
        ),
    ]
    # Two deadwood patches -- pale weathered wood (CLOUDY) showing
    # through the bark on the lit side.
    for i, t in enumerate((0.32, 0.62)):
        h0, h1, _, _ = canvas.quad_lcg(seed * 23 + i * 11 + 1)
        cx, cy = _spine_point_at(spine, t)
        offset = lit * (8 + (h0 / 255.0) * 4)
        # An irregular pale patch using a soft Bezier blob.
        w = 16 + (h1 / 255.0) * 8
        h = 26 + (h1 / 255.0) * 8
        out.append(
            f'<path d="M{cx + offset - w / 2:.1f},{cy:.1f} '
            f'Q{cx + offset - w / 2 - 2:.1f},{cy - h / 2:.1f} '
            f'{cx + offset:.1f},{cy - h / 2:.1f} '
            f'Q{cx + offset + w / 2 + 2:.1f},{cy - h / 4:.1f} '
            f'{cx + offset + w / 2:.1f},{cy + h / 3:.1f} '
            f'Q{cx + offset:.1f},{cy + h / 2:.1f} '
            f'{cx + offset - w / 2:.1f},{cy:.1f} Z" '
            f'fill="{tokens.CLOUDY}" opacity="0.95" />'
        )
        # Subtle darker stripe down the centre of the deadwood
        # suggesting fissure / weathering grain.
        out.append(
            f'<line x1="{cx + offset:.1f}" y1="{cy - h / 3:.1f}" '
            f'x2="{cx + offset + 1:.1f}" y2="{cy + h / 3:.1f}" '
            f'stroke="{tokens.BARK_DEEP}" stroke-width="1.2" opacity="0.55" />'
        )
    # Two gnarled knots: small dark concentric circles on the trunk.
    for i, t in enumerate((0.18, 0.48)):
        h0, _, _, _ = canvas.quad_lcg(seed * 31 + i * 7 + 5)
        cx, cy = _spine_point_at(spine, t)
        side = -lit if i == 0 else lit
        kx = cx + side * (6 + (h0 / 255.0) * 4)
        ky = cy
        out.append(
            f'<circle cx="{kx:.1f}" cy="{ky:.1f}" r="5" '
            f'fill="{tokens.BARK_DEEP}" opacity="0.85" />'
            f'<circle cx="{kx:.1f}" cy="{ky:.1f}" r="2.5" '
            f'fill="{tokens.BARK}" opacity="0.95" />'
        )
    return "".join(out)


def _branch(branch: Branch, state: TreeState, i: int) -> str:
    if not branch.segments:
        return ""
    spine = _spine(state)
    n_trunk = max(1, len(state.trunk))
    t = min(0.90, max(0.28, branch.attach_point[1] / float(n_trunk)))
    ax, ay = _spine_point_at(spine, t)
    side = -1.0 if i % 2 == 0 else 1.0
    # First branch in the list (index 0) is the deliberately
    # stubby/broken jin branch.
    is_jin = i == 0
    if is_jin:
        # Short stub, ending sharply with no foliage cluster.
        length = 56.0
        near = (ax + side * 14, ay + 2)
        mid = (ax + side * length * 0.6, ay - 4)
        tip = (ax + side * length, ay - 2)
        return canvas.tapered_ribbon_path(
            [(ax, ay), near, mid, tip],
            w_start=16.0, w_end=3.0,
            fill=tokens.CLOUDY, taper_curve=1.4,
        )
    length = min(185.0, 105.0 + 10.0 * len(branch.segments))
    seed = max(1, branch.attach_point[1] * 23 + i + 1)
    h0, h1, _, _ = canvas.quad_lcg(seed)
    near = (ax + side * 22, ay + 4)
    mid = (ax + side * length * 0.55, ay - length * 0.06 + (h0 / 255.0 - 0.5) * 10)
    tip = (ax + side * length, ay - length * 0.22 + (h1 / 255.0 - 0.5) * 8)
    return canvas.tapered_ribbon_path(
        [(ax, ay), near, mid, tip],
        w_start=15.0, w_end=3.0,
        fill=tokens.BARK, taper_curve=1.4,
    )


def _branch_tip(branch: Branch, state: TreeState, i: int) -> tuple[float, float] | None:
    """Returns None for the jin (broken) branch -- no foliage."""
    if not branch.segments or i == 0:
        return None
    spine = _spine(state)
    n_trunk = max(1, len(state.trunk))
    t = min(0.90, max(0.28, branch.attach_point[1] / float(n_trunk)))
    ax, ay = _spine_point_at(spine, t)
    side = -1.0 if i % 2 == 0 else 1.0
    length = min(185.0, 105.0 + 10.0 * len(branch.segments))
    return ax + side * length, ay - length * 0.22


def _branches_with_foliage(state: TreeState) -> str:
    out: list[str] = []
    # Use a darker MOSS for old-oak -- older oaks have deeper canopy.
    darker = canvas.shade(tokens.MOSS, -0.18)
    ab = canvas.abundance(state.event_count)
    for i, branch in enumerate(state.branches):
        out.append(_branch(branch, state, i))
        tip = _branch_tip(branch, state, i)
        if tip is None:
            continue
        tip_x, tip_y = tip
        seed = (branch.attach_point[1] + 1) * 91 + i
        out.append(
            canvas.leaf_cluster(
                tip_x, tip_y,
                seed=seed,
                base_fill=darker,
                highlight_fill=tokens.MOSS,
                count=int(15 * ab),
                radius=34 * ab,
                leaf_rx=10, leaf_ry=12,
            )
        )
        for li, leaf in enumerate(branch.leaves):
            side = -1.0 if i % 2 == 0 else 1.0
            sx = tip_x - side * 30 * (1 + li * 0.5)
            sy = tip_y + 22 + li * 6
            if leaf.color is not None:
                base, hi, ct, rd = leaf.color, canvas.shade(leaf.color, 0.18), 4, 12.0
            else:
                base, hi, ct, rd = darker, tokens.MOSS, 8, 16.0
            out.append(
                canvas.leaf_cluster(
                    sx, sy,
                    seed=leaf.birth_event_idx * 13 + li + 1,
                    base_fill=base, highlight_fill=hi,
                    count=ct, radius=rd, leaf_rx=7, leaf_ry=10,
                )
            )
    return "".join(out)


def _apex(state: TreeState) -> str:
    spine = _spine(state)
    apex_x, apex_y = spine[-1]
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    flower_bonus = min(4, len(state.flowers))
    ab = canvas.abundance(state.event_count)
    darker = canvas.shade(tokens.MOSS, -0.18)
    return canvas.leaf_cluster(
        apex_x, apex_y - 10,
        seed=seed + 7,
        base_fill=darker,
        highlight_fill=tokens.MOSS,
        count=int((17 + flower_bonus) * ab),
        radius=(40 + flower_bonus * 2) * ab,
        leaf_rx=11, leaf_ry=13,
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
            f'<path d="M{ax:.1f},{ay:.1f} Q{ax + side * 26:.1f},{ay - 4:.1f} '
            f'{tx:.1f},{ty:.1f}" stroke="{tokens.BARK}" stroke-width="2.6" '
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
        length = 36 + i * 8
        x0 = canvas.ORIGIN_X + side * 14
        x1 = canvas.ORIGIN_X + side * length
        out.append(
            f'<path d="M{x0:.1f},{base:.1f} Q{x0 + side * 16:.1f},{base + 9:.1f} '
            f'{x1:.1f},{base + 2:.1f}" stroke="{tokens.BARK_DEEP}" '
            f'stroke-width="4.2" fill="none" stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(out)


def render_old_oak(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    _ = ctx
    return (
        _trunk_path(state)
        + _bark_and_deadwood(state)
        + _branches_with_foliage(state)
        + _apex(state)
        + _offshoots(state)
        + _roots(state)
    )

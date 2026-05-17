"""``render_oak`` -- the go theme.

Squat thick trunk. Much thicker base relative to height than other
themes. Wide spreading horizontal branches that arc gently upward
at the tip. Large rounded dense foliage clusters in moss + yellower
highlight. Overall feeling: stable, mature, strong.
"""

from __future__ import annotations

from bonsai_cc.growth.state import Branch, TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_oak"]


_TRUNK_MAX_PX: float = 180.0  # shorter than generic -- oak is squat
_TRUNK_MIN_PX: float = 90.0


def _trunk_top_y(state: TreeState) -> float:
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 8.0)
    height_px = _TRUNK_MIN_PX + (_TRUNK_MAX_PX - _TRUNK_MIN_PX) * growth
    return canvas.TREE_BASE_Y - height_px


def _spine(state: TreeState) -> list[tuple[float, float]]:
    """Oak trunks are short and only mildly asymmetric -- stability
    over drama."""
    top_y = _trunk_top_y(state)
    base_x = float(canvas.ORIGIN_X)
    base_y = float(canvas.TREE_BASE_Y)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    lean = ((h0 / 255.0) - 0.5) * 28.0
    return [
        (base_x, base_y),
        (base_x + lean * 0.20, base_y - (base_y - top_y) * 0.28),
        (base_x + lean, (base_y + top_y) / 2),
        (base_x + lean * 0.30, top_y),
    ]


def _trunk_path(state: TreeState) -> str:
    spine = _spine(state)
    return canvas.tapered_ribbon_path(
        spine,
        w_start=52.0,  # much thicker base -- oak signature
        w_end=10.0,
        fill=tokens.BARK,
        taper_curve=1.8,
    )


def _bark(state: TreeState) -> str:
    spine = _spine(state)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    sway = spine[2][0] - spine[0][0]
    lit = 1.0 if sway >= 0 else -1.0
    return (
        canvas.bark_inner_shadow(
            spine, shadow_side=-lit, color=tokens.BARK_DEEP,
            width=5.0, opacity=0.40,
        )
        + canvas.vertical_bark_striations(
            spine, seed=seed * 7, side=lit, count=6,
            color=tokens.BARK_DEEP, opacity=0.45,
        )
        + canvas.vertical_bark_striations(
            spine, seed=seed * 11, side=-lit, count=4,
            color=tokens.BARK_DEEP, opacity=0.30,
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


def _branch(branch: Branch, state: TreeState, i: int) -> str:
    if not branch.segments:
        return ""
    spine = _spine(state)
    n_trunk = max(1, len(state.trunk))
    t = min(0.92, max(0.30, branch.attach_point[1] / float(n_trunk)))
    ax, ay = _spine_point_at(spine, t)
    side = -1.0 if i % 2 == 0 else 1.0
    # Wide horizontal spread: 130-260px out from the trunk.
    length = min(190.0, 110.0 + 11.0 * len(branch.segments))
    seed = max(1, branch.attach_point[1] * 19 + i + 1)
    h0, h1, _, _ = canvas.quad_lcg(seed)
    near = (ax + side * 22.0, ay + 4.0)
    mid = (
        ax + side * length * 0.55,
        ay - length * 0.05 + (h0 / 255.0 - 0.5) * 10.0,
    )
    # Slight upward arc at the tip -- oak signature.
    tip = (
        ax + side * length,
        ay - length * 0.22 + (h1 / 255.0 - 0.5) * 8.0,
    )
    return canvas.tapered_ribbon_path(
        [(ax, ay), near, mid, tip],
        w_start=14.0, w_end=3.0,
        fill=tokens.BARK, taper_curve=1.4,
    )


def _branch_tip(branch: Branch, state: TreeState, i: int) -> tuple[float, float]:
    if not branch.segments:
        spine = _spine(state)
        return _spine_point_at(
            spine, min(0.92, max(0.30, branch.attach_point[1] / max(1, len(state.trunk)))),
        )
    spine = _spine(state)
    n_trunk = max(1, len(state.trunk))
    t = min(0.92, max(0.30, branch.attach_point[1] / float(n_trunk)))
    ax, ay = _spine_point_at(spine, t)
    side = -1.0 if i % 2 == 0 else 1.0
    length = min(190.0, 110.0 + 11.0 * len(branch.segments))
    return ax + side * length, ay - length * 0.22


def _branches_with_foliage(state: TreeState) -> str:
    out: list[str] = []
    ab = canvas.abundance(state.event_count)
    for i, branch in enumerate(state.branches):
        out.append(_branch(branch, state, i))
        tip_x, tip_y = _branch_tip(branch, state, i)
        seed = (branch.attach_point[1] + 1) * 91 + i
        # Dense round cluster -- oak foliage signature. Cluster count
        # and radius scale with abundance so a long session reads as
        # visibly fuller than a short one.
        out.append(
            canvas.leaf_cluster(
                tip_x, tip_y,
                seed=seed,
                base_fill=tokens.MOSS,
                highlight_fill=tokens.LEAF_HIGHLIGHT,
                count=int(14 * ab),
                radius=32 * ab,
                leaf_rx=10, leaf_ry=12,
            )
        )
        # Per-leaf-event satellite clusters.
        for li, leaf in enumerate(branch.leaves):
            side = -1.0 if i % 2 == 0 else 1.0
            sx = tip_x - side * 30 * (1 + li * 0.5)
            sy = tip_y + 22 + li * 6
            if leaf.color is not None:
                base, hi, ct, rd = leaf.color, canvas.shade(leaf.color, 0.18), 4, 12.0
            else:
                base, hi, ct, rd = tokens.MOSS, tokens.LEAF_HIGHLIGHT, 8, 16.0
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
    return canvas.leaf_cluster(
        apex_x, apex_y - 10,
        seed=seed + 7,
        base_fill=tokens.MOSS,
        highlight_fill=tokens.LEAF_HIGHLIGHT,
        count=int((16 + flower_bonus) * ab),
        radius=(38 + flower_bonus * 2) * ab,
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
        tx = ax + side * 60.0
        ty = ay + 6.0
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
        length = 32 + i * 7  # oak roots are stout
        x0 = canvas.ORIGIN_X + side * 12
        x1 = canvas.ORIGIN_X + side * length
        out.append(
            f'<path d="M{x0:.1f},{base:.1f} Q{x0 + side * 14:.1f},{base + 8:.1f} '
            f'{x1:.1f},{base + 2:.1f}" stroke="{tokens.BARK_DEEP}" '
            f'stroke-width="4" fill="none" stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(out)


def render_oak(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    _ = ctx
    return (
        _trunk_path(state)
        + _bark(state)
        + _branches_with_foliage(state)
        + _apex(state)
        + _offshoots(state)
        + _roots(state)
    )

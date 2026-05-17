"""``render_willow`` -- the javascript theme.

Slanting style (shakan): the trunk leans noticeably to one side and
stays leaning -- no S-curve. All branches extend horizontally then
arc DOWNWARD at the tips, with multiple wispy leaf strands hanging
from each tip like a curtain. Foliage is small narrow leaves in a
softer green (WILLOW_LEAF), not moss.
"""

from __future__ import annotations

from bonsai_cc.growth.state import Branch, TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_willow"]


_TRUNK_MAX_PX: float = 200.0
_TRUNK_MIN_PX: float = 100.0


def _trunk_top_y(state: TreeState) -> float:
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 7.0)
    return canvas.TREE_BASE_Y - (
        _TRUNK_MIN_PX + (_TRUNK_MAX_PX - _TRUNK_MIN_PX) * growth
    )


def _spine(state: TreeState) -> list[tuple[float, float]]:
    """Sustained lean -- shakan style. The trunk never returns to
    centre; the apex stays well off to one side."""
    top_y = _trunk_top_y(state)
    base_x = float(canvas.ORIGIN_X)
    base_y = float(canvas.TREE_BASE_Y)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    side = 1.0 if (h0 & 1) else -1.0
    sustained = side * 70.0
    return [
        (base_x, base_y),
        (base_x + sustained * 0.25, base_y - (base_y - top_y) * 0.30),
        (base_x + sustained * 0.65, (base_y + top_y) / 2),
        (base_x + sustained, top_y),
    ]


def _trunk_path(state: TreeState) -> str:
    spine = _spine(state)
    return canvas.tapered_ribbon_path(
        spine, w_start=28.0, w_end=7.0,
        fill=tokens.BARK, taper_curve=1.5,
    )


def _bark(state: TreeState) -> str:
    spine = _spine(state)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    # Convex side catches light → opposite of the lean direction is
    # the shaded side.
    lean = spine[-1][0] - spine[0][0]
    shadow_side = -1.0 if lean >= 0 else 1.0
    return (
        canvas.bark_inner_shadow(
            spine, shadow_side=shadow_side,
            color=tokens.BARK_DEEP, width=3.5, opacity=0.36,
        )
        + canvas.vertical_bark_striations(
            spine, seed=seed * 7, side=-shadow_side, count=4,
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


def _branch_geometry(
    branch: Branch, state: TreeState, i: int
) -> tuple[float, float, float, float, float] | None:
    """Return ``(ax, ay, side, length, tip_y)`` for branch ``i``.

    Single source of truth for ``_drooping_branch`` and the curtain
    anchor: both need the same tip position, the same length, and the
    same drop clamp. ``tip_y`` is the FINAL drop coordinate; callers
    use it directly so the curtain start matches the branch end.

    The ``length`` and ``tip_y`` are clamped together so the tip plus
    the longest curtain strand (~100 px) never reaches below the pot
    rim. Without this guard, willow curtains hung 90 px below the
    soil line on every event count.
    """
    if not branch.segments:
        return None
    spine = _spine(state)
    n_trunk = max(1, len(state.trunk))
    t = min(0.92, max(0.20, branch.attach_point[1] / float(n_trunk)))
    ax, ay = _spine_point_at(spine, t)
    side = -1.0 if i % 2 == 0 else 1.0
    # Trimmed from the previous 100 + 16 * seg, max 220 -- shorter
    # branches keep tips closer to the trunk and the curtain hangs
    # in a tighter envelope.
    length = min(170.0, 90.0 + 11.0 * len(branch.segments))
    # Drop fraction at tip (was 0.45). 0.22 still reads as "droopy"
    # but stops the tip from arcing past the pot rim.
    tip_y = ay + length * 0.22
    # Hard ceiling: keep tip + curtain headroom above the pot rim so
    # nothing of the visible foliage ever lands inside the pot.
    # Curtain length is 50-100 px; reserve 110 below tip_y.
    max_tip_y = canvas.ORIGIN_Y - 110
    tip_y = min(tip_y, max_tip_y)
    return ax, ay, side, length, tip_y


def _drooping_branch(branch: Branch, state: TreeState, i: int) -> str:
    """Branch arcs horizontally then slightly DOWNWARD at the tip."""
    geom = _branch_geometry(branch, state, i)
    if geom is None:
        return ""
    ax, ay, side, length, tip_y = geom
    seed = max(1, branch.attach_point[1] * 23 + i + 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    near = (ax + side * 18.0, ay - 4.0)
    # Mid: roughly horizontal, midway in y between attach and tip.
    mid_y = ay + (tip_y - ay) * 0.45 + (h0 / 255.0 - 0.5) * 8.0
    mid = (ax + side * length * 0.55, mid_y)
    tip = (ax + side * length, tip_y)
    return canvas.tapered_ribbon_path(
        [(ax, ay), near, mid, tip],
        w_start=9.0, w_end=2.0,
        fill=tokens.BARK, taper_curve=1.4,
    )


def _branch_tip(branch: Branch, state: TreeState, i: int) -> tuple[float, float]:
    geom = _branch_geometry(branch, state, i)
    if geom is None:
        return _spine_point_at(_spine(state), 0.5)
    ax, _, side, length, tip_y = geom
    return ax + side * length, tip_y


def _branch_mid(
    branch: Branch, state: TreeState, i: int
) -> tuple[float, float] | None:
    """Midpoint of the branch ribbon -- used for the mid-branch
    foliage cluster that fills the otherwise-bare horizontal stretch.
    """
    geom = _branch_geometry(branch, state, i)
    if geom is None:
        return None
    ax, ay, side, length, tip_y = geom
    return ax + side * length * 0.55, ay + (tip_y - ay) * 0.45


def _hanging_strand(
    cx: float, cy: float, *, length: float, seed: int, fill: str
) -> str:
    """A wispy strand of leaves hanging vertically from (cx, cy).

    Strand is a sequence of small narrow leaves descending at slight
    horizontal jitter, ending at length below the start.
    """
    parts: list[str] = []
    n = max(4, int(length / 14))
    for i in range(n):
        h0, h1, h2, _ = canvas.quad_lcg(seed * 50 + i + 1)
        t = i / max(1, n - 1)
        x = cx + (h0 / 255.0 - 0.5) * 8
        y = cy + t * length
        # Each leaf points downward, narrow.
        ang = -85.0 + (h1 / 255.0 - 0.5) * 30.0
        scale = 0.55 + (h2 / 255.0) * 0.4
        parts.append(
            f'<g transform="translate({x:.1f},{y:.1f}) rotate({ang:.1f})">'
            f'<ellipse cx="0" cy="-6" rx="{2.5 * scale:.1f}" '
            f'ry="{8 * scale:.1f}" fill="{fill}" />'
            f"</g>"
        )
    return "".join(parts)


def _willow_curtain(
    cx: float, cy: float, *, seed: int, density: int
) -> str:
    """A curtain of 3-5 hanging strands from a branch tip.

    Strand length is capped so a curtain anchored at the
    geometry-clamped tip never reaches below the pot rim. The
    overall y budget: pot rim at 600 - 110 (geometry clamp) = 490
    max tip_y; curtain length up to ~95 lands at y <= 585, leaving
    15 px breathing room above the rim.
    """
    parts: list[str] = []
    for i in range(max(3, density)):
        h0, h1, _, _ = canvas.quad_lcg(seed * 17 + i + 1)
        x = cx + (h0 / 255.0 - 0.5) * 30
        # Was 50..100; now 40..95. Combined with tip_y clamp this
        # keeps every strand strictly above ORIGIN_Y - 8.
        length = 40.0 + (h1 / 255.0) * 55.0
        # Hard floor: curtain end must stay above the pot rim.
        max_length = max(20.0, (canvas.ORIGIN_Y - 8) - cy)
        length = min(length, max_length)
        parts.append(
            _hanging_strand(x, cy, length=length, seed=seed * 53 + i, fill=tokens.WILLOW_LEAF)
        )
    return "".join(parts)


def _branches_with_curtains(state: TreeState) -> str:
    out: list[str] = []
    ab = canvas.abundance(state.event_count)
    for i, branch in enumerate(state.branches):
        if not branch.segments:
            continue
        out.append(_drooping_branch(branch, state, i))
        # Mid-branch foliage cluster so the horizontal stretch of the
        # ribbon isn't a bare stick. Without this, all visible foliage
        # was at the tip and the long horizontal branch looked like
        # an exposed limb. Cluster count/radius scale with abundance.
        mid = _branch_mid(branch, state, i)
        seed = (branch.attach_point[1] + 1) * 41 + i
        if mid is not None:
            mx, my = mid
            out.append(
                canvas.leaf_cluster(
                    mx, my - 2,
                    seed=seed * 7 + 1,
                    base_fill=tokens.WILLOW_LEAF,
                    highlight_fill=canvas.shade(tokens.WILLOW_LEAF, 0.18),
                    count=int(5 * ab),
                    radius=10 * ab,
                    leaf_rx=3.5, leaf_ry=7.5,
                )
            )
        tip_x, tip_y = _branch_tip(branch, state, i)
        # Cluster of small leaves at the branch tip itself.
        out.append(
            canvas.leaf_cluster(
                tip_x, tip_y - 4,
                seed=seed,
                base_fill=tokens.WILLOW_LEAF,
                highlight_fill=canvas.shade(tokens.WILLOW_LEAF, 0.15),
                count=int(8 * ab),
                radius=14 * ab,
                leaf_rx=4, leaf_ry=9,
            )
        )
        # Curtain hanging below the tip. _branch_geometry already
        # clamped tip_y so curtain length <= 100 still lands above
        # the pot rim.
        out.append(
            _willow_curtain(
                tip_x, tip_y + 4,
                seed=seed * 3,
                density=3 + (len(branch.leaves) % 3),
            )
        )
    return "".join(out)


def _apex(state: TreeState) -> str:
    """Willow doesn't have a 'crown' -- the apex looks like another
    drooping tip. Render a small cluster + a curtain."""
    spine = _spine(state)
    apex_x, apex_y = spine[-1]
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    ab = canvas.abundance(state.event_count)
    level = canvas.density_level(state.event_count)
    return (
        canvas.leaf_cluster(
            apex_x, apex_y - 6,
            seed=seed + 11,
            base_fill=tokens.WILLOW_LEAF,
            highlight_fill=canvas.shade(tokens.WILLOW_LEAF, 0.18),
            count=int(10 * ab),
            radius=18 * ab,
            leaf_rx=5, leaf_ry=10,
        )
        + _willow_curtain(apex_x, apex_y + 6, seed=seed + 31, density=3 + level)
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
        tx = ax + side * 45
        ty = ay + 18  # offshoots droop too
        out.append(
            f'<path d="M{ax:.1f},{ay:.1f} Q{ax + side * 18:.1f},{ay + 4:.1f} '
            f'{tx:.1f},{ty:.1f}" stroke="{tokens.BARK}" stroke-width="2" '
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
        length = 26 + i * 6
        x0 = canvas.ORIGIN_X + side * 8
        x1 = canvas.ORIGIN_X + side * length
        out.append(
            f'<path d="M{x0:.1f},{base:.1f} Q{x0 + side * 12:.1f},{base + 6:.1f} '
            f'{x1:.1f},{base + 2:.1f}" stroke="{tokens.BARK_DEEP}" '
            f'stroke-width="3" fill="none" stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(out)


def render_willow(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    _ = ctx
    return (
        _trunk_path(state)
        + _bark(state)
        + _branches_with_curtains(state)
        + _apex(state)
        + _offshoots(state)
        + _roots(state)
    )

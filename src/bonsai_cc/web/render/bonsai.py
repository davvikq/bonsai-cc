"""``render_generic_bonsai`` -- the default theme (asymmetric S-curve)."""

from __future__ import annotations

from bonsai_cc.growth.state import Branch, TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_generic_bonsai"]


# Trunk envelope: how tall a "full grown" trunk is on the canvas.
_TRUNK_MAX_PX: float = float(canvas.TREE_BASE_Y - canvas.TREE_TOP_Y)  # 200
_TRUNK_MIN_PX: float = 80.0


def _trunk_top_y(state: TreeState) -> float:
    """How tall the trunk should be drawn, in SVG pixels above the base.

    Bonsai trunks have visible *base*: even an empty session sits in
    a pot and shows a stub. The trunk grows monotonically with the
    number of trunk segments, clamped between MIN and MAX.
    """
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 8.0)  # ~8 trunk segments reads as mature
    height_px = _TRUNK_MIN_PX + (_TRUNK_MAX_PX - _TRUNK_MIN_PX) * growth
    return canvas.TREE_BASE_Y - height_px


def _trunk_path(state: TreeState) -> str:
    """Three-control-point Bezier with asymmetric S-curve.

    The trunk leans gently right then corrects to vertical near the
    top. Deterministic per-session lean via the seed so the same
    session always re-renders identically (and so different sessions
    don't all look the same).
    """
    top_y = _trunk_top_y(state)
    base_x = canvas.ORIGIN_X
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    lean = ((h0 / 255.0) - 0.5) * 60.0  # ±30px lean at midpoint
    mid_x = base_x + lean
    mid_y = (top_y + canvas.TREE_BASE_Y) / 2
    # Curve back near the top so the apex sits roughly above the base.
    apex_x = base_x + lean * 0.25
    apex_y = top_y
    # Stack three sample points along the spine; tapered_ribbon_path
    # builds the Bezier between them. The extra anchor (1/4 height
    # from the base) keeps the bottom near-vertical.
    quarter_y = canvas.TREE_BASE_Y - (canvas.TREE_BASE_Y - top_y) * 0.30
    quarter_x = base_x + lean * 0.20
    spine = [
        (float(base_x), float(canvas.TREE_BASE_Y)),
        (quarter_x, quarter_y),
        (mid_x, mid_y),
        (apex_x, apex_y),
    ]
    return canvas.tapered_ribbon_path(
        spine,
        w_start=36.0,
        w_end=8.0,
        fill=tokens.BARK,
        taper_curve=1.7,
    )


def _trunk_spine_points(state: TreeState) -> list[tuple[float, float]]:
    """Same spine ``_trunk_path`` builds -- exported so bark helpers
    can hug the same curve."""
    top_y = _trunk_top_y(state)
    base_x = canvas.ORIGIN_X
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    lean = ((h0 / 255.0) - 0.5) * 60.0
    mid_x = base_x + lean
    mid_y = (top_y + canvas.TREE_BASE_Y) / 2
    apex_x = base_x + lean * 0.25
    apex_y = top_y
    quarter_y = canvas.TREE_BASE_Y - (canvas.TREE_BASE_Y - top_y) * 0.30
    quarter_x = base_x + lean * 0.20
    return [
        (float(base_x), float(canvas.TREE_BASE_Y)),
        (quarter_x, quarter_y),
        (mid_x, mid_y),
        (apex_x, apex_y),
    ]


def _trunk_bark_marks(state: TreeState) -> str:
    """Vertical bark striations + left-side inner shadow.

    Real bark grain runs vertically (parallel to the trunk axis).
    Horizontal ribs would read as cross-section diagrams; instead
    short hatch marks rotate to lie along the local trunk tangent.
    The inner shadow gives depth on the unlit side.
    """
    spine = _trunk_spine_points(state)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    # Which side is lit depends on the trunk lean -- the convex side
    # catches the light, the concave side stays shadowed.
    lean = spine[2][0] - spine[0][0]
    lit_side = 1.0 if lean >= 0 else -1.0
    striations_lit = canvas.vertical_bark_striations(
        spine, seed=seed * 3, side=lit_side, count=5,
        color=tokens.BARK_DEEP, opacity=0.45,
    )
    striations_shadow = canvas.vertical_bark_striations(
        spine, seed=seed * 5, side=-lit_side, count=3,
        color=tokens.BARK_DEEP, opacity=0.30,
    )
    inner_shadow = canvas.bark_inner_shadow(
        spine, shadow_side=-lit_side,
        color=tokens.BARK_DEEP, width=4.0, opacity=0.32,
    )
    return inner_shadow + striations_shadow + striations_lit


def _branch_attach_point_px(
    branch: Branch, state: TreeState
) -> tuple[float, float]:
    """Where on the trunk this branch emerges, in SVG coords.

    State's ``branch.attach_point`` is a logical (x, y); we ignore
    the logical x (which is always near 0) and use the y to find a
    point along the rendered trunk Bezier. For brevity we
    interpolate linearly along the trunk spine from base to apex --
    good enough at branch density.
    """
    top_y = _trunk_top_y(state)
    n_trunk = max(1, len(state.trunk))
    attach_y_logical = branch.attach_point[1]
    frac = min(1.0, max(0.0, attach_y_logical / float(n_trunk)))
    # Match the lean from _trunk_path so branches don't visually
    # detach from the trunk on its leaning side.
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    lean = ((h0 / 255.0) - 0.5) * 60.0
    base_x = canvas.ORIGIN_X
    spine_x = base_x + lean * frac
    spine_y = canvas.TREE_BASE_Y - (canvas.TREE_BASE_Y - top_y) * frac
    return spine_x, spine_y


def _branch_path(branch: Branch, state: TreeState, branch_idx: int) -> str:
    """One branch, Bezier-curved, horizontal-bias with upward arc at tip.

    Direction: alternates left / right based on branch index so the
    silhouette stays roughly balanced. Length grows with segment
    count. Tip arcs upward in the last 25% of the path.
    """
    if not branch.segments:
        return ""
    ax, ay = _branch_attach_point_px(branch, state)
    side = -1.0 if (branch_idx % 2 == 0) else 1.0
    # Length scales with segment count, capped so branches don't
    # overshoot the canvas.
    length_px = min(220.0, 90.0 + 18.0 * len(branch.segments))
    # Mid point: roughly horizontal from attach.
    seed = max(1, branch.attach_point[1] * 17 + branch_idx + 1)
    h0, h1, _, _ = canvas.quad_lcg(seed)
    vertical_jitter = (h0 / 255.0 - 0.5) * 14.0
    mid_x = ax + side * length_px * 0.55
    mid_y = ay + vertical_jitter
    # Tip: same horizontal distance again, but lifted upward.
    tip_x = ax + side * length_px
    tip_y = ay - length_px * 0.30 + (h1 / 255.0 - 0.5) * 12.0
    # Anchor near the attach so the branch emerges with a touch of
    # vertical curve before going horizontal.
    near_x = ax + side * 18.0
    near_y = ay + 6.0
    points = [(ax, ay), (near_x, near_y), (mid_x, mid_y), (tip_x, tip_y)]
    return canvas.tapered_ribbon_path(
        points,
        w_start=11.0,
        w_end=2.5,
        fill=tokens.BARK,
        taper_curve=1.4,
    )


def _branch_tip_px(
    branch: Branch, state: TreeState, branch_idx: int
) -> tuple[float, float]:
    """Where to put the foliage cluster for this branch."""
    if not branch.segments:
        return _branch_attach_point_px(branch, state)
    ax, ay = _branch_attach_point_px(branch, state)
    side = -1.0 if (branch_idx % 2 == 0) else 1.0
    length_px = min(220.0, 90.0 + 18.0 * len(branch.segments))
    return ax + side * length_px, ay - length_px * 0.30


def _branches_with_foliage(state: TreeState) -> str:
    """All branches and their tip-clusters, plus secondary leaf
    clusters along each branch."""
    out: list[str] = []
    ab = canvas.abundance(state.event_count)
    for i, branch in enumerate(state.branches):
        out.append(_branch_path(branch, state, i))
        # Tip cluster per branch -- count and radius scale with
        # session abundance so a 200-event session reads visibly
        # fuller than a 20-event session.
        tip_x, tip_y = _branch_tip_px(branch, state, i)
        seed = (branch.attach_point[1] + 1) * 91 + i
        out.append(
            canvas.leaf_cluster(
                tip_x,
                tip_y,
                seed=seed,
                base_fill=tokens.MOSS,
                highlight_fill=tokens.LEAF_HIGHLIGHT,
                count=int(11 * ab),
                radius=24 * ab,
                leaf_rx=8,
                leaf_ry=12,
            )
        )
        # Per-leaf-event clusters get a smaller satellite cluster
        # along the branch, so an Edit-heavy session still reads
        # as bushy without doubling tip foliage.
        for li, leaf in enumerate(branch.leaves):
            sx = tip_x - (-1.0 if i % 2 == 0 else 1.0) * 30 * (1 + li * 0.6)
            sy = tip_y + 20 + li * 4
            # Honor wilt: PostToolUseFailure paints leaves a
            # specific yellow; the cluster collapses to a smaller
            # single-tone cluster in that color so the wilt reads
            # visually distinct from healthy foliage.
            if leaf.color is not None:
                base = leaf.color
                highlight = canvas.shade(leaf.color, 0.18)
                count = 4
                radius = 10.0
            else:
                base = tokens.MOSS
                highlight = tokens.LEAF_HIGHLIGHT
                count = 7
                radius = 14.0
            out.append(
                canvas.leaf_cluster(
                    sx,
                    sy,
                    seed=leaf.birth_event_idx * 13 + li + 1,
                    base_fill=base,
                    highlight_fill=highlight,
                    count=count,
                    radius=radius,
                    leaf_rx=6,
                    leaf_ry=9,
                )
            )
    return "".join(out)


def _apex_cluster(state: TreeState) -> str:
    """The largest cluster, sitting on the trunk top so the apex is
    never a sharp point. Always present, even at zero events.

    Flower events (WebFetch / WebSearch) puff out the apex slightly
    instead of getting their own glyph -- keeps the silhouette clean
    while still preserving the "session reached out to the web"
    information.
    """
    top_y = _trunk_top_y(state)
    cx = canvas.ORIGIN_X
    # Same lean as trunk.
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    lean = ((h0 / 255.0) - 0.5) * 60.0
    apex_x = cx + lean * 0.25
    apex_y = top_y - 12
    flower_bonus = min(4, len(state.flowers))  # +0..+4 leaves
    ab = canvas.abundance(state.event_count)
    return canvas.leaf_cluster(
        apex_x,
        apex_y,
        seed=seed + 7,
        base_fill=tokens.MOSS,
        highlight_fill=tokens.LEAF_HIGHLIGHT,
        count=int((13 + flower_bonus) * ab),
        radius=(32 + flower_bonus * 2) * ab,
        leaf_rx=9,
        leaf_ry=13,
    )


def _flowers(state: TreeState) -> str:
    """Web fetch / web search events.

    The generic bonsai doesn't render explicit flower glyphs -- a
    five-petal mark reads as clip-art at this scale. Flower
    information is absorbed into the foliage by enlarging the apex
    cluster slightly when flowers are present (handled in
    ``_apex_cluster`` via state.flowers count).
    """
    _ = state
    return ""


def _offshoots(state: TreeState) -> str:
    """Sub-agent offshoots: small thin curve from the trunk with a
    Crail "berry" at the tip."""
    if not state.offshoots:
        return ""
    out: list[str] = []
    for i, off in enumerate(state.offshoots):
        if not off.segments:
            continue
        ax, ay = canvas.project_xy(
            off.attach_point[0], off.attach_point[1]
        )
        side = -1.0 if i % 2 == 0 else 1.0
        tx = ax + side * 60.0
        ty = ay + 4.0
        out.append(
            f'<path d="M{ax:.1f},{ay:.1f} Q{ax + side * 25:.1f},{ay - 6:.1f} '
            f'{tx:.1f},{ty:.1f}" stroke="{tokens.BARK}" stroke-width="2.4" '
            f'fill="none" stroke-linecap="round" />'
        )
        out.append(
            f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="5" fill="{tokens.CRAIL}" />'
        )
    return "".join(out)


def _roots(state: TreeState) -> str:
    """A short curved root pair at the soil line per Root event.

    Bonsai roots ("nebari") spread visibly at the base -- we hint at
    that without dominating the silhouette.
    """
    if not state.roots:
        return ""
    base = canvas.TREE_BASE_Y - 2  # just inside the soil line
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


def render_generic_bonsai(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    """Build the SVG body for a generic bonsai.

    Returns the *body* -- the dispatcher wraps it with <svg>, defs,
    sky, ground, pot. Order inside the body matters for layering:
    trunk → bark texture → branches → apex cluster → flowers →
    offshoots → roots. Roots come last so they sit on top of the
    soil ellipse but below the trunk visually (we adjust the y so
    they sit at the rim).
    """
    _ = ctx  # currently unused (sky/pot done outside) -- kept for shape consistency.
    return (
        _trunk_path(state)
        + _trunk_bark_marks(state)
        + _branches_with_foliage(state)
        + _apex_cluster(state)
        + _flowers(state)
        + _offshoots(state)
        + _roots(state)
    )

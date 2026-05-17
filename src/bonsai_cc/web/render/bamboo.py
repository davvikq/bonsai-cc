"""``render_bamboo`` -- the Python theme.

This is NOT a tree. Bamboo bonsai are multiple thin vertical stalks
of varying heights, clustered tight at the base and fanning slightly
outward at the top. Each stalk is segmented every ~40 px with a thin
horizontal line and a touch of darker shading at each node --
that's the bamboo signature.

Mapping from ``TreeState``:

* Trunk → 4-5 bamboo stalks. The taller the trunk in state, the
  taller the tallest stalk; shorter stalks scale proportionally.
* Each ``Branch`` in state attaches near the top of one stalk as a
  short horizontal twig.
* Foliage: narrow elongated leaves (3:1 aspect ratio) in drooping
  fans of 5-8 per twig. NOT round-ish leaf clusters.
"""

from __future__ import annotations

import math

from bonsai_cc.growth.state import TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_bamboo"]


_STALK_COUNT: int = 4
_STALK_MAX_PX: float = float(canvas.TREE_BASE_Y - canvas.TREE_TOP_Y)  # 200
_STALK_MIN_PX: float = 100.0
_NODE_SPACING_PX: float = 38.0


def _stalk_heights(state: TreeState) -> list[float]:
    """Height (in pixels above TREE_BASE_Y) of each bamboo stalk.

    The tallest stalk grows with state.trunk segment count; the
    others stagger downward so the silhouette has a natural fan
    shape (tallest in the middle-rear, shorter at the sides).
    """
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 8.0)
    tallest = _STALK_MIN_PX + (_STALK_MAX_PX - _STALK_MIN_PX) * growth
    # Stagger: pattern roughly (0.85, 1.00, 0.92, 0.75) -- middle stalk
    # tallest, sides shorter; bamboo bonsai compositional norm.
    pattern = [0.85, 1.0, 0.92, 0.75]
    return [tallest * pattern[i] for i in range(_STALK_COUNT)]


def _stalk_positions(state: TreeState) -> list[tuple[float, float]]:
    """X position at the base + X displacement at the top per stalk.

    Stalks are tight at the base (the rim of the pot) and fan
    outward at the top. Deterministic per-session via seed so the
    same session keeps the same arrangement.
    """
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    base_spread = 28.0   # base offsets within ±28px
    top_spread = 90.0    # top offsets within ±90px
    cx = float(canvas.ORIGIN_X)
    positions: list[tuple[float, float]] = []
    for i in range(_STALK_COUNT):
        h0, h1, _, _ = canvas.quad_lcg(seed * 9 + i + 1)
        # Slot each stalk to a horizontal band so they don't overlap.
        slot = -1 + (2 * i) / max(1, _STALK_COUNT - 1)  # -1..+1
        bx = cx + slot * base_spread + (h0 / 255.0 - 0.5) * 6
        tx = cx + slot * top_spread + (h1 / 255.0 - 0.5) * 18
        positions.append((bx, tx))
    return positions


def _stalk_color(i: int, total: int) -> tuple[str, str]:
    """Body / node color per stalk. Tallest stalk gets a touch of
    BAMBOO_YOUNG highlight (younger stalks read brighter)."""
    # The "middle/tallest" stalk leans young/bright; others stay MOSS.
    if i == 1:
        body = canvas.shade(tokens.MOSS, 0.08)
        node = canvas.shade(tokens.MOSS, -0.30)
    elif i == 2:
        body = tokens.MOSS
        node = canvas.shade(tokens.MOSS, -0.35)
    elif i == 0:
        body = canvas.shade(tokens.MOSS, -0.05)
        node = canvas.shade(tokens.MOSS, -0.40)
    else:
        body = canvas.shade(tokens.MOSS, -0.10)
        node = canvas.shade(tokens.MOSS, -0.40)
    _ = total
    # The youngest stalk gets a touch of BAMBOO_YOUNG at the top
    # in render_stalk via gradient overlay if we want -- keep this
    # function simple for now and let the gradient handle young/old.
    return body, node


def _render_stalk(
    base_x: float,
    top_x: float,
    height_px: float,
    *,
    body: str,
    node: str,
    is_young: bool,
    seed: int,
    w_start: float = 14.0,
    w_end: float = 7.0,
) -> str:
    """One bamboo stalk: tapered ribbon with horizontal node lines.

    The stalk is Bezier-smoothed (very slight S-curve, mainly
    vertical) and gets a node line every ~38 px.
    """
    base_y = float(canvas.TREE_BASE_Y)
    top_y = base_y - height_px
    h0, h1, _, _ = canvas.quad_lcg(seed)
    # Slight S-curve: one inflection between base and top.
    mid_y = (base_y + top_y) / 2
    sway_mid = (h0 / 255.0 - 0.5) * 12.0  # small mid-stalk lateral
    mid_x = base_x + (top_x - base_x) * 0.5 + sway_mid
    near_base_y = base_y - height_px * 0.18
    near_base_x = base_x + (top_x - base_x) * 0.10
    spine = [
        (base_x, base_y),
        (near_base_x, near_base_y),
        (mid_x, mid_y),
        (top_x, top_y),
    ]
    stalk = canvas.tapered_ribbon_path(
        spine,
        w_start=w_start,
        w_end=w_end,
        fill=tokens.BAMBOO_YOUNG if is_young else body,
        taper_curve=1.0,
    )
    # Optional darker outline on the shaded side -- subtle.
    shade_side = "M" + ",".join(
        f"{p[0] - 2:.1f},{p[1]:.1f}" for p in spine
    )
    shade_line = (
        f'<path d="{shade_side}" stroke="{canvas.shade(body, -0.45)}" '
        f'stroke-width="1.5" fill="none" opacity="0.30" '
        f'stroke-linecap="round" />'
    )
    # Node lines every ~38px along the stalk.
    nodes: list[str] = []
    n_nodes = max(1, int(height_px // _NODE_SPACING_PX))
    for k in range(1, n_nodes + 1):
        t = k / float(n_nodes + 1)
        # Linear lerp along the spine is adequate at this density.
        ny = base_y - height_px * t
        nx = base_x + (top_x - base_x) * t
        # Half-width at this height -- taper-linear approximation
        # tracking the (possibly overridden) w_start/w_end.
        half_w = (w_start + (w_end - w_start) * t) / 2 + 1.5
        nodes.append(
            f'<line x1="{nx - half_w:.1f}" y1="{ny:.1f}" '
            f'x2="{nx + half_w:.1f}" y2="{ny:.1f}" '
            f'stroke="{node}" stroke-width="2.2" stroke-linecap="round" />'
        )
        # Small darker dot at the join -- the "bud" at each node.
        nodes.append(
            f'<circle cx="{nx + half_w * 0.4:.1f}" cy="{ny:.1f}" r="2" '
            f'fill="{node}" opacity="0.85" />'
        )
    _ = h1
    return stalk + shade_line + "".join(nodes)


def _bamboo_leaf(
    cx: float, cy: float, *, scale: float, angle_deg: float, fill: str
) -> str:
    """One narrow 3:1 bamboo leaf -- elongated ellipse with pointed tips.

    Drawn as a long thin teardrop using two Bezier control points
    so the tips are pointed, not rounded.
    """
    length = 22.0 * scale
    width = 6.0 * scale
    # Local-axis path: from (0, 0) down to (0, -length), passing
    # through (±width/2, -length/2) for the side bulge.
    path_d = (
        f"M0,0 "
        f"Q{width / 2:.1f},{-length / 2:.1f} 0,{-length:.1f} "
        f"Q{-width / 2:.1f},{-length / 2:.1f} 0,0 Z"
    )
    return (
        f'<g transform="translate({cx:.1f},{cy:.1f}) rotate({angle_deg:.1f})">'
        f'<path d="{path_d}" fill="{fill}" />'
        f"</g>"
    )


def _leaf_fan(
    cx: float, cy: float, *, seed: int, count: int, droop_dir: float, fill: str
) -> str:
    """A drooping fan of ``count`` bamboo leaves emerging from (cx, cy).

    ``droop_dir`` = -1 fans left, +1 fans right. Leaves point
    outward and downward (the bamboo signature droop).
    """
    parts: list[str] = []
    # Spread angles roughly from -20° to +90° (downward-leaning fan).
    base_angle = -20.0 if droop_dir > 0 else 200.0  # 200 = mirror
    span = 110.0
    for i in range(count):
        h0, h1, h2, _ = canvas.quad_lcg(seed * 100 + i)
        t = i / max(1, count - 1)
        angle = base_angle + droop_dir * span * t + (h0 / 255.0 - 0.5) * 12
        scale = 0.85 + (h1 / 255.0) * 0.5
        # Slight stem offset along the fan direction.
        rad = math.radians(angle)
        dx = math.cos(rad) * 4
        dy = math.sin(rad) * 4
        local_fill = canvas.shade(fill, (h2 / 255.0 - 0.5) * 0.18)
        parts.append(
            _bamboo_leaf(cx + dx, cy + dy, scale=scale, angle_deg=angle, fill=local_fill)
        )
    return "<g>" + "".join(parts) + "</g>"


def _twigs_and_leaves(state: TreeState) -> str:
    """Each branch in state becomes a short horizontal twig near the
    top of one stalk, with a leaf fan on its tip."""
    positions = _stalk_positions(state)
    heights = _stalk_heights(state)
    out: list[str] = []
    if not state.branches:
        # Even an empty session shows a touch of canopy on the
        # tallest stalk -- bamboo without leaves reads as dead.
        _bx, tx = positions[1]
        ty = canvas.TREE_BASE_Y - heights[1]
        out.append(
            _leaf_fan(tx + 6, ty + 12, seed=11, count=5, droop_dir=1.0, fill=tokens.MOSS)
        )
        out.append(
            _leaf_fan(tx - 6, ty + 12, seed=23, count=5, droop_dir=-1.0, fill=tokens.MOSS)
        )
        return "".join(out)

    for i, branch in enumerate(state.branches):
        stalk_i = i % _STALK_COUNT
        _bx, tx = positions[stalk_i]
        ty = canvas.TREE_BASE_Y - heights[stalk_i]
        side = -1.0 if i % 2 == 0 else 1.0
        twig_len = 30.0 + min(40.0, len(branch.segments) * 4.0)
        # Twigs emerge near the top of the stalk; offset downward a
        # touch so they don't sit on top of the apex node.
        ty_emerge = ty + 18 + (i // _STALK_COUNT) * 22
        twig_tip_x = tx + side * twig_len
        twig_tip_y = ty_emerge + 6  # very slight downward
        out.append(
            f'<path d="M{tx:.1f},{ty_emerge:.1f} '
            f'Q{tx + side * twig_len * 0.5:.1f},{ty_emerge + 2:.1f} '
            f'{twig_tip_x:.1f},{twig_tip_y:.1f}" '
            f'stroke="{tokens.MOSS}" stroke-width="2.4" fill="none" '
            f'stroke-linecap="round" />'
        )
        leaf_seed = (branch.attach_point[1] + 1) * 37 + i + 1
        out.append(
            _leaf_fan(
                twig_tip_x,
                twig_tip_y,
                seed=leaf_seed,
                count=6 + (len(branch.leaves) % 3),
                droop_dir=side,
                fill=tokens.MOSS,
            )
        )
        # Per-leaf-event mini fan along the twig for density.
        for li, _leaf in enumerate(branch.leaves):
            sx = tx + side * (twig_len * 0.55) + side * li * 6
            sy = ty_emerge + 4
            out.append(
                _leaf_fan(
                    sx,
                    sy,
                    seed=leaf_seed * 5 + li,
                    count=4,
                    droop_dir=side,
                    fill=tokens.MOSS,
                )
            )
    return "".join(out)


def _apex_leaves(state: TreeState) -> str:
    """A small fan at the top of each stalk -- bamboo always has a
    leafy crown."""
    positions = _stalk_positions(state)
    heights = _stalk_heights(state)
    out: list[str] = []
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    for i in range(_STALK_COUNT):
        _bx, tx = positions[i]
        ty = canvas.TREE_BASE_Y - heights[i]
        out.append(
            _leaf_fan(
                tx + 4,
                ty + 6,
                seed=seed * 11 + i * 5,
                count=5,
                droop_dir=1.0,
                fill=tokens.BAMBOO_YOUNG if i == 1 else tokens.MOSS,
            )
        )
        out.append(
            _leaf_fan(
                tx - 4,
                ty + 6,
                seed=seed * 13 + i * 5,
                count=5,
                droop_dir=-1.0,
                fill=tokens.BAMBOO_YOUNG if i == 1 else tokens.MOSS,
            )
        )
    return "".join(out)


def _flowers(state: TreeState) -> str:
    """WebFetch flowers -- Crail accent at canopy height."""
    out: list[str] = []
    for f in state.flowers:
        x, y = canvas.project_xy(f.x, f.y)
        y = max(y, canvas.TREE_TOP_Y + 6)
        # Small five-petal blossom -- bamboo flowers are rare and tiny.
        for i in range(5):
            angle = i * 72
            out.append(
                f'<g transform="translate({x:.1f},{y:.1f}) rotate({angle})">'
                f'<ellipse cx="0" cy="-4" rx="2" ry="3.6" fill="{tokens.CRAIL}" />'
                f"</g>"
            )
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="{tokens.CRAIL_DEEP}" />'
        )
    return "".join(out)


def _offshoots(state: TreeState) -> str:
    """Sub-agent offshoots -- short bamboo offshoots near the base."""
    if not state.offshoots:
        return ""
    positions = _stalk_positions(state)
    out: list[str] = []
    for i, _off in enumerate(state.offshoots):
        stalk_i = i % _STALK_COUNT
        bx, _tx = positions[stalk_i]
        side = -1.0 if i % 2 == 0 else 1.0
        tip_x = bx + side * 40
        tip_y = canvas.TREE_BASE_Y - 60
        out.append(
            f'<path d="M{bx:.1f},{canvas.TREE_BASE_Y - 10:.1f} '
            f'Q{bx + side * 18:.1f},{canvas.TREE_BASE_Y - 36:.1f} '
            f'{tip_x:.1f},{tip_y:.1f}" '
            f'stroke="{tokens.MOSS}" stroke-width="3" fill="none" '
            f'stroke-linecap="round" />'
        )
        out.append(
            _leaf_fan(tip_x, tip_y, seed=i + 7, count=4, droop_dir=side, fill=tokens.MOSS)
        )
    return "".join(out)


def _roots(state: TreeState) -> str:
    """Bamboo doesn't show roots typically; we add small soil-line
    rhizome bumps for Bash-heavy sessions so the event registers."""
    if not state.roots:
        return ""
    base = canvas.TREE_BASE_Y - 2
    out: list[str] = []
    for i, _r in enumerate(state.roots):
        side = -1.0 if i % 2 == 0 else 1.0
        x = canvas.ORIGIN_X + side * (24 + i * 10)
        out.append(
            f'<ellipse cx="{x:.1f}" cy="{base + 4:.1f}" rx="11" ry="3.5" '
            f'fill="{tokens.MOSS}" opacity="0.7" />'
        )
    return "".join(out)


def _extra_stalk_positions(state: TreeState, n: int) -> list[tuple[float, float]]:
    """Base / top X for ``n`` thin "young" stalks beyond the primary fan.

    Stationed wider than the regular ``_stalk_positions`` so they
    expand the silhouette rather than crowding the central cluster.
    Deterministic per session seed.
    """
    if n <= 0:
        return []
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    cx = float(canvas.ORIGIN_X)
    out: list[tuple[float, float]] = []
    for i in range(n):
        h0, h1, _, _ = canvas.quad_lcg(seed * 29 + i + 1)
        side = -1.0 if i % 2 == 0 else 1.0
        depth = i // 2  # 0,0,1,1 for n=4 → outer pair stands further out
        base_off = 48 + depth * 14
        top_off = 118 + depth * 22
        bx = cx + side * base_off + (h0 / 255.0 - 0.5) * 4
        tx = cx + side * top_off + (h1 / 255.0 - 0.5) * 12
        out.append((bx, tx))
    return out


def _extra_stalk_heights(n: int) -> list[float]:
    """Younger stalks are shorter: 55-80 % of the primary minimum.

    Pure pattern, doesn't read state -- the silhouette goal is "a
    few thin upstart canes leaning out of the cluster," and the
    height distribution is purely compositional.
    """
    if n <= 0:
        return []
    out: list[float] = []
    for i in range(n):
        scale = 0.55 + (i / max(1, n - 1)) * 0.25 if n > 1 else 0.55
        out.append(_STALK_MIN_PX * scale)
    return out


def _extra_apex_leaves(
    positions: list[tuple[float, float]],
    heights: list[float],
    state: TreeState,
) -> str:
    """Small BAMBOO_YOUNG fan on each extra stalk so it isn't stark.

    Half the leaves of the primary apex fan -- these are "young"
    canes whose crowns shouldn't compete with the established
    stalks.
    """
    if not positions:
        return ""
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    out: list[str] = []
    for i, ((_bx, tx), h) in enumerate(zip(positions, heights, strict=False)):
        ty = canvas.TREE_BASE_Y - h
        out.append(
            _leaf_fan(
                tx + 3, ty + 4, seed=seed * 41 + i, count=3,
                droop_dir=1.0, fill=tokens.BAMBOO_YOUNG,
            )
        )
        out.append(
            _leaf_fan(
                tx - 3, ty + 4, seed=seed * 43 + i, count=3,
                droop_dir=-1.0, fill=tokens.BAMBOO_YOUNG,
            )
        )
    return "".join(out)


def render_bamboo(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    """Build the SVG body for the bamboo theme."""
    _ = ctx
    heights = _stalk_heights(state)
    positions = _stalk_positions(state)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    stalks: list[str] = []
    # Render stalks back-to-front: tallest behind, others in front.
    # We approximate via Z-order = original index (middle tallest).
    render_order = [3, 0, 2, 1]  # back, side, side, front
    for i in render_order:
        body, node = _stalk_color(i, _STALK_COUNT)
        bx, tx = positions[i]
        stalks.append(
            _render_stalk(
                bx,
                tx,
                heights[i],
                body=body,
                node=node,
                is_young=(i == 1),
                seed=seed * 17 + i + 1,
            )
        )

    # Extra thin stalks at the cluster periphery as the session
    # gets long. Caps at 4 extras (8 total) via density_level so a
    # 500-event session doesn't slide off the viewbox.
    extra_n = canvas.density_level(state.event_count)
    extra_positions = _extra_stalk_positions(state, extra_n)
    extra_heights = _extra_stalk_heights(extra_n)
    extras: list[str] = []
    for i, ((bx, tx), h) in enumerate(
        zip(extra_positions, extra_heights, strict=False)
    ):
        extras.append(
            _render_stalk(
                bx, tx, h,
                body=tokens.BAMBOO_YOUNG,
                node=canvas.shade(tokens.BAMBOO_YOUNG, -0.30),
                is_young=True,
                seed=seed * 31 + i + 1,
                w_start=9.0,
                w_end=4.5,
            )
        )

    return (
        "".join(stalks)
        + "".join(extras)
        + _twigs_and_leaves(state)
        + _apex_leaves(state)
        + _extra_apex_leaves(extra_positions, extra_heights, state)
        + _flowers(state)
        + _offshoots(state)
        + _roots(state)
    )

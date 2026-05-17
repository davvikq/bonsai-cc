"""``render_pine`` -- the rust theme.

Pine bonsai are flat-topped with distinct horizontal "cloud" tiers
of needles. The trunk leans pronouncedly to one side then corrects,
and the bark has deep furrows on the shaded side. Branches are
hidden inside the needle clouds -- what reads as a branch is really
the leading edge of a needle tier.

Mapping from ``TreeState``:

* Trunk: single thick gnarled spine, 20° lean then correction.
* Each ``Branch`` → one horizontal foliage tier. Tier height
  follows attach_point's logical y; longer branches → wider tiers.
* Needles: fans of 8-12 short lines radiating outward, MOSS-darkened.
"""

from __future__ import annotations

from bonsai_cc.growth.state import Branch, TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_pine"]


_TRUNK_MAX_PX: float = float(canvas.TREE_BASE_Y - canvas.TREE_TOP_Y)
_TRUNK_MIN_PX: float = 110.0


def _trunk_top_y(state: TreeState) -> float:
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 7.0)
    height_px = _TRUNK_MIN_PX + (_TRUNK_MAX_PX - _TRUNK_MIN_PX) * growth
    return canvas.TREE_BASE_Y - height_px


def _spine(state: TreeState) -> list[tuple[float, float]]:
    """Pronounced asymmetric lean: trunk leans 20° one way at the
    lower third, then corrects through the upper section. Per-session
    seed picks which side."""
    top_y = _trunk_top_y(state)
    base_x = float(canvas.ORIGIN_X)
    base_y = float(canvas.TREE_BASE_Y)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, _, _, _ = canvas.quad_lcg(seed)
    side = 1.0 if (h0 & 1) else -1.0
    height = base_y - top_y
    # Strong lean at the lower third.
    low_x = base_x + side * 50.0
    low_y = base_y - height * 0.35
    # Correction near the top: returns toward vertical, slightly
    # past centre.
    upper_x = base_x - side * 12.0
    upper_y = base_y - height * 0.72
    apex_x = base_x - side * 4.0
    apex_y = top_y
    return [
        (base_x, base_y),
        (low_x, low_y),
        (upper_x, upper_y),
        (apex_x, apex_y),
    ]


def _trunk_path(state: TreeState) -> str:
    spine = _spine(state)
    return canvas.tapered_ribbon_path(
        spine,
        w_start=40.0,
        w_end=9.0,
        fill=tokens.BARK,
        taper_curve=1.5,
    )


def _bark_texture(state: TreeState) -> str:
    """Deep furrows on the shaded side, finer striations on the lit
    side -- pronounced bark texture is the pine signature."""
    spine = _spine(state)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    # Lean direction: lower section sways +side, then corrects to
    # -side. The shaded side is the concave side of the dominant
    # curve, which is opposite the low-section sway.
    sway = spine[1][0] - spine[0][0]
    shadow_side = -1.0 if sway > 0 else 1.0
    inner = canvas.bark_inner_shadow(
        spine, shadow_side=shadow_side,
        color=tokens.BARK_DEEP, width=5.0, opacity=0.45,
    )
    deep_furrows = canvas.vertical_bark_striations(
        spine, seed=seed * 7, side=shadow_side, count=7,
        color=tokens.BARK_DEEP, opacity=0.55,
    )
    light_striations = canvas.vertical_bark_striations(
        spine, seed=seed * 11, side=-shadow_side, count=4,
        color=tokens.BARK_DEEP, opacity=0.30,
    )
    return inner + deep_furrows + light_striations


def _spine_point_at(spine: list[tuple[float, float]], t: float) -> tuple[float, float]:
    """Linear interpolation along the spine, t in [0, 1]."""
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


def _needle_cluster(
    cx: float, cy: float, *, seed: int, count: int, length: float
) -> str:
    """A fan of short needle lines radiating outward from (cx, cy).

    Needles point downward-and-outward (pine sprays cascade), with
    jitter in length and angle. Two-tone for depth: half the needles
    use MOSS, half LEAF_HIGHLIGHT.
    """
    parts: list[str] = []
    for i in range(count):
        h0, h1, h2, _ = canvas.quad_lcg(seed * 50 + i + 1)
        # Angle 30°-150° (downward-leaning fan).
        angle_deg = 30.0 + (i / max(1, count - 1)) * 120.0 + (h0 / 255.0 - 0.5) * 12.0
        import math as _math
        rad = _math.radians(angle_deg)
        ln = length * (0.75 + (h1 / 255.0) * 0.5)
        ex = cx + _math.cos(rad) * ln
        ey = cy + _math.sin(rad) * ln
        color = tokens.MOSS if i % 2 == 0 else tokens.LEAF_HIGHLIGHT
        _ = h2
        parts.append(
            f'<line x1="{cx:.1f}" y1="{cy:.1f}" '
            f'x2="{ex:.1f}" y2="{ey:.1f}" '
            f'stroke="{color}" stroke-width="1.4" '
            f'stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(parts)


def _foliage_tier(
    cx: float, cy: float, *, half_width: float, seed: int, density: int
) -> str:
    """One pine tier -- a fused cloud silhouette with bumpy top.

    Drawn as a single ``<path>`` whose top edge bumps up between
    anchors and whose bottom edge is a soft curve. This reads as a
    real pine cloud-pad (one continuous body with visible lumps
    along the lit upper surface) instead of a row of separate
    circles. Needle sprays anchor along the bottom edge for the
    needle texture.
    """
    parts: list[str] = []
    n_bumps = max(4, min(8, density))
    step = (half_width * 2) / max(1, n_bumps)
    # Build bump anchor points (top edge of the cloud).
    # Each anchor sits above the baseline (cy) by a per-bump amount;
    # outer bumps are shorter so the silhouette tapers naturally.
    bumps: list[tuple[float, float]] = []
    for i in range(n_bumps + 1):
        h0, _, _, _ = canvas.quad_lcg(seed * 17 + i + 1)
        bx = cx - half_width + step * i
        # Edge falloff: 0 at centre, 1 at the tips.
        edge = abs(i - n_bumps / 2) / max(1, n_bumps / 2)
        bump_h = 18.0 * (1.0 - edge * 0.65) + (h0 / 255.0 - 0.5) * 4
        bumps.append((bx, cy - bump_h))
    # Path: walk along the top via Q-curves between bumps, then a
    # gentle curve along the bottom returning to the start.
    left_x = cx - half_width
    right_x = cx + half_width
    base_y = cy + 8  # bottom of the tier body
    d_parts: list[str] = [f"M{left_x:.1f},{base_y:.1f}"]
    # Up to the first bump anchor.
    d_parts.append(f"Q{left_x - 2:.1f},{cy - 4:.1f} {bumps[0][0]:.1f},{bumps[0][1]:.1f}")
    # Bezier between subsequent bump anchors using mid-points as control.
    for i in range(1, len(bumps)):
        x0, y0 = bumps[i - 1]
        x1, y1 = bumps[i]
        cx_mid = (x0 + x1) / 2
        cy_mid = min(y0, y1) - 6  # peak slightly above the lower anchor
        d_parts.append(f"Q{cx_mid:.1f},{cy_mid:.1f} {x1:.1f},{y1:.1f}")
    # Right edge down to baseline.
    d_parts.append(f"Q{right_x + 2:.1f},{cy - 4:.1f} {right_x:.1f},{base_y:.1f}")
    # Bottom edge: soft single curve back to start.
    d_parts.append(
        f"Q{cx:.1f},{base_y + half_width * 0.16:.1f} {left_x:.1f},{base_y:.1f}"
    )
    d_parts.append("Z")
    parts.append(
        f'<path d="{" ".join(d_parts)}" fill="{tokens.MOSS}" />'
    )
    # Light cap along the bumpy top, slightly inset so the dark MOSS
    # body is visible as an outline on every bump.
    cap_parts: list[str] = [f"M{left_x + 6:.1f},{cy + 2:.1f}"]
    for i in range(len(bumps)):
        bx, by = bumps[i]
        prev = bumps[i - 1] if i > 0 else (left_x + 6, cy + 2)
        cmid_x = (prev[0] + bx) / 2
        cmid_y = min(prev[1], by) - 4
        cap_parts.append(f"Q{cmid_x:.1f},{cmid_y:.1f} {bx:.1f},{by + 3:.1f}")
    cap_parts.append(f"L{right_x - 6:.1f},{cy + 2:.1f} Z")
    parts.append(
        f'<path d="{" ".join(cap_parts)}" fill="{tokens.LEAF_HIGHLIGHT}" '
        f'opacity="0.55" />'
    )
    # Needle sprays along the bottom edge -- evenly spaced so the
    # underside has texture.
    n_sprays = max(3, n_bumps)
    for i in range(n_sprays):
        t = (i + 0.5) / n_sprays
        h0, h1, _, _ = canvas.quad_lcg(seed * 31 + i + 1)
        nx = cx - half_width + t * 2 * half_width + (h0 / 255.0 - 0.5) * 6
        ny = base_y - 2 + (h1 / 255.0 - 0.5) * 4
        parts.append(
            _needle_cluster(nx, ny, seed=seed * 47 + i, count=7, length=12.0)
        )
    return "".join(parts)


def _branches_as_tiers(state: TreeState) -> str:
    """Each branch becomes a horizontal foliage tier.

    Tier positions follow the branch attach_point's logical y; tier
    width follows segment count. We sort branches by height (lower
    branches drawn first → back; higher tiers drawn on top so the
    layering reads as physically layered cloud canopies).
    """
    spine = _spine(state)
    out: list[str] = []
    if not state.branches:
        return ""

    # Sort branches by attach height descending so lower tiers paint
    # first (we want apex tier on top).
    indexed: list[tuple[int, Branch]] = list(enumerate(state.branches))
    indexed.sort(key=lambda kv: kv[1].attach_point[1])
    n_trunk = max(1, len(state.trunk))
    ab = canvas.abundance(state.event_count)
    for draw_i, (orig_i, branch) in enumerate(indexed):
        if not branch.segments:
            continue
        # Spine parameter for this branch (0 = base, 1 = apex).
        # ``t >= 0.30`` keeps the lowest tier well above the soil so
        # the wide foliage ellipse can't sit inside the pot. Without
        # this clamp, branches attached at trunk segment 1 (t=1/14)
        # produced a tier centered just above the rim with ry=51,
        # extending ~50px INSIDE the pot interior.
        t = min(0.95, max(0.30, branch.attach_point[1] / float(n_trunk)))
        # Tier sits slightly below the spine point (gravity).
        bx, by = _spine_point_at(spine, t)
        # Width grows with segment count AND session abundance --
        # long sessions make tiers fuller.
        seg_count = max(1, len(branch.segments))
        half_w = min(170.0, (60.0 + 14.0 * seg_count) * ab)
        # Belt-and-braces: make sure the bottom edge of the tier
        # ellipse (centre + ry) stays above the pot rim. ry = half_w
        # * 0.32 in ``_foliage_tier``; reserve 8 px of breathing room.
        max_tier_y = canvas.ORIGIN_Y - half_w * 0.32 - 8
        tier_y = min(by + 4, max_tier_y)
        # Off-centre: lower tiers favour the lean side, upper tiers
        # opposite, giving the canopy a wind-shaped look.
        seed = max(1, branch.attach_point[1] * 23 + orig_i + 1)
        h0, _, _, _ = canvas.quad_lcg(seed)
        # Side flips for visual balance.
        side = -1.0 if orig_i % 2 == 0 else 1.0
        offset = side * (12.0 + (h0 / 255.0) * 18.0)
        tier_cx = bx + offset
        out.append(
            _foliage_tier(
                tier_cx,
                tier_y,
                half_width=half_w,
                seed=seed,
                density=max(4, 3 + seg_count // 2),
            )
        )
        _ = draw_i
    return "".join(out)


def _apex_tier(state: TreeState) -> str:
    """Flat-topped apex -- the pine signature. Always present."""
    spine = _spine(state)
    apex_x, apex_y = spine[-1]
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    # Caps: flower count translates to apex puff, but unbounded flower
    # counts (sessions with 20+ WebFetches) used to push the tier
    # wider than the canvas. Capped at 5 visible flowers' worth, which
    # adds at most ~30 px on each side -- comfortably inside the
    # viewport.
    flower_bonus = min(5, len(state.flowers))
    ab = canvas.abundance(state.event_count)
    return _foliage_tier(
        apex_x,
        apex_y - 4,
        half_width=min(150.0, (110.0 + flower_bonus * 3) * ab),
        seed=seed * 13 + 1,
        density=8 + flower_bonus + canvas.density_level(state.event_count),
    )


def _offshoots(state: TreeState) -> str:
    """Sub-agent offshoots -- thin curve with a small Crail berry tip."""
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
        tx = ax + side * 55
        ty = ay + 4
        out.append(
            f'<path d="M{ax:.1f},{ay:.1f} Q{ax + side * 24:.1f},{ay - 8:.1f} '
            f'{tx:.1f},{ty:.1f}" stroke="{tokens.BARK}" stroke-width="2.2" '
            f'fill="none" stroke-linecap="round" />'
        )
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
        length = 28 + i * 6
        x0 = canvas.ORIGIN_X + side * 10
        x1 = canvas.ORIGIN_X + side * length
        out.append(
            f'<path d="M{x0:.1f},{base:.1f} Q{x0 + side * 14:.1f},{base + 7:.1f} '
            f'{x1:.1f},{base + 2:.1f}" stroke="{tokens.BARK_DEEP}" '
            f'stroke-width="3.4" fill="none" stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(out)


def render_pine(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    _ = ctx
    return (
        _trunk_path(state)
        + _bark_texture(state)
        + _branches_as_tiers(state)
        + _apex_tier(state)
        + _offshoots(state)
        + _roots(state)
    )

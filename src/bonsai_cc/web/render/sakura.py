"""``render_sakura`` -- the swift / cherry-blossom theme.

Literati-style (bunjin) cherry tree: tall and slender with a single
dramatic curve, sparse branches, dense pink blossom clusters, and
a few drifting blossoms in the air. The showcase theme; chosen as
the README hero image.

Choices that differ from the generic renderer:

* Slender trunk (24px → 5px) with a pronounced single C-curve.
* Sparse branches -- only the longest ``ceil(n / 2)`` branches get
  geometry; remaining branches contribute to canopy fullness via
  the apex cluster.
* Foliage clusters are five-petal blossoms (drawn via the shared
  blossom helper) rather than ellipses. Mix of pale and deep cherry
  per-cluster, 30 / 70 by deterministic seed.
* 5 free-drifting blossoms placed deterministically around the
  tree at 50 % opacity.
"""

from __future__ import annotations

import math

from bonsai_cc.growth.state import Branch, TreeState
from bonsai_cc.web.render import canvas, tokens

__all__ = ["render_sakura"]


# Same envelope as the generic bonsai but slightly taller -- the
# bunjin silhouette wants stretch.
_TRUNK_MAX_PX: float = float(canvas.TREE_BASE_Y - canvas.TREE_TOP_Y)
_TRUNK_MIN_PX: float = 100.0


def _trunk_top_y(state: TreeState) -> float:
    n = max(0, len(state.trunk))
    growth = min(1.0, n / 6.0)
    height_px = _TRUNK_MIN_PX + (_TRUNK_MAX_PX - _TRUNK_MIN_PX) * growth
    return canvas.TREE_BASE_Y - height_px


def _trunk_spine(state: TreeState) -> list[tuple[float, float]]:
    """A bunjin-style spine: pronounced single C-curve.

    The trunk leans hard to one side at the midpoint then corrects.
    Per-session seed picks which side (so different sessions get
    different curves) and how dramatic.
    """
    top_y = _trunk_top_y(state)
    base_x = float(canvas.ORIGIN_X)
    base_y = float(canvas.TREE_BASE_Y)
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    h0, h1, _, _ = canvas.quad_lcg(seed)
    side = 1.0 if (h0 & 1) else -1.0
    # Mid-point displacement: 60-100 px to the chosen side.
    sway = side * (60.0 + (h1 / 255.0) * 40.0)
    mid_x = base_x + sway
    mid_y = (top_y + base_y) / 2
    apex_x = base_x + sway * 0.15  # apex returns near vertical
    apex_y = top_y
    # Anchor low on the spine so the bottom doesn't twist.
    low_y = base_y - (base_y - top_y) * 0.18
    low_x = base_x + sway * 0.10
    return [
        (base_x, base_y),
        (low_x, low_y),
        (mid_x, mid_y),
        (apex_x, apex_y),
    ]


def _trunk_path(state: TreeState) -> str:
    spine = _trunk_spine(state)
    return canvas.tapered_ribbon_path(
        spine,
        w_start=24.0,
        w_end=5.0,
        fill=tokens.BARK,
        taper_curve=1.6,
    )


def _bark_texture(state: TreeState) -> str:
    """Vertical striations along the lit side + an inner shadow
    along the concave (unlit) side. Sakura's bark is finer than the
    generic bonsai's -- fewer marks, lighter opacity, since the
    blossoms dominate the silhouette."""
    spine = _trunk_spine(state)
    if len(spine) < 4:
        return ""
    sway = spine[2][0] - spine[0][0]
    lit_side = 1.0 if sway >= 0 else -1.0
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    inner = canvas.bark_inner_shadow(
        spine, shadow_side=-lit_side,
        color=tokens.BARK_DEEP, width=2.5, opacity=0.32,
    )
    striations = canvas.vertical_bark_striations(
        spine, seed=seed * 11, side=lit_side, count=3,
        color=tokens.BARK_DEEP, opacity=0.30,
    )
    return inner + striations


def _attach_xy(branch: Branch, state: TreeState) -> tuple[float, float]:
    """Interpolate along the trunk spine to find the branch base."""
    spine = _trunk_spine(state)
    n_trunk = max(1, len(state.trunk))
    frac = min(1.0, max(0.0, branch.attach_point[1] / float(n_trunk)))
    # Linear segments between four spine anchors; for sakura's
    # sparse branches this is precise enough.
    if frac <= 0:
        return spine[0]
    if frac >= 1.0:
        return spine[-1]
    seg_count = len(spine) - 1
    pos = frac * seg_count
    i = int(pos)
    t = pos - i
    p0 = spine[i]
    p1 = spine[i + 1]
    return p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t


def _branches_with_blossoms(state: TreeState) -> str:
    """Sparse branches with dense blossom clusters at each tip.

    Only the longest ceil(n/2) branches get drawn; the rest
    contribute to the apex cluster's fullness instead.
    """
    if not state.branches:
        return ""
    # Pick the longest branches first; preserve original index for
    # deterministic side-alternation.
    indexed = list(enumerate(state.branches))
    indexed.sort(key=lambda kv: -len(kv[1].segments))
    keep = max(1, math.ceil(len(indexed) / 2))
    chosen = sorted(indexed[:keep], key=lambda kv: kv[0])
    out: list[str] = []
    ab = canvas.abundance(state.event_count)
    for draw_i, (orig_i, branch) in enumerate(chosen):
        if not branch.segments:
            continue
        ax, ay = _attach_xy(branch, state)
        side = -1.0 if orig_i % 2 == 0 else 1.0
        # Sakura branches are *less* horizontal than the generic
        # bonsai -- they reach upward and outward.
        length_px = min(170.0, 80.0 + 16.0 * len(branch.segments))
        seed = (branch.attach_point[1] + 1) * 19 + orig_i + 1
        h0, h1, _, _ = canvas.quad_lcg(seed)
        mid_x = ax + side * length_px * 0.55
        mid_y = ay - length_px * 0.20 + (h0 / 255.0 - 0.5) * 12.0
        tip_x = ax + side * length_px
        tip_y = ay - length_px * 0.45 + (h1 / 255.0 - 0.5) * 10.0
        near_x = ax + side * 14.0
        near_y = ay - 4.0
        points = [(ax, ay), (near_x, near_y), (mid_x, mid_y), (tip_x, tip_y)]
        out.append(
            canvas.tapered_ribbon_path(
                points,
                w_start=8.0,
                w_end=2.0,
                fill=tokens.BARK,
                taper_curve=1.4,
            )
        )
        # Dense tip blossom cluster; tip and mid clusters scale with
        # session abundance so long sessions read visibly fuller.
        out.append(
            _blossom_cluster(
                tip_x, tip_y, seed=seed + 31,
                count=int(10 * ab), radius=22 * ab,
            )
        )
        # Mid-branch smaller cluster so the branch reads as full.
        out.append(
            _blossom_cluster(
                mid_x, mid_y, seed=seed + 67,
                count=int(6 * ab), radius=14 * ab,
            )
        )
        _ = draw_i
    return "".join(out)


def _five_petal_blossom(
    cx: float, cy: float, *, scale: float, fill: str,
    rotation: float = 0.0,
) -> str:
    """Single five-petal blossom centred on (cx, cy).

    ``rotation`` rotates the whole blossom by N degrees. Without it
    every blossom had a petal pointing straight up (angle=0), and a
    dense apex cluster aligned those upward petals into visible
    vertical bars at small zoom levels.
    """
    petals: list[str] = []
    for i in range(5):
        angle = i * 72 + rotation
        rx = 2.2 * scale
        ry = 4.6 * scale
        petals.append(
            f'<ellipse cx="0" cy="-{4.0 * scale:.2f}" rx="{rx:.2f}" '
            f'ry="{ry:.2f}" fill="{fill}" transform="rotate({angle:.1f})" />'
        )
    center = (
        f'<circle cx="0" cy="0" r="{1.4 * scale:.2f}" fill="{tokens.CRAIL}" />'
    )
    return (
        f'<g transform="translate({cx:.1f},{cy:.1f})">'
        + "".join(petals)
        + center
        + "</g>"
    )


def _blossom_cluster(
    cx: float, cy: float, *, seed: int, count: int, radius: float
) -> str:
    """A cluster of ``count`` five-petal blossoms with jitter.

    Mix is 30 % pale / 70 % deep, deterministic per-blossom. Each
    blossom is rotated 0-360 deg so the up-pointing petal of one
    doesn't align with the up-pointing petal of the next neighbour,
    which used to read as vertical stripes when the cluster was dense.
    """
    parts: list[str] = []
    for i in range(count):
        h0, h1, h2, h3 = canvas.quad_lcg(seed * 100 + i)
        h4, _, _, _ = canvas.quad_lcg(seed * 100 + i + 991)
        dx = (h0 / 255.0 - 0.5) * 2 * radius
        dy = (h1 / 255.0 - 0.5) * 2 * radius * 0.75
        scale = 0.8 + (h2 / 255.0) * 0.7
        fill = tokens.SAKURA_PALE if (h3 / 255.0) < 0.30 else tokens.SAKURA_DEEP
        rotation = (h4 / 255.0) * 72.0  # 0-72 deg covers the 5-fold symmetry.
        parts.append(_five_petal_blossom(
            cx + dx, cy + dy, scale=scale, fill=fill, rotation=rotation,
        ))
    return f"<g>{''.join(parts)}</g>"


def _apex_blossoms(state: TreeState) -> str:
    spine = _trunk_spine(state)
    apex_x, apex_y = spine[-1]
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    # Flower events puff out the apex slightly: web-fetch flowers
    # don't get their own glyph on sakura, so they register as extra
    # blossoms in the apex cluster instead.
    flower_bonus = min(5, len(state.flowers))
    level = canvas.density_level(state.event_count)
    # Density rebalanced: ``count`` grows slowly, ``radius`` grows
    # faster. The earlier values (count up to 32, radius capped at
    # 47) packed 5-petal blossoms tight enough that their vertical
    # petals (rx=2.2, ry=4.6) aliased into visible vertical bars at
    # the default zoom. Spreading them across a larger area at
    # similar count keeps the silhouette dense without the
    # bar-pattern artefact.
    return _blossom_cluster(
        apex_x, apex_y - 8,
        seed=seed + 11,
        count=10 + flower_bonus + level,
        radius=44 + flower_bonus * 2 + level * 5,
    )


def _drifting_blossoms(state: TreeState) -> str:
    """Five blossoms drifting around the tree as distant air-flowers.

    Scale and opacity are deliberately small: bigger drifters
    competed with the canopy. Final tuning sits around half-scale
    at 40% opacity so they read as background atmosphere instead of
    subjects.
    """
    seed = max(1, int(state.seed_hex[:8] or "0", 16) if state.seed_hex else 1)
    parts: list[str] = []
    # Long sessions fill more air with drifting blossoms -- caps at
    # 5 + 6 = 11 via the density_level tier (max 3) so we never
    # crowd the canvas.
    drifter_count = 5 + canvas.density_level(state.event_count) * 2
    for i in range(drifter_count):
        h0, h1, h2, h3 = canvas.quad_lcg(seed * 7 + i)
        # Drifters spread across the upper 60% of the canvas, avoiding
        # the sun/moon corner.
        x = 200 + (h0 / 255.0) * 600
        y = 80 + (h1 / 255.0) * 320
        # Scale 0.35-0.70 -- small enough to read as distant.
        scale = 0.35 + (h2 / 255.0) * 0.35
        fill = tokens.SAKURA_PALE if (h3 / 255.0) < 0.35 else tokens.SAKURA_DEEP
        parts.append(
            f'<g opacity="0.4">{_five_petal_blossom(x, y, scale=scale, fill=fill)}</g>'
        )
    return "".join(parts)


def _flowers(state: TreeState) -> str:
    """Web fetch / web search events.

    Sakura's visual signature is already dense blossoms; the
    five-petal glyph for fetch events read as clip-art "stars" in
    the first cut. Absorbed into the apex cluster instead -- see
    ``_apex_blossoms``.
    """
    _ = state
    return ""


def _offshoots(state: TreeState) -> str:
    """Sub-agent offshoots -- thin Crail-tipped twigs."""
    if not state.offshoots:
        return ""
    spine = _trunk_spine(state)
    base_x, base_y = spine[0]
    out: list[str] = []
    for i, off in enumerate(state.offshoots):
        if not off.segments:
            continue
        ax, ay = canvas.project_xy(
            off.attach_point[0], off.attach_point[1]
        )
        side = -1.0 if i % 2 == 0 else 1.0
        tx = ax + side * 55.0
        ty = ay + 2.0
        out.append(
            f'<path d="M{ax:.1f},{ay:.1f} Q{ax + side * 22:.1f},{ay - 5:.1f} '
            f'{tx:.1f},{ty:.1f}" stroke="{tokens.BARK}" stroke-width="2" '
            f'fill="none" stroke-linecap="round" />'
        )
        # Small SAKURA_DEEP berry at the offshoot tip -- a single
        # soft circle, not a five-petal glyph, so it doesn't
        # compete with the canopy.
        out.append(
            f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="4" '
            f'fill="{tokens.SAKURA_DEEP}" />'
        )
    _ = base_x, base_y
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
            f'<path d="M{x0:.1f},{base:.1f} Q{x0 + side * 9:.1f},{base + 5:.1f} '
            f'{x1:.1f},{base + 2:.1f}" stroke="{tokens.BARK_DEEP}" '
            f'stroke-width="2.4" fill="none" stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(out)


def render_sakura(state: TreeState, ctx: canvas.CanvasCtx) -> str:
    """Build the SVG body for the cherry-blossom theme."""
    _ = ctx
    return (
        # Drifting blossoms are drawn FIRST (behind the tree) so the
        # tree silhouette doesn't get visually pierced by them.
        _drifting_blossoms(state)
        + _trunk_path(state)
        + _bark_texture(state)
        + _branches_with_blossoms(state)
        + _apex_blossoms(state)
        + _flowers(state)
        + _offshoots(state)
        + _roots(state)
    )

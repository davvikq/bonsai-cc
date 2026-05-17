"""Shared geometry + atmosphere helpers used by every theme renderer.

Per-theme renderers compose their tree out of these primitives:

* canvas frame (viewBox, origin, time-of-day)
* sky gradient + celestial body
* pot + soil + scattered pebbles
* tapered ribbon paths (trunk / branch fills)
* Catmull-Rom-to-Bezier smoothing
* deterministic per-leaf jitter (LCG)
* leaf-cluster generator

Everything that's NOT specific to a theme's silhouette lives here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bonsai_cc.web.render import tokens

# ---------------------------------------------------------------------------
# Canvas dimensions -- chosen so the bonsai proportions land on
# round numbers and the existing JS / tests keep working.
# ---------------------------------------------------------------------------

VB_W: int = 1000
VB_H: int = 800
ORIGIN_X: int = VB_W // 2          # 500 -- horizontal centre
ORIGIN_Y: int = 600                # 75% down -- pot rim sits here
UNIT: int = 30                     # logical-unit -> SVG-pixel scale

# Vertical envelope for the tree itself. Sky owns 0..TREE_TOP_Y; the
# tree occupies TREE_TOP_Y..ORIGIN_Y; the pot/ground below ORIGIN_Y.
TREE_TOP_Y: int = 400              # tree height <= 50% of viewport
TREE_BASE_Y: int = ORIGIN_Y        # trunk seats here
POT_HEIGHT: int = 110
POT_BOTTOM_Y: int = ORIGIN_Y + POT_HEIGHT


@dataclass(frozen=True, slots=True)
class CanvasCtx:
    """Render-time context passed to per-theme renderers.

    Pure data; renderers stay free of globals so the test suite can
    spin them up with arbitrary parameters.

    ``theme`` is the page theme ("light" or "dark") and drives the
    sky/celestial atmosphere. The per-theme tree renderers don't
    use it today (their fills are theme-agnostic), but
    it's available here so a future leaf-tint adjustment can read
    it without a signature change.
    """

    hour: int                      # 0..23, time of day for sky band
    theme: str = "light"           # "light" | "dark" -- sky/moon swap
    viewport_w: int = VB_W
    viewport_h: int = VB_H
    origin_x: int = ORIGIN_X
    origin_y: int = ORIGIN_Y
    tree_top_y: int = TREE_TOP_Y
    pot_bottom_y: int = POT_BOTTOM_Y


def project_xy(x: float, y: float) -> tuple[float, float]:
    """Logical ``(x, y)`` → SVG ``(x, y)``.

    Logical y grows upward; SVG y grows downward. UNIT is the pixel
    scale per logical unit. The trunk's logical y values are clamped
    against TREE_TOP_Y elsewhere; this helper is dumb.
    """
    return ORIGIN_X + x * UNIT, ORIGIN_Y - y * UNIT


# ---------------------------------------------------------------------------
# Defs / sky / ground / pot -- the atmosphere every renderer reuses.
# ---------------------------------------------------------------------------


def build_defs(hour: int, *, theme: str = "light") -> str:
    """Linear / radial gradients used by sky, pot, soil.

    ``theme="dark"`` overrides the time-of-day sky with a deep warm
    near-black gradient (NIGHT_DEEP → NIGHT). The sun/moon decision
    still lives in :func:`build_celestial`; this just paints the
    sky band.
    """
    if theme == "dark":
        sky_top, sky_bot = tokens.NIGHT_DEEP, tokens.NIGHT
    else:
        sky_top, sky_bot = tokens.sky_stops(hour)
    return (
        "<defs>"
        f'<linearGradient id="bcc-sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{sky_top}" />'
        f'<stop offset="100%" stop-color="{sky_bot}" />'
        "</linearGradient>"
        f'<linearGradient id="bcc-ground" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{tokens.PAMPAS_DEEP}" />'
        f'<stop offset="100%" stop-color="{tokens.CLOUDY}" />'
        "</linearGradient>"
        f'<linearGradient id="bcc-pot" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{tokens.CRAIL_DEEP}" />'
        f'<stop offset="100%" stop-color="{tokens.BARK_DEEP}" />'
        "</linearGradient>"
        # IDs kept compatible with old "id=sky" / "id=ground" tests.
        f'<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{sky_top}" />'
        f'<stop offset="100%" stop-color="{sky_bot}" />'
        "</linearGradient>"
        f'<linearGradient id="ground" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{tokens.PAMPAS_DEEP}" />'
        f'<stop offset="100%" stop-color="{tokens.CLOUDY}" />'
        "</linearGradient>"
        # Soft sun glow.
        f'<radialGradient id="sun" cx="0.5" cy="0.5" r="0.5">'
        f'<stop offset="0%" stop-color="{tokens.CRAIL}" stop-opacity="0.55" />'
        f'<stop offset="70%" stop-color="{tokens.CRAIL}" stop-opacity="0.20" />'
        f'<stop offset="100%" stop-color="{tokens.CRAIL}" stop-opacity="0" />'
        "</radialGradient>"
        f'<radialGradient id="moon" cx="0.45" cy="0.45" r="0.55">'
        f'<stop offset="0%" stop-color="{tokens.PAMPAS}" stop-opacity="0.85" />'
        f'<stop offset="100%" stop-color="{tokens.PAMPAS}" stop-opacity="0.35" />'
        "</radialGradient>"
        # Pot drop shadow -- a soft blur ellipse below the pot.
        f'<radialGradient id="pot-shadow" cx="0.5" cy="0.5" r="0.5">'
        f'<stop offset="0%" stop-color="{tokens.INK}" stop-opacity="0.18" />'
        f'<stop offset="100%" stop-color="{tokens.INK}" stop-opacity="0" />'
        "</radialGradient>"
        "</defs>"
    )


def build_sky() -> str:
    """Full-width sky rectangle. The gradient is in defs."""
    return f'<rect x="0" y="0" width="{VB_W}" height="{ORIGIN_Y}" fill="url(#bcc-sky)" />'


def build_celestial(hour: int, *, theme: str = "light") -> str:
    """Small sun (Crail at 60% opacity, soft) or off-white moon.

    Always off-centre, never dead-top. Day pushes sun toward upper
    right; night gives moon upper left so the composition has
    diagonal balance with the typically right-leaning trunk.

    ``theme="dark"`` forces the moon path regardless of the hour --
    a sunlit tree against a dark page would read as an open
    daytime window in a dark room. The moon at upper-left feels
    intentional in dark mode.
    """
    night = theme == "dark" or hour >= 22 or hour < 5
    if night:
        cx, cy, r = 220, 150, 36
        return (
            f'<circle cx="{cx}" cy="{cy}" r="{r * 2}" fill="url(#moon)" />'
            f'<circle cx="{cx}" cy="{cy}" r="{r:.0f}" fill="{tokens.PAMPAS}" fill-opacity="0.85" />'
        )
    sun_y = 180 if 8 <= hour < 18 else 250
    sun_cx = VB_W - 220
    return (
        f'<circle cx="{sun_cx}" cy="{sun_y}" r="80" fill="url(#sun)" />'
        f'<circle cx="{sun_cx}" cy="{sun_y}" r="28" fill="{tokens.CRAIL}" fill-opacity="0.55" />'
    )


def build_ground() -> str:
    """Soft horizon below the pot. Subtle gradient, no harsh edge."""
    return (
        f'<rect x="0" y="{ORIGIN_Y}" width="{VB_W}" '
        f'height="{VB_H - ORIGIN_Y}" fill="url(#bcc-ground)" />'
    )


def build_pot() -> str:
    """Shallow oval/trapezoid pot under the trunk.

    Order matters:

    1. Drop-shadow ellipse below the pot (anchors the tree).
    2. Pot body (trapezoid with rounded corners).
    3. Glaze-ring line near the rim.
    4. Soil ellipse along the rim.
    5. 2-3 pebble dots in cloudy on the soil.
    """
    cx = ORIGIN_X
    rim_y = ORIGIN_Y
    bot_y = POT_BOTTOM_Y
    rim_half_w = 160
    bot_half_w = 130
    # Drop shadow -- wide soft ellipse just below the pot bottom.
    shadow = (
        f'<ellipse cx="{cx}" cy="{bot_y + 18}" rx="{rim_half_w + 30}" '
        f'ry="22" fill="url(#pot-shadow)" />'
    )
    # Pot body -- slight curvature on the rim, slight inset on the bottom.
    pot = (
        f'<path d="'
        f'M{cx - rim_half_w},{rim_y} '
        # rounded top-left into vertical wall
        f'Q{cx - rim_half_w - 4},{rim_y + 8} {cx - rim_half_w + 4},{rim_y + 18} '
        f'L{cx - bot_half_w},{bot_y - 10} '
        # rounded bottom-left
        f'Q{cx - bot_half_w + 6},{bot_y} {cx - bot_half_w + 14},{bot_y} '
        f'L{cx + bot_half_w - 14},{bot_y} '
        f'Q{cx + bot_half_w - 6},{bot_y} {cx + bot_half_w},{bot_y - 10} '
        f'L{cx + rim_half_w - 4},{rim_y + 18} '
        f'Q{cx + rim_half_w + 4},{rim_y + 8} {cx + rim_half_w},{rim_y} '
        f'Z" fill="url(#bcc-pot)" />'
    )
    # Glaze ring -- a thin darker stroke parallel to the rim.
    ring = (
        f'<path d="M{cx - rim_half_w + 10},{rim_y + 22} '
        f'Q{cx},{rim_y + 26} {cx + rim_half_w - 10},{rim_y + 22}" '
        f'stroke="{tokens.BARK_DEEP}" stroke-width="1.5" fill="none" '
        f'stroke-linecap="round" opacity="0.55" />'
    )
    # Soil along the rim.
    soil = (
        f'<ellipse cx="{cx}" cy="{rim_y + 2}" rx="{rim_half_w - 8}" ry="8" '
        f'fill="{tokens.BARK_DEEP}" />'
    )
    # 2-3 cloudy pebble dots on the soil.
    pebbles = (
        f'<circle cx="{cx - 70}" cy="{rim_y + 1}" r="4" fill="{tokens.CLOUDY}" opacity="0.7" />'
        f'<circle cx="{cx + 30}" cy="{rim_y + 3}" r="3" fill="{tokens.CLOUDY}" opacity="0.55" />'
        f'<circle cx="{cx + 90}" cy="{rim_y + 1}" r="3.5" fill="{tokens.CLOUDY}" opacity="0.7" />'
    )
    return shadow + pot + ring + soil + pebbles


# ---------------------------------------------------------------------------
# Deterministic per-leaf jitter -- identical algorithm in the JS
# client. Kept here so renderers compose it.
# ---------------------------------------------------------------------------

_LCG_A = 1103515245
_LCG_C = 12345
_LCG_M = 0x7FFFFFFF


def lcg(seed: int) -> int:
    """One step of the LCG. Returns a 31-bit unsigned int."""
    return (seed * _LCG_A + _LCG_C) & _LCG_M


def quad_lcg(seed: int) -> tuple[int, int, int, int]:
    """Four 0-255 bytes from a single seed."""
    h = lcg(seed)
    return (h & 0xFF, (h >> 8) & 0xFF, (h >> 16) & 0xFF, (h >> 23) & 0xFF)


# ---------------------------------------------------------------------------
# Bezier helpers -- Catmull-Rom-to-cubic smoothing for any polyline,
# plus a tapered-ribbon path builder used by trunk / branch fills.
# ---------------------------------------------------------------------------


def catmull_rom_bezier(
    points: list[tuple[float, float]], tension: float = 0.5
) -> str:
    """Convert a polyline into cubic-Bezier ``C ... C ...`` commands.

    The first point is assumed to be the current pen position via a
    prior ``M``. Tension 0.5 reads as "natural curve" -- high enough
    to be visibly smooth, low enough to avoid overshoot.
    """
    n = len(points)
    if n < 2:
        return ""
    parts: list[str] = []
    for i in range(n - 1):
        p0 = points[max(0, i - 1)]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[min(n - 1, i + 2)]
        c1x = p1[0] + (p2[0] - p0[0]) * tension / 6
        c1y = p1[1] + (p2[1] - p0[1]) * tension / 6
        c2x = p2[0] - (p3[0] - p1[0]) * tension / 6
        c2y = p2[1] - (p3[1] - p1[1]) * tension / 6
        parts.append(
            f"C{c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} "
            f"{p2[0]:.1f},{p2[1]:.1f}"
        )
    return " ".join(parts)


def tapered_ribbon_path(
    points: list[tuple[float, float]],
    *,
    w_start: float,
    w_end: float,
    fill: str,
    taper_curve: float = 1.0,
) -> str:
    """Build a closed filled ``<path>`` along ``points``.

    Width interpolates between ``w_start`` (first point) and
    ``w_end`` (last). ``taper_curve > 1`` accelerates the taper near
    the tip (used by trunks so the bottom 30% stays near-constant
    thickness and the upper section narrows quickly -- bonsai-style
    taper).

    Both sides of the ribbon get Bezier-smoothed so the silhouette
    reads as organic, not as a folded polygon.
    """
    if len(points) < 2:
        return ""
    n = len(points)
    widths: list[float] = []
    for i in range(n):
        t = i / (n - 1)
        # Power-curve taper: t=0 → 0 (full w_start), t=1 → 1 (full w_end).
        eff = t ** taper_curve
        widths.append(w_start + (w_end - w_start) * eff)
    normals: list[tuple[float, float]] = []
    for i in range(n):
        if i == 0:
            dx = points[1][0] - points[0][0]
            dy = points[1][1] - points[0][1]
        elif i == n - 1:
            dx = points[-1][0] - points[-2][0]
            dy = points[-1][1] - points[-2][1]
        else:
            dx = points[i + 1][0] - points[i - 1][0]
            dy = points[i + 1][1] - points[i - 1][1]
        length = math.hypot(dx, dy) or 1.0
        normals.append((-dy / length, dx / length))
    left = [
        (
            points[i][0] + normals[i][0] * widths[i] / 2,
            points[i][1] + normals[i][1] * widths[i] / 2,
        )
        for i in range(n)
    ]
    right = [
        (
            points[i][0] - normals[i][0] * widths[i] / 2,
            points[i][1] - normals[i][1] * widths[i] / 2,
        )
        for i in range(n)
    ]
    right.reverse()
    head = f"M{left[0][0]:.1f},{left[0][1]:.1f}"
    body_left = catmull_rom_bezier(left)
    body_right = catmull_rom_bezier([left[-1], *right])
    return f'<path d="{head} {body_left} {body_right} Z" fill="{fill}" />'


# ---------------------------------------------------------------------------
# Leaf-cluster generator.
# ---------------------------------------------------------------------------


def leaf_cluster(
    cx: float,
    cy: float,
    *,
    seed: int,
    base_fill: str,
    highlight_fill: str,
    count: int = 9,
    radius: float = 18.0,
    leaf_rx: float = 7.0,
    leaf_ry: float = 11.0,
    rotation_range: float = 360.0,
    scale_min: float = 0.7,
    scale_max: float = 1.3,
    opacity: float = 1.0,
) -> str:
    """Emit a cluster of ``count`` ellipse "leaves" centred on ``(cx, cy)``.

    Two-tone fill: the lower (interior) half uses ``base_fill``, the
    upper (edge-catching) half uses ``highlight_fill``. Position,
    rotation, and scale are deterministically jittered from
    ``seed`` via the shared LCG.

    Returns a single ``<g>`` so the cluster can be group-animated.
    """
    parts: list[str] = []
    half = count // 2
    for i in range(count):
        h0, h1, h2, h3 = quad_lcg(seed * 100 + i)
        dx = (h0 / 255.0 - 0.5) * 2 * radius
        dy = (h1 / 255.0 - 0.5) * 2 * radius * 0.7  # vertical compression
        scale = scale_min + (h2 / 255.0) * (scale_max - scale_min)
        rot = (h3 / 255.0 - 0.5) * rotation_range
        # First half darker, second half lighter -- edges of the
        # cluster (drawn last so they sit on top) catch the light.
        fill = base_fill if i < half else highlight_fill
        rx = leaf_rx * scale
        ry = leaf_ry * scale
        parts.append(
            f'<ellipse cx="{cx + dx:.1f}" cy="{cy + dy:.1f}" '
            f'rx="{rx:.1f}" ry="{ry:.1f}" '
            f'fill="{fill}" '
            f'transform="rotate({rot:.1f},{cx + dx:.1f},{cy + dy:.1f})" />'
        )
    opacity_attr = f' opacity="{opacity:.2f}"' if opacity < 0.999 else ""
    return f"<g{opacity_attr}>{''.join(parts)}</g>"


def vertical_bark_striations(
    spine: list[tuple[float, float]],
    *,
    seed: int,
    side: float = 1.0,
    count: int = 5,
    color: str = "#000000",
    opacity: float = 0.35,
) -> str:
    """Short vertical hatch marks along a trunk spine -- real bark grain.

    Bark grain on most trees runs vertically (parallel to the trunk
    axis), not horizontally. This helper emits ``count`` short
    near-vertical stroke marks distributed along the spine, on the
    chosen ``side`` (+1 right, -1 left, 0 centred). Each mark is
    rotated to match the local trunk tangent so it lies along the
    grain rather than against it.

    The exception is birch: birch has signature horizontal lenticels.
    Birch uses ``horizontal_bark_marks`` instead -- kept as a separate
    helper because mixing styles confuses the silhouette read.
    """
    if len(spine) < 2:
        return ""
    parts: list[str] = []
    for i in range(count):
        h0, h1, h2, _ = quad_lcg(seed * 100 + i + 1)
        # Position along the spine in [0.15, 0.85] -- keep marks away
        # from the soil line and the apex foliage.
        t = 0.15 + (h0 / 255.0) * 0.70
        # Linear interpolation along spine for position; precise
        # enough for short hatch marks.
        n_seg = len(spine) - 1
        pos = t * n_seg
        si = min(n_seg - 1, int(pos))
        u = pos - si
        p0 = spine[si]
        p1 = spine[si + 1]
        cx = p0[0] + (p1[0] - p0[0]) * u
        cy = p0[1] + (p1[1] - p0[1]) * u
        # Tangent → rotation angle so the hatch lies along the trunk.
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        length_tan = math.hypot(dx, dy) or 1.0
        # Normal pointing "outward" on the chosen side.
        nx = -dy / length_tan * side
        ny = dx / length_tan * side
        # Offset from spine onto bark surface (jitter the offset
        # so marks don't all land at the same depth).
        offset = 4.0 + (h1 / 255.0) * 6.0
        bx = cx + nx * offset
        by = cy + ny * offset
        # Hatch length 6-12 px along the grain.
        hatch_len = 6.0 + (h2 / 255.0) * 6.0
        # Tangent direction (along the grain).
        tx = dx / length_tan
        ty = dy / length_tan
        x1 = bx - tx * hatch_len / 2
        y1 = by - ty * hatch_len / 2
        x2 = bx + tx * hatch_len / 2
        y2 = by + ty * hatch_len / 2
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" '
            f'x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="1.1" '
            f'stroke-linecap="round" opacity="{opacity:.2f}" />'
        )
    return "".join(parts)


def horizontal_bark_marks(
    spine: list[tuple[float, float]],
    *,
    count: int,
    color: str,
    width_estimator: float = 12.0,
) -> str:
    """Horizontal short dashes -- birch only.

    Birch bark has horizontal lenticels (the dark slash marks). Every
    ~50px up the trunk we draw a short dark dash perpendicular to
    the grain. Alternating left/right so the trunk reads as having
    bark on both sides without being symmetric.
    """
    if len(spine) < 2:
        return ""
    parts: list[str] = []
    for i in range(count):
        t = 0.10 + (i / max(1, count - 1)) * 0.80
        n_seg = len(spine) - 1
        pos = t * n_seg
        si = min(n_seg - 1, int(pos))
        u = pos - si
        p0 = spine[si]
        p1 = spine[si + 1]
        cx = p0[0] + (p1[0] - p0[0]) * u
        cy = p0[1] + (p1[1] - p0[1]) * u
        side = -1.0 if i % 2 == 0 else 1.0
        half = width_estimator * 0.5
        dash_offset = side * half * 0.4
        x1 = cx - half + dash_offset
        x2 = cx + half * 0.4 + dash_offset
        parts.append(
            f'<line x1="{x1:.1f}" y1="{cy:.1f}" '
            f'x2="{x2:.1f}" y2="{cy:.1f}" '
            f'stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" opacity="0.85" />'
        )
    return "".join(parts)


def bark_inner_shadow(
    spine: list[tuple[float, float]],
    *,
    shadow_side: float,
    color: str,
    width: float = 3.0,
    opacity: float = 0.30,
) -> str:
    """A subtle inner shadow stripe along one side of the trunk.

    ``shadow_side`` = -1 for left shadow, +1 for right. The stripe
    follows the spine offset inward by ~half the local width so it
    looks like depth rather than a separate line.
    """
    if len(spine) < 2:
        return ""
    n = len(spine)
    shadow_pts: list[tuple[float, float]] = []
    for i in range(n):
        if i == 0:
            dx = spine[1][0] - spine[0][0]
            dy = spine[1][1] - spine[0][1]
        elif i == n - 1:
            dx = spine[-1][0] - spine[-2][0]
            dy = spine[-1][1] - spine[-2][1]
        else:
            dx = spine[i + 1][0] - spine[i - 1][0]
            dy = spine[i + 1][1] - spine[i - 1][1]
        L = math.hypot(dx, dy) or 1.0
        nx = -dy / L * shadow_side
        ny = dx / L * shadow_side
        # Taper the offset toward the tip so the shadow doesn't
        # spike above the trunk.
        taper = 1.0 - (i / max(1, n - 1)) * 0.55
        offset = width * 1.2 * taper
        shadow_pts.append((spine[i][0] + nx * offset, spine[i][1] + ny * offset))
    head = f"M{shadow_pts[0][0]:.1f},{shadow_pts[0][1]:.1f}"
    body = catmull_rom_bezier(shadow_pts)
    return (
        f'<path d="{head} {body}" stroke="{color}" '
        f'stroke-width="{width:.1f}" fill="none" '
        f'opacity="{opacity:.2f}" stroke-linecap="round" />'
    )


def build_idle_svg(
    project_root: str | None = None,
    *,
    hour: int | None = None,
    theme: str = "light",
) -> str:
    """Render the "no session yet" placeholder as a full SVG.

    Same coordinate system + canvas wrapping as ``state_to_svg``, so
    the visual transition from idle → first event is "seedling
    appears in this exact pot." The page reuses the SVG-swap
    crossfade. Includes a small green seedling poking out of the
    soil so the pot doesn't look completely barren.

    Producing this server-side means the SSE payload's ``svg`` field
    is always substantial (~3 KB), which defeats the browser SSE
    buffer-threshold issue that left "connecting…" stuck on screen
    until the first real event arrived.
    """
    h = 12 if hour is None else hour
    cx = ORIGIN_X
    root_label = (
        project_root
        if project_root
        else "this project"
    )
    # Escape angle brackets in the path so an unusual cwd can't break
    # the SVG envelope.
    safe_label = (
        root_label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    # Tiny seedling: two cotyledons on a short stem at the pot
    # centre, color MOSS. Reads as "newly planted, awaiting growth."
    seedling = (
        f'<line x1="{cx}" y1="{ORIGIN_Y - 4}" x2="{cx}" y2="{ORIGIN_Y - 22}" '
        f'stroke="{tokens.MOSS}" stroke-width="2" stroke-linecap="round" />'
        f'<ellipse cx="{cx - 7}" cy="{ORIGIN_Y - 22}" rx="7" ry="4" '
        f'fill="{tokens.MOSS}" transform="rotate(-25,{cx - 7},{ORIGIN_Y - 22})" />'
        f'<ellipse cx="{cx + 7}" cy="{ORIGIN_Y - 22}" rx="7" ry="4" '
        f'fill="{tokens.MOSS}" transform="rotate(25,{cx + 7},{ORIGIN_Y - 22})" />'
    )
    # Idle-state text fills: on dark sky we need light text so the
    # message reads against the near-black background.
    text_primary = tokens.PAMPAS if theme == "dark" else tokens.INK
    text_secondary = tokens.PAMPAS_DEEP if theme == "dark" else tokens.INK_SOFT
    text_muted = tokens.CLOUDY  # works on both -- mid-tone warm gray
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {VB_W} {VB_H}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="bonsai-cc waiting">'
        + build_defs(h, theme=theme)
        + build_sky()
        + build_celestial(h, theme=theme)
        + build_ground()
        + build_pot()
        + seedling
        + f'<text x="{cx}" y="270" text-anchor="middle" '
          f'fill="{text_primary}" font-family="system-ui, sans-serif" '
          f'font-size="34" font-weight="600" letter-spacing="-0.02em">'
          f'Waiting for Claude Code activity</text>'
        + f'<text x="{cx}" y="312" text-anchor="middle" '
          f'fill="{text_secondary}" '
          f'font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
          f'font-size="20">in {safe_label}</text>'
        + f'<text x="{cx}" y="358" text-anchor="middle" '
          f'fill="{text_muted}" font-family="system-ui, sans-serif" '
          f'font-size="18">run `claude` in another terminal to start growing your tree</text>'
        + "</svg>"
    )


def density_level(event_count: int) -> int:
    """Visual-richness tier as a function of session length.

    The growth engine produces unbounded state -- branches and
    leaves keep accumulating no matter how many events arrive. The
    renderer needs a parallel signal to enrich the silhouette so a
    100-event session doesn't read as visually frozen after the
    first ~20 events fill out the base composition. Each per-theme
    renderer maps this tier to its own enrichment: bamboo grows
    extra thinner stalks at the cluster periphery, oak/pine/willow
    add secondary branches on existing primaries, sakura drifts
    more blossoms in the air. Caps at 3 so a 500-event session
    doesn't push the renderer past ~40 visual elements.

    Tiers:

    * 0 (<=20 events): baseline silhouette only.
    * 1 (21-50): first enrichment.
    * 2 (51-100): second enrichment.
    * 3 (>100):    third enrichment + cap.
    """
    if event_count <= 20:
        return 0
    if event_count <= 50:
        return 1
    if event_count <= 100:
        return 2
    return 3


def abundance(event_count: int) -> float:
    """Continuous foliage-volume scalar.

    Where :func:`density_level` returns a small integer tier (good
    for counting "how many extra elements to add"), ``abundance``
    returns a smooth float that grows with ``event_count``. Renderers
    multiply cluster radius / leaf count / blossom radius by this so
    a 200-event tree visibly carries more foliage volume than a
    50-event tree, not just more branches.

    Curve: 0.85 at 0 events, 1.0 at ~50, 1.3 at ~150, asymptotic to
    ~1.6 so the tree fills but never bursts out of the viewport.
    """
    # Saturating curve: 0.85 + 0.75 * (1 - exp(-event_count / 80)).
    # Caps just under 1.6 at event_count -> infinity.
    import math
    return 0.85 + 0.75 * (1.0 - math.exp(-event_count / 80.0))


def shade(hex_color: str, amount: float) -> str:
    """Lighten (positive) or darken (negative) a #RRGGBB color.

    Used sparingly by renderers that want a touch of variation
    without introducing a brand-new token (e.g. per-stalk
    bamboo tints). The result must still be reviewed for
    palette discipline; renderers that go beyond a small
    +/-0.2 nudge should introduce a named token instead.
    """
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if amount >= 0:
        r = int(r + (255 - r) * amount)
        g = int(g + (255 - g) * amount)
        b = int(b + (255 - b) * amount)
    else:
        a = 1.0 + amount
        r = int(r * a)
        g = int(g * a)
        b = int(b * a)
    return f"#{r:02X}{g:02X}{b:02X}"


__all__ = [
    "ORIGIN_X",
    "ORIGIN_Y",
    "POT_BOTTOM_Y",
    "POT_HEIGHT",
    "TREE_BASE_Y",
    "TREE_TOP_Y",
    "UNIT",
    "VB_H",
    "VB_W",
    "CanvasCtx",
    "abundance",
    "bark_inner_shadow",
    "build_celestial",
    "build_defs",
    "build_ground",
    "build_idle_svg",
    "build_pot",
    "build_sky",
    "catmull_rom_bezier",
    "density_level",
    "horizontal_bark_marks",
    "lcg",
    "leaf_cluster",
    "project_xy",
    "quad_lcg",
    "shade",
    "tapered_ribbon_path",
    "vertical_bark_striations",
]

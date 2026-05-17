"""Per-theme silhouettes -- leaf shapes and trunk decorations.

The palette (colour) lives in :mod:`bonsai_cc.render.palette`.
*Shape* is what makes a python bamboo look different from a ruby
maple even at the same colour. This module owns that mapping and
ships the SVG shape strings both Python and JS consume.

For each theme, ``THEMES[theme]`` returns a :class:`ThemeConfig`
naming a leaf shape, a trunk decoration, and a leaf size scale.
The renderer's per-leaf dispatch reads :data:`LEAF_SHAPES` to
produce the SVG markup; the trunk renderer reads
:data:`TRUNK_DECORATIONS` to overlay bamboo nodes / birch bark /
nothing.

JS mirror: ``static/index.html`` carries the same THEMES dict
literal verbatim. Keep them in sync by hand -- a sync test is on
the v0.2 list.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "LEAF_SHAPES",
    "THEMES",
    "TRUNK_DECORATIONS",
    "ThemeConfig",
    "theme_config",
]


@dataclass(frozen=True, slots=True)
class ThemeConfig:
    """Visual config layered on top of a palette.

    * ``leaf_shape`` -- one of the keys in :data:`LEAF_SHAPES`.
    * ``trunk_decoration`` -- one of the keys in
      :data:`TRUNK_DECORATIONS`, or ``"plain"``.
    * ``leaf_scale`` -- multiplier on the base leaf size; lets us
      keep needles small and oak leaves big without rewriting the
      shape.
    """

    leaf_shape: str
    trunk_decoration: str = "plain"
    leaf_scale: float = 1.0


# Theme -> visual config. Palette colour separately in
# ``bonsai_cc.render.palette``.
THEMES: dict[str, ThemeConfig] = {
    "default":    ThemeConfig(leaf_shape="ellipse_broad",  trunk_decoration="plain"),
    "python":     ThemeConfig(leaf_shape="ellipse_narrow", trunk_decoration="bamboo", leaf_scale=0.95),
    "rust":       ThemeConfig(leaf_shape="needle",         trunk_decoration="plain", leaf_scale=0.85),
    "go":         ThemeConfig(leaf_shape="ellipse_broad",  trunk_decoration="plain", leaf_scale=1.20),
    "typescript": ThemeConfig(leaf_shape="ellipse_narrow", trunk_decoration="plain", leaf_scale=0.90),
    "javascript": ThemeConfig(leaf_shape="ellipse_narrow", trunk_decoration="plain", leaf_scale=0.95),
    "swift":      ThemeConfig(leaf_shape="blossom",        trunk_decoration="plain", leaf_scale=1.05),
    "ruby":       ThemeConfig(leaf_shape="maple",          trunk_decoration="plain", leaf_scale=1.10),
    "cpp":        ThemeConfig(leaf_shape="ellipse_broad",  trunk_decoration="plain", leaf_scale=1.05),
    "java":       ThemeConfig(leaf_shape="ellipse_broad",  trunk_decoration="plain", leaf_scale=1.10),
    "haskell":    ThemeConfig(leaf_shape="fan",            trunk_decoration="plain"),
    "zig":        ThemeConfig(leaf_shape="ellipse_narrow", trunk_decoration="birch", leaf_scale=0.95),
}


def theme_config(theme: str) -> ThemeConfig:
    """Return the theme's config or the default if unknown."""
    return THEMES.get(theme, THEMES["default"])


# ---------------------------------------------------------------------------
# Shape templates.
#
# Each entry is a *function* that takes (cx, cy, scale, fill, rotation_deg)
# and emits the SVG markup for one leaf-shape unit. The renderer
# wraps the result in a ``<g>`` per cluster so commit 5 can vary
# shade and rotation across the cluster without re-templating each
# call.
# ---------------------------------------------------------------------------


def _ellipse_broad(cx: float, cy: float, s: float, fill: str, rot: float) -> str:
    rx, ry = 7.0 * s, 4.5 * s
    return (
        f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
        f'fill="{fill}" opacity="0.92" transform="rotate({rot:.0f} {cx:.1f} {cy:.1f})" />'
    )


def _ellipse_narrow(cx: float, cy: float, s: float, fill: str, rot: float) -> str:
    rx, ry = 2.5 * s, 8.0 * s
    return (
        f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
        f'fill="{fill}" opacity="0.92" transform="rotate({rot:.0f} {cx:.1f} {cy:.1f})" />'
    )


def _needle(cx: float, cy: float, s: float, fill: str, rot: float) -> str:
    """Pine-needle cluster: 4 thin lines fanning from a point."""
    L = 10.0 * s
    lines: list[str] = []
    for angle in (-30, -10, 10, 30):
        rad = (rot + angle) * 3.14159 / 180
        from math import cos, sin

        x2 = cx + sin(rad) * L
        y2 = cy - cos(rad) * L
        lines.append(
            f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{fill}" stroke-width="1.4" stroke-linecap="round" />'
        )
    return "".join(lines)


def _blossom(cx: float, cy: float, s: float, fill: str, rot: float) -> str:
    """Sakura 5-petal blossom: small ellipses arrayed around the
    centre, with a tiny golden core."""
    r = 4.5 * s
    petals: list[str] = []
    for i in range(5):
        a = rot + i * 72
        petals.append(
            f'<ellipse cx="0" cy="-{r * 0.95:.1f}" '
            f'rx="{r * 0.55:.1f}" ry="{r * 0.95:.1f}" '
            f'fill="{fill}" opacity="0.9" '
            f'transform="rotate({a:.0f})" />'
        )
    core = f'<circle cx="0" cy="0" r="{r * 0.25:.1f}" fill="#F2C063" opacity="0.95" />'
    return f'<g transform="translate({cx:.1f},{cy:.1f})">{"".join(petals)}{core}</g>'


def _maple(cx: float, cy: float, s: float, fill: str, rot: float) -> str:
    """Maple-ish silhouette: five overlapping ovals forming a five-
    lobed shape. Pure ellipse math -- much simpler than a real
    maple path and still reads as "lobed" at low resolution."""
    lobe_r = 5.0 * s
    lobes: list[str] = []
    for i in range(5):
        a = rot + i * 72 - 90  # one lobe pointing up
        lobes.append(
            f'<ellipse cx="0" cy="-{lobe_r * 0.95:.1f}" '
            f'rx="{lobe_r * 0.65:.1f}" ry="{lobe_r * 1.15:.1f}" '
            f'fill="{fill}" opacity="0.85" '
            f'transform="rotate({a:.0f})" />'
        )
    return f'<g transform="translate({cx:.1f},{cy:.1f})">{"".join(lobes)}</g>'


def _fan(cx: float, cy: float, s: float, fill: str, rot: float) -> str:
    """Ginkgo fan: half-disc shape via a quadratic-Bezier triangle
    with a curved top edge."""
    w = 9.0 * s
    h = 7.0 * s
    # An arc from (-w/2, 0) to (w/2, 0) bulging up by h, then down
    # to the stem at (0, h*0.3).
    d = (
        f"M-{w / 2:.1f},0 Q0,-{h:.1f} {w / 2:.1f},0 "
        f"Q0,{h * 0.45:.1f} 0,{h * 0.3:.1f} Z"
    )
    return (
        f'<g transform="translate({cx:.1f},{cy:.1f}) rotate({rot:.0f})">'
        f'<path d="{d}" fill="{fill}" opacity="0.92" />'
        f"</g>"
    )


LEAF_SHAPES = {
    "ellipse_broad":  _ellipse_broad,
    "ellipse_narrow": _ellipse_narrow,
    "needle":         _needle,
    "blossom":        _blossom,
    "maple":          _maple,
    "fan":            _fan,
}


# ---------------------------------------------------------------------------
# Trunk decorations -- overlays drawn on top of the trunk fill.
# ---------------------------------------------------------------------------


def _trunk_bamboo(trunk_top_y: float, trunk_base_y: float, mid_x: float, dark: str) -> str:
    """Horizontal segment lines simulating bamboo nodes.

    Three to five evenly spaced thin bands across the trunk.
    """
    n = 4
    out: list[str] = []
    for i in range(1, n + 1):
        y = trunk_base_y - (trunk_base_y - trunk_top_y) * i / (n + 1)
        out.append(
            f'<line x1="{mid_x - 16:.1f}" y1="{y:.1f}" '
            f'x2="{mid_x + 16:.1f}" y2="{y:.1f}" '
            f'stroke="{dark}" stroke-width="3" stroke-linecap="round" '
            f'opacity="0.55" />'
        )
    return "".join(out)


def _trunk_birch(trunk_top_y: float, trunk_base_y: float, mid_x: float, dark: str) -> str:
    """Short horizontal dashes mimicking birch bark stripes.

    Smaller, more numerous, alternating side, lower contrast.
    """
    n = 8
    out: list[str] = []
    for i in range(1, n + 1):
        y = trunk_base_y - (trunk_base_y - trunk_top_y) * i / (n + 1)
        x_offset = -6 if i % 2 == 0 else 6
        out.append(
            f'<line x1="{mid_x + x_offset - 5:.1f}" y1="{y:.1f}" '
            f'x2="{mid_x + x_offset + 5:.1f}" y2="{y:.1f}" '
            f'stroke="{dark}" stroke-width="1.2" stroke-linecap="round" '
            f'opacity="0.6" />'
        )
    return "".join(out)


TRUNK_DECORATIONS = {
    "plain":  lambda *args: "",
    "bamboo": _trunk_bamboo,
    "birch":  _trunk_birch,
}

"""Per-theme SVG renderers + shared canvas / palette tokens.

One renderer per theme. Each renderer is a function
``(state, ctx) -> str`` that returns just the *tree body* -- the
dispatcher in ``svg_render.state_to_svg`` wraps it with the SVG
envelope, defs, sky, ground, and pot.

Twelve renderers in total: generic (default), bamboo (python),
pine (rust), oak (go), willow (javascript), willow_ts (typescript),
sakura (swift), maple (ruby), old_oak (c, cpp), banyan (java),
ginkgo (haskell), birch (zig).
"""

from __future__ import annotations

from collections.abc import Callable

from bonsai_cc.growth.state import TreeState
from bonsai_cc.web.render.bamboo import render_bamboo
from bonsai_cc.web.render.banyan import render_banyan
from bonsai_cc.web.render.birch import render_birch
from bonsai_cc.web.render.bonsai import render_generic_bonsai
from bonsai_cc.web.render.canvas import CanvasCtx
from bonsai_cc.web.render.ginkgo import render_ginkgo
from bonsai_cc.web.render.maple import render_maple
from bonsai_cc.web.render.oak import render_oak
from bonsai_cc.web.render.old_oak import render_old_oak
from bonsai_cc.web.render.pine import render_pine
from bonsai_cc.web.render.sakura import render_sakura
from bonsai_cc.web.render.willow import render_willow
from bonsai_cc.web.render.willow_ts import render_willow_ts

ThemeRenderer = Callable[[TreeState, CanvasCtx], str]


THEME_RENDERERS: dict[str, ThemeRenderer] = {
    "default": render_generic_bonsai,
    "python": render_bamboo,
    "rust": render_pine,
    "go": render_oak,
    "javascript": render_willow,
    "typescript": render_willow_ts,
    "swift": render_sakura,
    "ruby": render_maple,
    "c": render_old_oak,
    "cpp": render_old_oak,
    "java": render_banyan,
    "haskell": render_ginkgo,
    "zig": render_birch,
}


# Theme display name → language key for renderer lookup. The
# theme-override picker uses display names (``sakura``, ``bamboo``,
# ``ginkgo``) because they read naturally in URLs like
# ``?theme=sakura``; the registry above is keyed on the language
# that auto-detection picks. Both names route to the same renderer.
THEME_DISPLAY_NAMES: dict[str, str] = {
    "bamboo": "python",
    "pine": "rust",
    "oak": "go",
    "willow": "javascript",
    "willow_ts": "typescript",
    "sakura": "swift",
    "maple": "ruby",
    "old_oak": "c",
    "banyan": "java",
    "ginkgo": "haskell",
    "birch": "zig",
    "generic": "default",
}


def render_for_theme(theme: str) -> ThemeRenderer:
    """Pick a renderer for ``theme`` with a sensible fallback.

    Themes outside the registry fall back to ``render_generic_bonsai``
    -- the neutral, never-stands-out, never-looks-bad shape.
    """
    return THEME_RENDERERS.get(theme, render_generic_bonsai)


def resolve_theme_override(name: str | None) -> str | None:
    """Map a theme-override input to a renderer key.

    Accepts either the display name (``"sakura"``) or the
    underlying language key (``"swift"``). Returns the language
    key on a successful match, ``None`` if the input is missing
    or unrecognised -- the latter so callers can fall back to the
    state's detected theme.
    """
    if not name:
        return None
    if name in THEME_DISPLAY_NAMES:
        return THEME_DISPLAY_NAMES[name]
    if name in THEME_RENDERERS:
        return name
    return None


__all__ = [
    "THEME_DISPLAY_NAMES",
    "THEME_RENDERERS",
    "CanvasCtx",
    "ThemeRenderer",
    "render_bamboo",
    "render_banyan",
    "render_birch",
    "render_for_theme",
    "render_generic_bonsai",
    "render_ginkgo",
    "render_maple",
    "render_oak",
    "render_old_oak",
    "render_pine",
    "render_sakura",
    "render_willow",
    "render_willow_ts",
    "resolve_theme_override",
]

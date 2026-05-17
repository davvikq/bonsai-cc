"""Server-side SVG renderer: dispatches by theme, wraps with envelope.

Coordinate system: viewport ``0 0 1000 800``, trunk base at
``(500, 600)``, ``UNIT`` SVG pixels per logical unit, logical y
grows upward (SVG y grows downward). Per-theme drawing lives in
:mod:`bonsai_cc.web.render`; all colours flow through
:mod:`bonsai_cc.web.render.tokens`.
"""

from __future__ import annotations

from dataclasses import dataclass

from bonsai_cc.growth.state import TreeState
from bonsai_cc.web.render import canvas as _canvas
from bonsai_cc.web.render import render_for_theme, resolve_theme_override

__all__ = [
    "ORIGIN_X",
    "ORIGIN_Y",
    "UNIT",
    "VB_H",
    "VB_W",
    "RenderConfig",
    "project_xy",
    "state_to_svg",
]


# Re-export the canvas constants under the legacy names so tests
# and the JS-mirroring geometry don't need to track an import move.
VB_W = _canvas.VB_W
VB_H = _canvas.VB_H
ORIGIN_X = _canvas.ORIGIN_X
ORIGIN_Y = _canvas.ORIGIN_Y
UNIT = _canvas.UNIT
project_xy = _canvas.project_xy


@dataclass(frozen=True, slots=True)
class RenderConfig:
    """Legacy knob bag -- kept for callers that still construct it.

    Current renderers consume tokens and per-theme constants
    directly; the values here only affect callers that don't go
    through ``state_to_svg``.
    """

    trunk_base_width: float = 30.0
    trunk_top_width: float = 6.0
    branch_base_width: float = 11.0
    branch_tip_width: float = 2.5
    root_base_width: float = 10.0
    root_tip_width: float = 2.0
    leaf_radius: float = 5.0
    flower_radius: float = 9.0
    offshoot_width: float = 4.0
    sun_radius: float = 30.0
    moon_radius: float = 28.0


def state_to_svg(
    state: TreeState,
    *,
    width: int = VB_W,
    height: int = VB_H,
    cfg: RenderConfig | None = None,
    now_hour: int | None = None,
    theme: str = "light",
    theme_override: str | None = None,
) -> str:
    """Render ``state`` as a full SVG document string.

    ``now_hour`` (0-23) drives the time-of-day sky band. When
    omitted, defaults to ``12`` (day) so headless tests are
    independent of the wall clock.

    ``theme`` (``"light"`` or ``"dark"``) swaps the sky atmosphere
    so the tree doesn't read as a sunlit window punched into a
    dark page. Dark sky uses ``NIGHT_DEEP -> NIGHT``; the sun is
    replaced by a soft moon glow at upper-left. Tree fills are
    theme-agnostic: the warm trunk/leaf palette works on both
    atmospheres without recolouring.

    ``theme_override`` forces a specific bonsai-theme renderer
    regardless of ``state.theme`` (which is the auto-detected
    project language). Pass either a display name
    (``"sakura"``, ``"bamboo"``) or a language key (``"swift"``,
    ``"python"``). Unknown values silently fall back to
    ``state.theme`` -- invalid URLs shouldn't crash the daemon.

    ``cfg`` is accepted for backwards compatibility but only
    affects the legacy callers that pass it; the per-theme renderers
    consume tokens directly.
    """
    _ = cfg  # ignored -- renderers consume tokens directly
    hour = 12 if now_hour is None else now_hour
    ctx = _canvas.CanvasCtx(hour=hour, theme=theme)
    resolved = resolve_theme_override(theme_override)
    effective_theme = resolved if resolved is not None else state.theme
    renderer = render_for_theme(effective_theme)
    body = renderer(state, ctx)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="bonsai-cc tree">'
        + _canvas.build_defs(hour, theme=theme)
        + _canvas.build_sky()
        + _canvas.build_celestial(hour, theme=theme)
        + _canvas.build_ground()
        + _canvas.build_pot()
        + body
        + "</svg>"
    )

"""Seasonal + time-of-day overlays (DESIGN.md §2.5).

Render-time only: this module is **never** allowed to mutate
``TreeState``. Replay determinism depends on state being a pure
function of the events; overlays change with the wall clock and
must stay outside the state.

What it does
------------
Given a state and a "now" timestamp, return an :class:`Overlay`
that the renderer paints on top of the projected grid:

* a *season* (spring / summer / autumn / winter) chosen by session
  duration -- only after the first hour, so short sessions stay
  neutral;
* a *time of day* (night / dawn / day / dusk) chosen by the hour
  hand of the current wall clock;
* a small set of *ambient cells* to sprinkle around the canvas
  (moon and fireflies at night, dew at dawn, snowflakes in winter).

Ambient cell positions are **deterministic from the session seed +
the current minute**, not random. Two renders within the same
minute produce the same ambient pattern; the pattern shifts each
minute so the canopy never feels frozen.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bonsai_cc.growth.state import TreeState
from bonsai_cc.render.palette import Palette, palette_for

__all__ = [
    "AmbientCell",
    "Overlay",
    "compute_overlay",
    "pick_season",
    "pick_time_of_day",
]


# Hours-into-session thresholds for the seasonal march.
_SPRING_AFTER_H = 1.0
_SUMMER_AFTER_H = 2.0
_AUTUMN_AFTER_H = 4.0
_WINTER_AFTER_H = 8.0


@dataclass(frozen=True, slots=True)
class AmbientCell:
    """One overlay glyph to paint over the projected grid.

    Coordinates are *logical* (same coordinate system as
    :mod:`bonsai_cc.growth.state` -- y increases upward). The canvas
    re-projects them at render time so resize behaves naturally.
    """

    x: int
    y: int
    glyph: str
    color: str | None = None


@dataclass(frozen=True, slots=True)
class Overlay:
    season: str | None
    time_of_day: str
    ambient: tuple[AmbientCell, ...] = field(default_factory=tuple)


def pick_season(duration_h: float) -> str | None:
    """Map hours-since-start to a season name (or None if too young).

    Within the first hour we don't apply any seasonal effect -- fresh
    trees look like fresh trees. After 8h we leave the canopy in
    winter (the user's been running a marathon session and we want
    them to see their bare-elegant tree).
    """
    if duration_h < _SPRING_AFTER_H:
        return None
    if duration_h < _SUMMER_AFTER_H:
        return "spring"
    if duration_h < _AUTUMN_AFTER_H:
        return "summer"
    if duration_h < _WINTER_AFTER_H:
        return "autumn"
    return "winter"


def pick_time_of_day(now: datetime) -> str:
    """Bucket an absolute moment into one of four bands.

    Bands match the design's specification verbatim::

        night  22:00-05:00
        dawn   05:00-08:00
        day    08:00-18:00
        dusk   18:00-22:00
    """
    h = now.hour
    if h >= 22 or h < 5:
        return "night"
    if h < 8:
        return "dawn"
    if h < 18:
        return "day"
    return "dusk"


def compute_overlay(
    state: TreeState,
    *,
    now: datetime | None = None,
) -> Overlay:
    """Pure overlay computation for a state at moment ``now``.

    ``now`` defaults to :func:`datetime.now` in UTC so production
    callers don't have to thread it. Tests inject a fixed timestamp
    to assert overlay decisions without flakiness.
    """
    now = now or datetime.now(UTC)
    duration_h = _duration_h(state, now)
    season = pick_season(duration_h)
    time_of_day = pick_time_of_day(now)
    palette = palette_for(state.theme)
    ambient = _ambient_cells(state, palette, season, time_of_day, now)
    return Overlay(season=season, time_of_day=time_of_day, ambient=ambient)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _duration_h(state: TreeState, now: datetime) -> float:
    if state.started_at_ms <= 0:
        return 0.0
    now_ms = int(now.timestamp() * 1000)
    return max(0.0, (now_ms - state.started_at_ms) / 1000.0 / 3600.0)


def _ambient_cells(
    state: TreeState,
    palette: Palette,
    season: str | None,
    time_of_day: str,
    now: datetime,
) -> tuple[AmbientCell, ...]:
    """Build the small overlay glyph set.

    Determinism: position hash takes ``(session seed, current
    minute, slot index)`` so the layout is stable within a minute
    and shifts predictably on the next.
    """
    cells: list[AmbientCell] = []
    minute_bucket = int(now.replace(second=0, microsecond=0).timestamp() // 60)
    seed_bytes = bytes.fromhex(state.seed_hex) if _all_hex(state.seed_hex) else b"\0"
    seed_int = int.from_bytes(seed_bytes, "big") if seed_bytes else 0

    # Time-of-day ambient. Every band gets *something* visible --
    # earlier releases left "day" empty, which made the sky look
    # broken even though the renderer was fine.
    if time_of_day == "night":
        # Moon at upper-left, three fireflies sprinkled.
        cells.append(AmbientCell(x=-10, y=18, glyph="☾", color="#F5F5DC"))
        for slot in range(3):
            x, y = _pos(seed_int, minute_bucket, slot, span_x=18, span_y=14, baseline_y=4)
            cells.append(AmbientCell(x=x, y=y, glyph="·", color="#F0E68C"))
    elif time_of_day == "dawn":
        for slot in range(2):
            x, y = _pos(seed_int, minute_bucket, slot + 100, span_x=14, span_y=10, baseline_y=2)
            cells.append(AmbientCell(x=x, y=y, glyph="'", color="#B0E0E6"))
    elif time_of_day == "day":
        # A sun sits in the upper-right canopy area. Stable
        # within the session so it never appears to wander.
        cells.append(AmbientCell(x=10, y=18, glyph="☀", color="#FFD700"))
    elif time_of_day == "dusk":
        # Two warm sparks, mirroring dawn's two dew droplets.
        for slot in range(2):
            x, y = _pos(seed_int, minute_bucket, slot + 500, span_x=14, span_y=10, baseline_y=4)
            cells.append(AmbientCell(x=x, y=y, glyph="*", color="#FF8C00"))

    # Season ambient -- falls before time-of-day so it's foregrounded.
    if season == "spring":
        for slot in range(3):
            x, y = _pos(seed_int, minute_bucket, slot + 200, span_x=16, span_y=12, baseline_y=6)
            cells.append(AmbientCell(x=x, y=y, glyph="*", color=palette.flower))
    elif season == "autumn":
        for slot in range(2):
            x, y = _pos(seed_int, minute_bucket, slot + 300, span_x=14, span_y=8, baseline_y=2)
            cells.append(AmbientCell(x=x, y=y, glyph=".", color=palette.leaf_dim))
    elif season == "winter":
        for slot in range(4):
            x, y = _pos(seed_int, minute_bucket, slot + 400, span_x=18, span_y=14, baseline_y=4)
            cells.append(AmbientCell(x=x, y=y, glyph="❄", color="#E0FFFF"))

    return tuple(cells)


def _pos(
    seed: int, minute: int, slot: int, *, span_x: int, span_y: int, baseline_y: int
) -> tuple[int, int]:
    """Hash to ``(x, y)`` for one ambient glyph.

    Two slots in the same ``(seed, minute)`` bucket land at the
    same position iff their ``slot`` indices collide on the hash
    -- which they don't, by construction. Position drift across
    minutes is the source of the gentle "alive" feel.
    """
    payload = f"{seed}:{minute}:{slot}".encode()
    digest = hashlib.blake2b(payload, digest_size=4).digest()
    dx = (int.from_bytes(digest[:2], "big") % (span_x * 2 + 1)) - span_x
    dy = (digest[2] % (span_y + 1)) + baseline_y
    return dx, dy


def _all_hex(s: str) -> bool:
    if not s:
        return False
    try:
        int(s, 16)
    except ValueError:
        return False
    return True

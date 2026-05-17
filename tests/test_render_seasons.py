"""Overlay computation: seasonal bands, time-of-day bands, determinism."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from bonsai_cc.growth.state import TreeState, demo_tree
from bonsai_cc.render.seasons import (
    Overlay,
    compute_overlay,
    pick_season,
    pick_time_of_day,
)

# ---------------------------------------------------------------------------
# Season buckets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "duration_h,expected",
    [
        (0.5, None),
        (1.5, "spring"),
        (3.0, "summer"),
        (5.5, "autumn"),
        (12.0, "winter"),
    ],
)
def test_pick_season(duration_h: float, expected: str | None) -> None:
    assert pick_season(duration_h) == expected


# ---------------------------------------------------------------------------
# Time-of-day buckets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hour,expected",
    [
        (0, "night"), (4, "night"), (5, "dawn"), (7, "dawn"),
        (8, "day"), (15, "day"), (17, "day"),
        (18, "dusk"), (21, "dusk"),
        (22, "night"), (23, "night"),
    ],
)
def test_pick_time_of_day(hour: int, expected: str) -> None:
    now = datetime(2026, 5, 14, hour, 30, tzinfo=UTC)
    assert pick_time_of_day(now) == expected


# ---------------------------------------------------------------------------
# Overlay assembly
# ---------------------------------------------------------------------------


def _state_with_age(hours: float) -> TreeState:
    state = demo_tree("overlay")
    # Place ``started_at`` so the "now" we pass below sits ``hours`` later.
    now = int(datetime(2026, 5, 14, 12, 0, tzinfo=UTC).timestamp() * 1000)
    return replace(state, started_at_ms=now - int(hours * 3600 * 1000))


def test_overlay_returns_known_shape() -> None:
    state = _state_with_age(0.0)
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    overlay = compute_overlay(state, now=now)
    assert isinstance(overlay, Overlay)
    assert overlay.season is None  # session too young
    assert overlay.time_of_day == "day"
    # Day used to return zero ambient cells; that left the sky
    # entirely blank. Every band now carries at least one glyph.
    assert overlay.ambient != ()


def test_night_adds_moon_and_fireflies() -> None:
    state = _state_with_age(0.0)
    now = datetime(2026, 5, 14, 23, 30, tzinfo=UTC)
    overlay = compute_overlay(state, now=now)
    assert overlay.time_of_day == "night"
    glyphs = [c.glyph for c in overlay.ambient]
    assert "☾" in glyphs
    assert glyphs.count("·") == 3  # three fireflies


def test_dawn_adds_dew() -> None:
    state = _state_with_age(0.0)
    now = datetime(2026, 5, 14, 6, 30, tzinfo=UTC)
    overlay = compute_overlay(state, now=now)
    assert overlay.time_of_day == "dawn"
    glyphs = [c.glyph for c in overlay.ambient]
    assert "'" in glyphs


def test_winter_adds_snowflakes() -> None:
    state = _state_with_age(10.0)
    now = datetime(2026, 5, 14, 14, 0, tzinfo=UTC)
    overlay = compute_overlay(state, now=now)
    assert overlay.season == "winter"
    glyphs = [c.glyph for c in overlay.ambient]
    assert glyphs.count("❄") == 4


def test_overlay_is_stable_within_a_minute() -> None:
    """Two calls in the same minute → identical ambient layout."""
    state = _state_with_age(10.0)
    a = compute_overlay(state, now=datetime(2026, 5, 14, 23, 30, 5, tzinfo=UTC))
    b = compute_overlay(state, now=datetime(2026, 5, 14, 23, 30, 45, tzinfo=UTC))
    assert a.ambient == b.ambient


def test_overlay_shifts_between_minutes() -> None:
    """Next minute → at least one cell moved (the whole point of the
    'alive' feel)."""
    state = _state_with_age(10.0)
    a = compute_overlay(state, now=datetime(2026, 5, 14, 23, 30, 0, tzinfo=UTC))
    b = compute_overlay(state, now=datetime(2026, 5, 14, 23, 31, 0, tzinfo=UTC))
    assert a.ambient != b.ambient
    # Same number of cells though (deterministic count).
    assert len(a.ambient) == len(b.ambient)


def test_day_now_carries_a_sun() -> None:
    """Empty sky at noon was the bug — every band needs presence."""
    state = _state_with_age(0.0)
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    overlay = compute_overlay(state, now=now)
    glyphs = [c.glyph for c in overlay.ambient]
    assert "☀" in glyphs


def test_dusk_carries_warm_sparks() -> None:
    state = _state_with_age(0.0)
    now = datetime(2026, 5, 14, 19, 0, tzinfo=UTC)
    overlay = compute_overlay(state, now=now)
    glyphs = [c.glyph for c in overlay.ambient]
    # Two warm sparks.
    assert glyphs.count("*") == 2

"""Aggregate-stats math for the web garden hero band.

The streak rule is subtle enough to deserve unit tests at the
"every edge case in the brief" level: timezone boundaries, gap of
exactly 24 h, sessions spanning midnight, the "today not yet
counted" carve-out.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from bonsai_cc.garden.stats import (
    GardenStats,
    SessionTime,
    compute_stats,
    compute_streak,
)


def _at(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> int:
    """A wall-clock-local epoch ms helper. The stats module uses
    local-timezone date conversion, so we construct the timestamps
    via ``datetime`` (no tzinfo) → ``timestamp()``."""
    return int(datetime(year, month, day, hour, minute).timestamp() * 1000)


def _ses(
    started_at_ms: int, ended_at_ms: int | None = None, *, status: str = "complete"
) -> SessionTime:
    return SessionTime(
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms if ended_at_ms is not None else started_at_ms + 60_000,
        status=status,
    )


# ---------------------------------------------------------------------------
# Streak — happy paths
# ---------------------------------------------------------------------------


def test_streak_empty_garden_is_zero() -> None:
    assert compute_streak([], now=datetime(2026, 5, 15, 12, 0)) == 0


def test_streak_today_only_is_one() -> None:
    sessions = [_ses(_at(2026, 5, 15, 9, 0))]
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 18, 0)) == 1


def test_streak_today_and_yesterday_is_two() -> None:
    sessions = [
        _ses(_at(2026, 5, 14, 11, 0)),
        _ses(_at(2026, 5, 15, 11, 0)),
    ]
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 18, 0)) == 2


def test_streak_runs_back_until_first_gap() -> None:
    sessions = [
        _ses(_at(2026, 5, 11)),  # day -4
        _ses(_at(2026, 5, 12)),  # day -3
        _ses(_at(2026, 5, 13)),  # day -2
        _ses(_at(2026, 5, 14)),  # day -1
        _ses(_at(2026, 5, 15)),  # today
    ]
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 18, 0)) == 5


def test_streak_breaks_on_missing_day() -> None:
    sessions = [
        _ses(_at(2026, 5, 12)),
        _ses(_at(2026, 5, 13)),
        # 14 missing!
        _ses(_at(2026, 5, 15)),
    ]
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 18, 0)) == 1


# ---------------------------------------------------------------------------
# The "today not yet counted" carve-out
# ---------------------------------------------------------------------------


def test_streak_no_session_today_but_yesterday_keeps_count() -> None:
    """It's morning. The user hasn't started a session today yet.
    They have one from yesterday. The streak count should still
    show the yesterday-ending tail — encourages the user to start
    today's session."""
    sessions = [
        _ses(_at(2026, 5, 13, 10, 0)),
        _ses(_at(2026, 5, 14, 11, 0)),
    ]
    # ``now`` is 2026-05-15 07:00 — early morning, no session today.
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 7, 0)) == 2


def test_streak_no_session_today_or_yesterday_is_zero() -> None:
    """Two-day gap is enough to break the streak — even an
    otherwise-rich history doesn't keep it alive."""
    sessions = [
        _ses(_at(2026, 5, 10)),
        _ses(_at(2026, 5, 11)),
        _ses(_at(2026, 5, 12)),
        _ses(_at(2026, 5, 13)),
    ]
    # Now is May 15: 14 (silent) + 15 (silent) → 2-day gap.
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 18, 0)) == 0


# ---------------------------------------------------------------------------
# Tricky edges: gap of exactly 24 h, session spanning midnight,
# clusters within one day.
# ---------------------------------------------------------------------------


def test_streak_gap_of_exactly_24h_calendar_aware() -> None:
    """24-hour wall-clock gap does NOT necessarily break the streak;
    the rule is about *calendar days*. A session at Mon 23:50 and
    another at Tue 23:55 are two distinct calendar days even though
    only ~24 h separates them."""
    sessions = [
        _ses(_at(2026, 5, 12, 23, 50)),
        _ses(_at(2026, 5, 13, 23, 55)),
        _ses(_at(2026, 5, 14, 12, 0)),
        _ses(_at(2026, 5, 15, 9, 0)),
    ]
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 18, 0)) == 4


def test_streak_session_spanning_midnight_counts_for_start_day() -> None:
    """A session that starts at 23:30 and ends at 01:15 next day
    contributes to the start day only — keeps the rule
    unambiguous."""
    # The session starts 5/13 23:30 and ends 5/14 01:15. We have
    # no other session on 5/14 — so the streak ending today
    # (5/15) requires 5/15, 5/14, 5/13 to all be days_with_activity.
    sessions = [
        _ses(_at(2026, 5, 13, 23, 30), ended_at_ms=_at(2026, 5, 14, 1, 15)),
        _ses(_at(2026, 5, 15, 11, 0)),
    ]
    # Streak: today (5/15) yes, yesterday (5/14) NO (only end-time
    # there, start was on 5/13) → streak is 1.
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 18, 0)) == 1


def test_streak_cluster_within_one_day_counts_as_one() -> None:
    """Three sessions in one calendar day is one day toward the
    streak — not three."""
    sessions = [
        _ses(_at(2026, 5, 15, 9, 0)),
        _ses(_at(2026, 5, 15, 12, 0)),
        _ses(_at(2026, 5, 15, 16, 0)),
    ]
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 18, 0)) == 1


def test_streak_future_session_counted_toward_today() -> None:
    """Clock skew defence: a session whose start is after ``now``
    still counts for today (the system clock on the daemon's
    machine is authoritative; this only matters if it's wrong)."""
    sessions = [
        _ses(_at(2026, 5, 15, 23, 50)),  # technically future at now=18:00
    ]
    assert compute_streak(sessions, now=datetime(2026, 5, 15, 18, 0)) == 1


# ---------------------------------------------------------------------------
# Aggregate stats: total time, count, this-month
# ---------------------------------------------------------------------------


def test_total_time_excludes_sub_10s_sessions() -> None:
    """Sessions under 10 s are noise (misfired hooks, accidental
    Claude Code window opens). The hero band drops them from the
    total-time sum."""
    sessions = [
        # 5 s — under threshold, dropped.
        _ses(_at(2026, 5, 14, 10, 0), ended_at_ms=_at(2026, 5, 14, 10, 0) + 5_000),
        # 30 s — kept.
        _ses(_at(2026, 5, 14, 11, 0), ended_at_ms=_at(2026, 5, 14, 11, 0) + 30_000),
        # 1 h — kept.
        _ses(_at(2026, 5, 14, 12, 0), ended_at_ms=_at(2026, 5, 14, 13, 0)),
    ]
    stats = compute_stats(sessions, now=datetime(2026, 5, 14, 18, 0))
    # 30 s + 3600 s = 3630 s.
    assert stats.total_seconds == 3630


def test_total_time_ignores_unended_sessions() -> None:
    """Active (no ended_at) and aborted sessions don't contribute
    a duration."""
    sessions = [
        SessionTime(started_at_ms=_at(2026, 5, 15, 10, 0), ended_at_ms=None, status="partial"),
        _ses(_at(2026, 5, 14), ended_at_ms=_at(2026, 5, 14) + 600_000),
    ]
    stats = compute_stats(sessions, now=datetime(2026, 5, 15, 18, 0))
    assert stats.total_seconds == 600


def test_sessions_count_includes_every_row() -> None:
    sessions = [_ses(_at(2026, 5, i)) for i in range(1, 9)]
    stats = compute_stats(sessions, now=datetime(2026, 5, 15, 18, 0))
    assert stats.sessions_count == 8


def test_sessions_this_month_filter() -> None:
    """Only sessions whose START date falls in the current calendar
    month count. April sessions don't contribute to a May figure."""
    sessions = [
        _ses(_at(2026, 4, 29)),  # last week of April — outside
        _ses(_at(2026, 4, 30)),  # last day of April — outside
        _ses(_at(2026, 5, 1)),   # first day of May — inside
        _ses(_at(2026, 5, 15)),  # mid-May — inside
    ]
    stats = compute_stats(sessions, now=datetime(2026, 5, 15, 18, 0))
    assert stats.sessions_this_month == 2
    assert stats.sessions_count == 4


def test_garden_stats_is_a_frozen_dataclass() -> None:
    """The return type is meant to be immutable so the route handler
    can hand it off to the JSON encoder safely."""
    stats = GardenStats(
        total_seconds=10, sessions_count=1, sessions_this_month=1, streak_days=1,
    )
    import dataclasses

    assert dataclasses.is_dataclass(stats)
    try:
        stats.total_seconds = 99  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("GardenStats should be frozen")


_ = timedelta  # imported for the docstring's edge-case description

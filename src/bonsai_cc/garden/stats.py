"""Aggregate stats for the web garden hero band.

Three numbers + a couple of secondary lines:

* **Total time** -- sum of session durations, excluding noise
  (anything under 10 seconds).
* **Sessions** -- count of saved rows. Secondary line: count in the
  current calendar month (user's local timezone).
* **Current streak** -- consecutive calendar days with at least one
  session, ending today or yesterday. Today is not counted yet if no
  session has run today, so the streak feels motivating in the
  morning instead of accusatory.

Local-timezone awareness is essential here: a session that starts at
23:45 belongs to that calendar day, not the UTC day. We use
``datetime.fromtimestamp(... )`` (no tzinfo) so the conversion
honours the server's local clock -- which on bonsai-cc's
single-user, runs-on-your-laptop deployment is also the user's
clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import NamedTuple

__all__ = [
    "MIN_DURATION_S",
    "GardenStats",
    "SessionTime",
    "compute_stats",
    "compute_streak",
]


# Sessions shorter than this are dropped from the "total time" sum.
# Short = noise: misfired hooks, a Claude Code window that opened
# briefly and closed, accidental keystrokes.
MIN_DURATION_S = 10.0


class SessionTime(NamedTuple):
    """The slice of a session row this module cares about.

    Kept narrow on purpose -- the stats module shouldn't depend on
    the full ``SessionRow`` dataclass. Callers extract the three
    fields and pass them in.
    """

    started_at_ms: int
    ended_at_ms: int | None
    status: str  # ``complete`` / ``partial`` / ``recovered``


@dataclass(frozen=True, slots=True)
class GardenStats:
    """Hero-band values plus the secondary lines."""

    total_seconds: int          # sum of complete-session durations ≥ 10 s
    sessions_count: int         # all saved sessions, any status
    sessions_this_month: int    # sessions whose START day is in current month
    streak_days: int            # consecutive calendar days with ≥1 session


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_stats(
    sessions: list[SessionTime],
    *,
    now: datetime | None = None,
) -> GardenStats:
    """Compute hero-band stats from a list of session times.

    ``now`` is the reference instant; defaults to wall-clock at call
    time. Tests pin it to a deterministic value.
    """
    reference = now or datetime.now()
    total_seconds = 0
    for s in sessions:
        if s.ended_at_ms is None:
            # Active or aborted-without-end. Don't count toward
            # total time -- the duration is unknown.
            continue
        dur_s = (s.ended_at_ms - s.started_at_ms) / 1000.0
        if dur_s < MIN_DURATION_S:
            continue
        total_seconds += int(dur_s)

    sessions_count = len(sessions)
    sessions_this_month = sum(
        1
        for s in sessions
        if _local_date_of(s.started_at_ms).year == reference.year
        and _local_date_of(s.started_at_ms).month == reference.month
    )
    streak_days = compute_streak(sessions, now=reference)
    return GardenStats(
        total_seconds=total_seconds,
        sessions_count=sessions_count,
        sessions_this_month=sessions_this_month,
        streak_days=streak_days,
    )


# ---------------------------------------------------------------------------
# Streak math
# ---------------------------------------------------------------------------


def compute_streak(
    sessions: list[SessionTime],
    *,
    now: datetime | None = None,
) -> int:
    """Consecutive calendar days with ≥1 session, ending today or yesterday.

    Rules:

    * "Today" is the local calendar day of ``now``.
    * A session counts toward the day it *started* (local time). A
      session that runs across midnight contributes to the start day
      only -- keeps the rule unambiguous.
    * If no session has started today, the streak is allowed to end
      *yesterday*: the streak survives the morning before today's
      first session.
    * If no session has started today OR yesterday, the streak is
      0. A two-day gap breaks it cleanly.
    """
    reference = now or datetime.now()
    today = reference.date()

    if not sessions:
        return 0

    days_with_activity: set[date] = {
        _local_date_of(s.started_at_ms) for s in sessions
    }

    # Pick the streak's tail day. "Ending today or yesterday."
    if today in days_with_activity:
        tail = today
    elif (today - timedelta(days=1)) in days_with_activity:
        tail = today - timedelta(days=1)
    else:
        return 0

    # Walk backwards day-by-day; stop at the first gap.
    streak = 0
    cursor = tail
    while cursor in days_with_activity:
        streak += 1
        cursor = cursor - timedelta(days=1)
    return streak


def _local_date_of(epoch_ms: int) -> date:
    """Convert ``epoch_ms`` to the user's local calendar date.

    ``datetime.fromtimestamp(secs)`` with no tz argument uses the
    server's local timezone, which on a single-user laptop is also
    the user's. The garden is single-user (DESIGN.md §0).
    """
    return datetime.fromtimestamp(epoch_ms / 1000.0).date()

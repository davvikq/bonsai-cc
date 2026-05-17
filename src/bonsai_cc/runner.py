"""Pipeline wiring: event bus → growth engine → renderer sink.

This module imports from :mod:`bonsai_cc.events` and
:mod:`bonsai_cc.growth`. The architectural seam
keeps the growth layer from reaching into event production; this
module is where the wiring lives, and the seam test deliberately
permits it because it isn't inside ``growth/`` or ``render/``.

What lives here:

* :class:`GrowthRunner` -- consumes ingested events from
  :mod:`bonsai_cc.events.bus`, calls
  :func:`bonsai_cc.growth.apply.apply_event`, and pushes the new
  ``TreeState`` into the renderer sink (a duck-typed
  ``set_state(state, *, last_event_name=...)`` target -- the web
  broadcaster, or a test recorder).
* :func:`replay_journal_into_bus` -- feeds an on-disk JSONL journal
  through the bus so the renderer can be driven without a live
  Claude Code hook, for demos and tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bonsai_cc.config import Config, get_config
from bonsai_cc.events.bus import (
    EventBus,
    IngestedEvent,
    get_event_bus,
)
from bonsai_cc.events.journal import JournalRegistry
from bonsai_cc.events.models import (
    PostToolUseEvent,
    PostToolUseFailureEvent,
    SessionEndEvent,
    parse_event,
)
from bonsai_cc.garden.store import GardenStore, SessionStatus
from bonsai_cc.growth.apply import apply_all, apply_event
from bonsai_cc.growth.language import detect_language
from bonsai_cc.growth.state import TreeState
from bonsai_cc.log import get_logger

__all__ = [
    "DEFAULT_IDLE_TIMEOUT_S",
    "DEFAULT_PARTIAL_SAVE_EVERY_N",
    "DEFAULT_TIMER_TICK_S",
    "GrowthRunner",
    "build_initial_state",
    "count_orphan_journals",
    "ensure_garden_consistent",
    "recover_orphan_sessions",
    "replay_journal_into_bus",
]


_log = get_logger("bonsai_cc.runner")


# Persistence cadence (the design contract + the user's
# "data-loss-by-default" bug report).
#
# - Every N events, write a partial snapshot. This guarantees that
#   even a SIGKILL of the daemon loses at most N-1 events worth of
#   "completed" state -- the journal still has every event for full
#   recovery on the next start.
# - Every M seconds with no further events, treat the session as
#   over and finalise it as complete. Claude Code on Windows doesn't
#   reliably fire SessionEnd; the idle timeout closes that hole.
# - The timer ticks every TIMER_TICK_S to check both conditions.
#
# These defaults are kwargs on GrowthRunner so tests can shrink them.
DEFAULT_PARTIAL_SAVE_EVERY_N = 10
DEFAULT_IDLE_TIMEOUT_S = 300.0
DEFAULT_TIMER_TICK_S = 10.0


# ---------------------------------------------------------------------------
# State bootstrap (mirrors apply._initial_state -- kept here so the
# runner can construct state without reaching into apply's private
# helpers).
# ---------------------------------------------------------------------------


def build_initial_state(
    session_id: str,
    *,
    theme: str = "default",
    started_at_ms: int | None = None,
) -> TreeState:
    """Construct a fresh ``TreeState`` for a new session.

    Mirrors the seed-derivation in :mod:`bonsai_cc.growth.apply` so
    the runner can mint a state when it sees the first event for a
    session id. The ``apply_all`` helper is a one-shot equivalent
    that we can't easily use here because the runner folds events
    in one at a time as they arrive.
    """
    from bonsai_cc.growth.lsystem import seed_from_session_id  # local: avoid cycles

    seed = seed_from_session_id(session_id)
    return TreeState(
        session_id=session_id,
        seed_hex=f"{seed:016x}",
        started_at_ms=(
            started_at_ms
            if started_at_ms is not None
            else int(datetime.now(UTC).timestamp() * 1000)
        ),
        theme=theme,
    )


# ---------------------------------------------------------------------------
# Growth runner: consume bus, apply, update app
# ---------------------------------------------------------------------------


class GrowthRunner:
    """Pulls ingested events off the bus and updates the renderer.

    Owns a single ``TreeState`` for the *first* session we see in
    this process. Events tagged with other session ids are logged
    and dropped -- the design contract punts multi-renderer support to v2.
    A future ``--session <id>`` flag will let users pick which
    session to follow; for now the first one wins.
    """

    def __init__(
        self,
        app: Any,
        bus: EventBus | None = None,
        *,
        theme: str = "default",
        garden: GardenStore | None = None,
        journals: JournalRegistry | None = None,
        partial_save_every_n: int = DEFAULT_PARTIAL_SAVE_EVERY_N,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        timer_tick_s: float = DEFAULT_TIMER_TICK_S,
    ) -> None:
        self._app = app
        self._bus = bus or get_event_bus()
        self._theme = theme
        self._garden = garden
        self._journals = journals
        self._partial_save_every_n = partial_save_every_n
        self._idle_timeout_s = idle_timeout_s
        self._timer_tick_s = timer_tick_s
        self._task: asyncio.Task[None] | None = None
        self._timer_task: asyncio.Task[None] | None = None
        self._state: TreeState | None = None
        self._session_id: str | None = None
        self._project_path: str | None = None
        self._detected_language: str | None = None
        self._saved_on_session_end: bool = False
        self._events_since_partial_save: int = 0
        self._last_event_monotonic: float | None = None
        # Latest journal ts seen for the active session. Used as the
        # ``ended_at_ms`` on garden saves so duration accounting
        # reflects when events ACTUALLY happened (the original hook-
        # write timestamps), not when the daemon got around to
        # processing them. Without this, orphan-journal replay
        # produces zero-duration garden rows because state.started_at
        # and now() collapse into the same processing instant.
        self._latest_event_ts_ms: int | None = None
        # Per-tool counts for the web sidebar. Incremented on every
        # PostToolUse / PostToolUseFailure event. Keys are
        # ``tool_name`` strings (Bash, Edit, Write, …); values are
        # cumulative counts for the session.
        self._tool_counts: dict[str, int] = {}
        # Cache of session-id → "already finalised in garden?" lookups
        # so the per-event check (used to skip journal backlog for
        # sessions a previous daemon run already saved) is O(1) after
        # the first miss instead of a SQLite hit per event.
        self._completed_session_cache: dict[str, bool] = {}

    @property
    def state(self) -> TreeState | None:
        """The current state, if we've seen any events for our session."""
        return self._state

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def current_live_session_id(self) -> str | None:
        """The session_id of an actively-tracked live session, or None.

        Returns the bound session id only while the session is in
        flight -- once it's been finalised via SessionEnd (or the
        idle-timeout fallback), this returns None even though
        :attr:`session_id` still holds the value for tooling that
        cares about "which session was the last".

        Used by the web server to filter ``/api/garden`` so the
        client's hero-vs-grid dedup doesn't fire on the live
        session's own periodic partial saves (which would make the
        live tree vanish mid-session).
        """
        if self._saved_on_session_end:
            return None
        return self._session_id

    @property
    def project_path(self) -> str | None:
        return self._project_path

    async def start(self) -> None:
        """Spawn the consume loop and the periodic-save timer. Idempotent."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._consume_loop())
            _log.info("growth_runner_started")
        if self._timer_task is None or self._timer_task.done():
            self._timer_task = asyncio.create_task(self._save_timer_loop())

    async def stop(self) -> None:
        """Cancel the consume + timer loops and write a final snapshot.

        On the way out, save the state as ``complete`` if we have
        seen any events. Better a near-miss snapshot than a lost
        session. Already-saved-on-SessionEnd path is preserved so
        we don't double-write.
        """
        for task in (self._timer_task, self._task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = None
        self._timer_task = None
        if not self._saved_on_session_end:
            self._persist("runner_stop", status=SessionStatus.COMPLETE)
        _log.info("growth_runner_stopped")

    async def _save_timer_loop(self) -> None:
        """Periodic save / idle-timeout watchdog.

        Runs alongside :meth:`_consume_loop`. Two responsibilities:

        * Every ``partial_save_every_n`` events seen since the last
          save → write a ``partial`` snapshot. Bounds maximum data
          loss to one tick's worth of work if the daemon is killed.
        * Every ``idle_timeout_s`` seconds of inactivity → finalise
          the session as ``complete``. Closes the
          SessionEnd-not-fired hole observed on Windows Claude Code.
        """
        try:
            while True:
                await asyncio.sleep(self._timer_tick_s)
                self._maybe_save_periodic()
        except asyncio.CancelledError:
            raise

    def _maybe_save_periodic(self) -> None:
        if self._state is None or self._session_id is None:
            return
        if self._saved_on_session_end:
            return
        # Idle timeout first: if the session has gone quiet, treat
        # it as done and stop ticking partial saves.
        if (
            self._last_event_monotonic is not None
            and self._idle_timeout_s > 0
            and (time.monotonic() - self._last_event_monotonic)
            >= self._idle_timeout_s
        ):
            self._persist("idle_timeout", status=SessionStatus.COMPLETE)
            self._saved_on_session_end = True
            return
        # Periodic partial otherwise.
        if self._events_since_partial_save >= self._partial_save_every_n:
            self._persist("periodic_partial", status=SessionStatus.PARTIAL)
            self._events_since_partial_save = 0

    async def _consume_loop(self) -> None:
        while True:
            ingested = await self._bus.consume()
            try:
                self._handle(ingested)
            except Exception as exc:  # noqa: BLE001 - never let one event kill the runner
                _log.error(
                    "growth_runner_handle_failed",
                    idx=ingested.idx,
                    event_name=getattr(ingested.event, "hook_event_name", "?"),
                    error=str(exc),
                )

    def _handle(self, ingested: IngestedEvent) -> None:
        ev = ingested.event
        is_session_start = (
            getattr(ev, "hook_event_name", None) == "SessionStart"
        )
        if self._session_id is None:
            # First event for this runner -- but only bind if this
            # isn't a session a previous daemon run already finalised.
            # On startup, the journal watcher replays existing files'
            # backlog; without this guard the runner would latch onto
            # the first event it saw (often an old, complete session)
            # and ignore every subsequent live event for a different
            # session_id -- the user's symptom of "I started Claude
            # but the tree never appears."
            if self._is_session_already_completed(ev.session_id):
                _log.info(
                    "growth_runner_skipping_completed_session",
                    session_id=ev.session_id,
                    idx=ingested.idx,
                )
                return
            self._bind_to_session(ev, ingested)
        elif ev.session_id != self._session_id:
            # Different session. The canonical signal of a new Claude
            # session beginning is its SessionStart event -- switch on
            # that and only that. Random PostToolUse / Stop events
            # from a parallel session can't yank the renderer off the
            # current binding (the design contract: "one session at a time;
            # the most recent SessionStart wins").
            if is_session_start:
                self._switch_to_session(ev, ingested)
            else:
                _log.info(
                    "growth_runner_ignoring_other_session",
                    active=self._session_id,
                    seen=ev.session_id,
                    idx=ingested.idx,
                )
                return

        # Capture project_path from the first event that carries one.
        if not self._project_path and ev.cwd:
            self._project_path = ev.cwd

        assert self._state is not None  # set just above; mypy needs the hint
        self._state = apply_event(self._state, ev, event_idx=ingested.idx)
        if isinstance(ev, (PostToolUseEvent, PostToolUseFailureEvent)):
            name = ev.tool_name or "Other"
            self._tool_counts[name] = self._tool_counts.get(name, 0) + 1
        # Track the latest journal ts we've seen so ``_persist`` can
        # stamp the garden row's ended_at with the real hook-write
        # time, not the processing instant.
        if ingested.ts > 0:
            self._latest_event_ts_ms = ingested.ts

        # Save on SessionEnd BEFORE broadcasting so the payload the
        # subscribers receive carries ``live_session_id = None``
        # (via ``current_live_session_id`` reading
        # ``_saved_on_session_end``). The client uses that
        # transition as the trigger to re-fetch /api/garden -- if we
        # broadcast first the SSE goes out with the session still
        # marked live, and the client doesn't see the transition.
        if isinstance(ev, SessionEndEvent):
            self._persist("session_end", status=SessionStatus.COMPLETE)
            self._saved_on_session_end = True

        try:
            self._app.set_state(
                self._state,
                last_event_name=ev.hook_event_name,
                tool_counts=dict(self._tool_counts),
            )
        except TypeError:
            # Older ``set_state`` signatures (test stubs that haven't
            # been updated) only accept ``last_event_name`` -- fall
            # back so the runner keeps consuming.
            self._app.set_state(self._state, last_event_name=ev.hook_event_name)
        self._events_since_partial_save += 1
        self._last_event_monotonic = time.monotonic()

    def _bind_to_session(self, ev: Any, ingested: IngestedEvent) -> None:
        """Initialise per-session state and log the binding.

        Extracted from the original inline branch in :meth:`_handle`
        so :meth:`_switch_to_session` can reuse it. Resets every
        per-session counter so a switch from one session to another
        doesn't carry partial-save / idle-timeout state across.

        ``ingested.ts`` (the journal-recorded wall-clock at hook
        write) seeds ``state.started_at_ms`` so duration accounting
        survives orphan-journal replay -- without it the runner
        stamped both started and ended with the processing instant,
        producing 0-second sessions in the garden.
        """
        self._session_id = ev.session_id
        self._project_path = ev.cwd or ""
        detected = (
            self._theme
            if self._theme != "default"
            else detect_language(self._project_path)
        )
        started_ts = ingested.ts if ingested.ts > 0 else None
        self._state = build_initial_state(
            ev.session_id, theme=detected, started_at_ms=started_ts,
        )
        self._detected_language = detected
        self._saved_on_session_end = False
        self._events_since_partial_save = 0
        self._last_event_monotonic = None
        self._latest_event_ts_ms = started_ts
        self._tool_counts = {}
        _log.info(
            "growth_runner_session_bound",
            session_id=ev.session_id,
            project_path=self._project_path,
            theme=detected,
            idx=ingested.idx,
        )

    def _switch_to_session(self, ev: Any, ingested: IngestedEvent) -> None:
        """Persist the current session (as partial) and bind to a new one.

        Fired when a SessionStart event arrives for a session_id
        that doesn't match the current binding. Persisting the
        outgoing session as ``partial`` means its progress lands in
        the garden as a card rather than vanishing -- even if it
        never got a SessionEnd (parallel session, user closed
        Claude without a clean shutdown, etc.).
        """
        old_session = self._session_id
        if old_session is not None and not self._saved_on_session_end:
            self._persist("session_switch", status=SessionStatus.PARTIAL)
        self._bind_to_session(ev, ingested)
        _log.info(
            "growth_runner_session_switched",
            old_session=old_session,
            new_session=ev.session_id,
        )

    def _is_session_already_completed(self, session_id: str) -> bool:
        """Has a previous daemon run already finalised this session?

        ``complete`` / ``recovered`` rows in the garden mean the
        session is done -- no point re-binding to it just because the
        journal-watcher replayed its backlog on startup. ``partial``
        rows are intentionally NOT treated as finalised: a partial
        means a previous daemon run took a snapshot but the session
        might still be making progress.

        Cached so the same lookup doesn't hit SQLite per event.
        """
        if self._garden is None:
            return False
        cached = self._completed_session_cache.get(session_id)
        if cached is not None:
            return cached
        row = self._garden.get_session(session_id)
        completed = (
            row is not None
            and row.status in (SessionStatus.COMPLETE, SessionStatus.RECOVERED)
        )
        self._completed_session_cache[session_id] = completed
        return completed

    def _persist(self, reason: str, *, status: str = SessionStatus.COMPLETE) -> None:
        """Write the current state to the garden if a store is wired in.

        Idempotent: the store's ``INSERT OR REPLACE`` overwrites the
        previous row for the same session id, so a session that
        progresses partial -> partial -> complete leaves only one
        row in the garden at any time.

        ``ended_at_ms`` is the most recent journal ts we've seen for
        this session -- the original hook-write timestamp, not the
        processing instant. Without this, replayed orphan journals
        landed in the garden with started == ended (both = wall
        clock at replay time), producing zero-duration rows in the
        Total time stat.
        """
        if self._garden is None or self._state is None or self._journals is None:
            return
        if not self._session_id:
            return
        try:
            self._garden.save_session(
                self._state,
                project_path=self._project_path or "",
                event_log_path=self._journals.path_for(self._session_id),
                detected_language=self._detected_language,
                ended_at_ms=self._latest_event_ts_ms,
                status=status,
            )
            _log.info(
                "garden_session_persisted",
                reason=reason,
                status=status,
                session_id=self._session_id,
            )
        except Exception as exc:  # noqa: BLE001 - never let a save kill the runner
            _log.error(
                "garden_persist_failed",
                reason=reason,
                status=status,
                session_id=self._session_id,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Orphan-journal recovery
# ---------------------------------------------------------------------------


def recover_orphan_sessions(
    store: GardenStore, journals: JournalRegistry
) -> int:
    """Replay every journal that has no up-to-date garden row.

    Called once on daemon startup so a previous run that was
    SIGKILL'd (no chance to call ``runner.stop()``) still ends up
    with a row in the garden. The rules:

    * If no row exists for the session id at all → save as
      ``recovered``.
    * If a row exists but its ``ended_at`` is older than the
      journal's last record timestamp → re-save as ``recovered``,
      with the freshly-replayed state.
    * If the row is current (``ended_at`` >= last journal ts) →
      skip; the previous shutdown handled it.

    Returns the number of sessions actually written. Logs but
    swallows per-journal errors -- recovery is best-effort, never
    a hard failure on startup.
    """
    recovered = 0
    for path in journals.list_journal_files():
        try:
            recovered += _maybe_recover_one(store, path)
        except Exception as exc:  # noqa: BLE001 - one bad journal must not kill startup
            _log.warning(
                "orphan_recovery_failed",
                path=str(path),
                error=str(exc),
            )
    if recovered:
        _log.info("orphan_recovery_summary", recovered=recovered)
    return recovered


def _maybe_recover_one(store: GardenStore, journal_path: Path) -> int:
    """Replay ``journal_path`` if the garden row is missing/stale.

    Returns 1 if a row was written, 0 otherwise.
    """
    records: list[dict[str, object]] = []
    with journal_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not records:
        return 0
    first_raw = records[0].get("raw") if isinstance(records[0], dict) else None
    if not isinstance(first_raw, dict):
        return 0
    session_id = str(first_raw.get("session_id", ""))
    if not session_id:
        return 0
    cwd = str(first_raw.get("cwd", "") or "")
    last_ts_raw = records[-1].get("ts")
    last_ts = last_ts_raw if isinstance(last_ts_raw, int) else 0

    existing = store.get_session(session_id)
    if (
        existing is not None
        and existing.ended_at is not None
        and last_ts
        and existing.ended_at >= last_ts
    ):
        return 0  # garden row is at least as fresh as the journal
    from bonsai_cc.events.models import BaseHookEvent

    # Line position is the idx -- hook records omit the ``idx`` field
    # entirely (see ``hook/client_template.py`` for the rationale).
    # Reading ``rec.get("idx")`` here was the bug: every real production
    # journal was silently skipped, so ``bonsai-cc list`` / ``show`` /
    # ``export`` returned an empty garden on a clean install. ``idx`` is
    # assigned from the enumerate counter to match what ``read_journal``
    # and the watcher produce.
    parsed_events: list[tuple[int, BaseHookEvent]] = []
    next_idx = 0
    for rec in records:
        if not isinstance(rec, dict):
            next_idx += 1  # advance even on skip -- keep line-position contract
            continue
        raw = rec.get("raw")
        if not isinstance(raw, dict):
            next_idx += 1
            continue
        with contextlib.suppress(Exception):  # drop malformed events
            parsed_events.append((next_idx, parse_event(raw)))
        next_idx += 1
    if not parsed_events:
        return 0

    theme = detect_language(cwd)
    first_ts_raw = records[0].get("ts")
    started_at_ms = first_ts_raw if isinstance(first_ts_raw, int) else 0
    state = apply_all(
        session_id,
        parsed_events,
        theme=theme,
        started_at_ms=started_at_ms,
    )
    store.save_session(
        state,
        project_path=cwd,
        event_log_path=str(journal_path),
        detected_language=theme,
        started_at_ms=started_at_ms,
        ended_at_ms=last_ts or None,
        status=SessionStatus.RECOVERED,
    )
    _log.info(
        "orphan_session_recovered",
        session_id=session_id,
        events=len(parsed_events),
    )
    return 1


# ---------------------------------------------------------------------------
# CLI prelude: every read-only command calls this before doing its work.
# ---------------------------------------------------------------------------


def ensure_garden_consistent(config: Config | None = None) -> int:
    """Make sure the garden reflects every journal on disk.

    The web pipeline runs orphan-recovery on startup, but read-only
    commands (``list``, ``show``, ``export``) previously didn't -- so a user
    who finished their work, never re-ran the watch flow, and just
    opened the garden saw an empty list even though an orphan
    journal sat on disk. This helper closes that gap.

    Idempotent: ``recover_orphan_sessions`` already skips journals
    whose garden row is at least as fresh as the latest record, so
    repeated invocations cost ~one stat per journal.

    Logs at start and end give us forensics when something is off:
    a missing pair of entries means recovery never ran.

    Returns the number of sessions actually recovered on this call.
    """
    cfg = config or get_config()
    cfg.ensure_dirs()
    journals = JournalRegistry(cfg.journals_dir)
    pending = count_orphan_journals(cfg, journals=journals)
    _log.info(
        "garden_consistency_check_start",
        garden_db=str(cfg.garden_db),
        orphans_found=pending,
    )
    started = time.monotonic()
    store = GardenStore(config=cfg)
    try:
        recovered = recover_orphan_sessions(store, journals)
    finally:
        store.close()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    total = len(journals.list_journal_files())
    skipped = max(0, total - recovered)
    _log.info(
        "garden_consistency_check_done",
        recovered=recovered,
        skipped=skipped,
        ms=elapsed_ms,
    )
    return recovered


def count_orphan_journals(
    config: Config | None = None,
    *,
    journals: JournalRegistry | None = None,
) -> int:
    """Return how many on-disk journals are NOT yet reflected in the
    garden DB. Read-only: never writes, never raises on missing
    files. Used by ``ensure_garden_consistent`` for the start-line
    log and by ``bonsai-cc doctor`` for its dry-run report.
    """
    cfg = config or get_config()
    journals = journals or JournalRegistry(cfg.journals_dir)
    files = journals.list_journal_files()
    if not files:
        return 0
    # Open a short-lived store so the count survives even if the
    # caller hasn't migrated the schema yet.
    try:
        store = GardenStore(config=cfg)
    except Exception:  # noqa: BLE001 - the doctor must never crash
        return len(files)
    try:
        pending = 0
        for path in files:
            try:
                sid, last_ts = _peek_journal_endpoints(path)
            except OSError:
                continue
            if not sid:
                continue
            row = store.get_session(sid)
            if row is None:
                pending += 1
                continue
            if last_ts and (row.ended_at is None or row.ended_at < last_ts):
                pending += 1
        return pending
    finally:
        store.close()


def _peek_journal_endpoints(path: Path) -> tuple[str | None, int]:
    """Return ``(first_session_id, last_ts)`` for ``path`` without
    parsing every record into a typed event.

    A read-only summary used by the orphan-count check.
    """
    first_sid: str | None = None
    last_ts = 0
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = rec.get("ts")
            if isinstance(ts, int):
                last_ts = ts
            if first_sid is None:
                raw = rec.get("raw")
                if isinstance(raw, dict):
                    sid = raw.get("session_id")
                    if isinstance(sid, str):
                        first_sid = sid
    return first_sid, last_ts


# ---------------------------------------------------------------------------
# Journal replay: feed a recorded session through the bus
# ---------------------------------------------------------------------------


async def replay_journal_into_bus(
    journal_path: Path,
    bus: EventBus,
    *,
    speed: float = 0.0,
) -> None:
    """Read a JSONL journal and publish every record onto ``bus``.

    * ``speed == 0`` -- push every event immediately (the test path
      and the demo-grow CLI use this).
    * ``speed > 0`` -- sleep between events proportional to the
      original wall-clock gap divided by ``speed``. ``speed=1.0``
      is real-time; ``speed=10.0`` is 10x faster than the original
      session.

    Missing / malformed lines log and are skipped -- the journal is
    the source of truth and we want replay to survive minor
    corruption the same way live ingest does.
    """
    prev_ts: int | None = None
    async for raw_line in _read_lines(journal_path):
        try:
            rec = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            _log.warning("replay_corrupt_line", path=str(journal_path), error=str(exc))
            continue
        if not isinstance(rec, dict):
            continue
        idx = rec.get("idx")
        raw = rec.get("raw")
        ts = rec.get("ts")
        if not isinstance(idx, int) or not isinstance(raw, dict):
            continue
        try:
            event = parse_event(raw)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "replay_parse_failed",
                idx=idx,
                event_name=raw.get("hook_event_name"),
                error=str(exc),
            )
            continue
        if speed > 0 and prev_ts is not None and isinstance(ts, int):
            gap_s = max(0.0, (ts - prev_ts) / 1000.0 / speed)
            if gap_s:
                await asyncio.sleep(min(gap_s, 5.0))
        if isinstance(ts, int):
            prev_ts = ts
        await bus.publish(IngestedEvent(idx=idx, event=event))


async def _read_lines(path: Path) -> AsyncIterator[str]:
    """Yield non-empty lines from ``path``. Async iteration so the
    caller can interleave its own awaits between events."""
    # Reading off the asyncio thread would need aiofiles; for our
    # event volumes the sync read is fine and the function is
    # already async so the caller can ``await`` between yields.
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if s:
            yield s


# ``bonsai_cc.web.pipeline.run_web_pipeline`` is the only top-level
# entry point; the pieces above (``GrowthRunner``,
# ``recover_orphan_sessions``, ``ensure_garden_consistent``,
# ``replay_journal_into_bus``) are reused by it.

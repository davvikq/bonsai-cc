"""Robust session persistence: SessionEnd is one path of four.

The original implementation only saved on a clean ``SessionEnd``
event. Live Claude Code on Windows doesn't reliably fire one — the
user's May-2026 session ended via ``/exit`` with 4 ``Stop`` events
and 0 ``SessionEnd``, and the garden was empty afterwards.

The four save paths are now:

1. ``SessionEnd`` hook (existing behaviour)
2. ``runner.stop()`` shutdown flush (existing behaviour)
3. Periodic partial save every N events (new)
4. Idle timeout finalisation after M seconds of no events (new)

And on every daemon start, an orphan-journal scan recovers any
session whose previous daemon process died without saving.

This file covers all five paths plus the partial-then-replay
guarantee.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

from bonsai_cc.events.bus import IngestedEvent, reset_event_bus_for_tests
from bonsai_cc.events.journal import JournalRegistry
from bonsai_cc.events.models import parse_event
from bonsai_cc.garden.store import GardenStore, SessionStatus
from bonsai_cc.growth.apply import apply_all
from bonsai_cc.growth.state import demo_tree, state_from_dict
from bonsai_cc.runner import (
    GrowthRunner,
    build_initial_state,
    recover_orphan_sessions,
    replay_journal_into_bus,
)
from tests.conftest import RecorderApp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ingested(idx: int, payload: dict[str, object]) -> IngestedEvent:
    return IngestedEvent(idx=idx, event=parse_event(payload))


async def _drain(bus) -> None:  # type: ignore[no-untyped-def]
    """Yield control until the bus drains, then one extra tick."""
    for _ in range(60):
        await asyncio.sleep(0)
        if bus.qsize() == 0:
            break
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# (a) Clean SessionEnd → status=complete
# ---------------------------------------------------------------------------


async def test_clean_session_end_saves_complete(bonsai_home: Path) -> None:
    bus = reset_event_bus_for_tests()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journals = JournalRegistry(cfg_journals)
    garden = GardenStore()
    sid = "clean-end-001"
    # The journal must exist for the save's event_log_path to be sane.
    (cfg_journals / f"{sid}.jsonl").write_text(
        "", encoding="utf-8"
    )

    app = RecorderApp(build_initial_state(sid))
    runner = GrowthRunner(
        app, bus, garden=garden, journals=journals,
        # Big numbers so neither periodic nor idle fires during this test.
        partial_save_every_n=1000,
        idle_timeout_s=99999.0,
        timer_tick_s=99999.0,
    )

    if True:
        await runner.start()
        await bus.publish(_ingested(0, {
            "session_id": sid, "hook_event_name": "SessionStart",
            "source": "startup", "cwd": str(bonsai_home),
        }))
        await bus.publish(_ingested(1, {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Write", "tool_input": {
                "file_path": str(bonsai_home / "a.py"), "content": "x"},
            "cwd": str(bonsai_home),
        }))
        await bus.publish(_ingested(2, {
            "session_id": sid, "hook_event_name": "SessionEnd",
            "end_reason": "clear",
        }))
        for _ in range(50):
            await asyncio.sleep(0)
            if bus.qsize() == 0:
                break
        await asyncio.sleep(0)
        await runner.stop()

    row = garden.get_session(sid)
    garden.close()
    assert row is not None
    assert row.status == SessionStatus.COMPLETE


# ---------------------------------------------------------------------------
# (b) Daemon killed mid-session → next daemon start picks up the orphan
# ---------------------------------------------------------------------------


def test_recovery_picks_up_orphan_journal(bonsai_home: Path) -> None:
    """Simulate a daemon that died before saving: write a journal
    directly, then call ``recover_orphan_sessions``. The garden
    must end up with a row marked ``recovered``.

    SIGKILL on the daemon is functionally equivalent to "the
    journal exists but no save was performed" — we test the
    recovery routine against that state."""
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journals = JournalRegistry(cfg_journals)
    sid = "orphan-aaa"
    records = [
        {"ts": 1700000000000, "idx": 0, "raw": {
            "session_id": sid, "hook_event_name": "SessionStart",
            "source": "startup", "cwd": str(bonsai_home),
        }},
        {"ts": 1700000001000, "idx": 1, "raw": {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "PowerShell", "tool_input": {"command": "ls"},
            "cwd": str(bonsai_home),
        }},
        {"ts": 1700000002000, "idx": 2, "raw": {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Write", "tool_input": {
                "file_path": str(bonsai_home / "x.py"), "content": ""},
            "cwd": str(bonsai_home),
        }},
    ]
    (cfg_journals / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )

    garden = GardenStore()
    # Daemon starts → recovery runs.
    assert garden.get_session(sid) is None
    recovered = recover_orphan_sessions(garden, journals)
    assert recovered == 1
    row = garden.get_session(sid)
    garden.close()
    assert row is not None
    assert row.status == SessionStatus.RECOVERED
    # The replayed state should have geometry.
    assert row.tool_call_count >= 1
    assert row.final_ascii is not None and "│" in row.final_ascii


def test_recovery_handles_phase11_records_without_idx_field(
    bonsai_home: Path,
) -> None:
    """Phase-11 hook records omit the ``idx`` field — line position
    is the idx (see ``hook/client_template.py``). The earlier
    recovery loop required ``isinstance(rec.get("idx"), int)``, so
    every real production journal was silently skipped and
    ``bonsai-cc list`` / ``show`` / ``export`` returned an empty
    garden on a clean install. This test pins the line-position
    contract: a journal whose records carry no ``idx`` field at all
    must still recover to a garden row.
    """
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journals = JournalRegistry(cfg_journals)
    sid = "phase11-no-idx-001"
    # Same shape ``hook/client_template.py`` writes: ``{"ts": ...,
    # "raw": ...}`` — no ``idx`` anywhere.
    records = [
        {"ts": 1700000000000, "raw": {
            "session_id": sid, "hook_event_name": "SessionStart",
            "source": "startup", "cwd": str(bonsai_home),
        }},
        {"ts": 1700000001000, "raw": {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "tool_input": {"command": "ls"},
            "cwd": str(bonsai_home),
        }},
        {"ts": 1700000002000, "raw": {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Write", "tool_input": {
                "file_path": str(bonsai_home / "x.py"), "content": ""},
            "cwd": str(bonsai_home),
        }},
    ]
    (cfg_journals / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )

    garden = GardenStore()
    recovered = recover_orphan_sessions(garden, journals)
    row = garden.get_session(sid)
    garden.close()
    assert recovered == 1
    assert row is not None
    assert row.status == SessionStatus.RECOVERED
    assert row.tool_call_count >= 1


def test_recovery_is_idempotent_when_garden_is_already_fresh(
    bonsai_home: Path,
) -> None:
    """A second recovery pass over a journal whose row is already
    up to date should be a no-op."""
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journals = JournalRegistry(cfg_journals)
    sid = "already-saved-001"
    records = [
        {"ts": 1700000000000, "idx": 0, "raw": {
            "session_id": sid, "hook_event_name": "SessionStart",
            "cwd": str(bonsai_home),
        }},
    ]
    (cfg_journals / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    garden = GardenStore()
    # First pass: recovers.
    assert recover_orphan_sessions(garden, journals) == 1
    # Second pass: no-op (the ended_at on the row covers last_ts).
    assert recover_orphan_sessions(garden, journals) == 0
    garden.close()


# ---------------------------------------------------------------------------
# (c) Stop without SessionEnd, then daemon shutdown → save on stop
# ---------------------------------------------------------------------------


async def test_stop_without_session_end_saves_on_shutdown(
    bonsai_home: Path,
) -> None:
    """The bug-report scenario: 4 Stop events, 0 SessionEnd, then
    the user presses q to quit the TUI. The runner's shutdown flush
    must save the session as ``complete``.
    """
    bus = reset_event_bus_for_tests()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journals = JournalRegistry(cfg_journals)
    garden = GardenStore()
    sid = "no-end-but-q-001"
    (cfg_journals / f"{sid}.jsonl").write_text("", encoding="utf-8")

    app = RecorderApp(build_initial_state(sid))
    runner = GrowthRunner(
        app, bus, garden=garden, journals=journals,
        partial_save_every_n=1000, idle_timeout_s=99999.0,
        timer_tick_s=99999.0,
    )
    if True:
        await runner.start()
        await bus.publish(_ingested(0, {
            "session_id": sid, "hook_event_name": "SessionStart",
            "cwd": str(bonsai_home),
        }))
        await bus.publish(_ingested(1, {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Write", "tool_input": {
                "file_path": str(bonsai_home / "a.py"), "content": "x"},
            "cwd": str(bonsai_home),
        }))
        await bus.publish(_ingested(2, {
            "session_id": sid, "hook_event_name": "Stop",
            "stop_reason": "end_turn",
        }))
        for _ in range(50):
            await asyncio.sleep(0)
            if bus.qsize() == 0:
                break
        await asyncio.sleep(0)
        # No SessionEnd — user quit the TUI instead.
        await runner.stop()

    row = garden.get_session(sid)
    garden.close()
    assert row is not None, "shutdown flush should have written the row"
    # Shutdown flush considers the session complete.
    assert row.status == SessionStatus.COMPLETE
    assert row.tool_call_count >= 1


# ---------------------------------------------------------------------------
# (d) Replay of a saved partial session works
# ---------------------------------------------------------------------------


def test_partial_state_replays_without_exceptions(bonsai_home: Path) -> None:
    """A partial save's stored JSON must round-trip back into a
    valid ``TreeState`` and survive a re-render."""
    garden = GardenStore()
    sid = "partial-replay-001"
    state = demo_tree(sid)
    state = replace(state, event_count=5)
    garden.save_session(
        state,
        project_path="/p",
        event_log_path=bonsai_home / "journals" / f"{sid}.jsonl",
        status=SessionStatus.PARTIAL,
    )
    row = garden.get_session(sid)
    garden.close()
    assert row is not None
    assert row.status == SessionStatus.PARTIAL
    # The serialised state round-trips.
    payload = row.state_dict()
    assert payload is not None
    restored = state_from_dict(payload)
    # And re-projecting doesn't raise.
    from bonsai_cc.render.projection import project

    grid = project(restored, 80, 24)
    assert grid  # non-empty


# ---------------------------------------------------------------------------
# Periodic partial saves while the session is live
# ---------------------------------------------------------------------------


async def test_periodic_partial_save_fires_during_live_session(
    bonsai_home: Path,
) -> None:
    """Every N events the runner writes a ``partial`` row so a
    kill -9 loses at most a tick's worth of state, not the whole
    session."""
    bus = reset_event_bus_for_tests()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journals = JournalRegistry(cfg_journals)
    garden = GardenStore()
    sid = "partial-tick-001"
    (cfg_journals / f"{sid}.jsonl").write_text("", encoding="utf-8")

    app = RecorderApp(build_initial_state(sid))
    runner = GrowthRunner(
        app, bus, garden=garden, journals=journals,
        partial_save_every_n=2,         # fire after every two events
        idle_timeout_s=99999.0,         # don't trip idle here
        timer_tick_s=0.05,              # tick fast so the test isn't slow
    )

    if True:
        await runner.start()
        await bus.publish(_ingested(0, {
            "session_id": sid, "hook_event_name": "SessionStart",
            "cwd": str(bonsai_home),
        }))
        await bus.publish(_ingested(1, {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Write", "tool_input": {
                "file_path": str(bonsai_home / "a.py"), "content": "x"},
            "cwd": str(bonsai_home),
        }))
        await bus.publish(_ingested(2, {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Write", "tool_input": {
                "file_path": str(bonsai_home / "b.py"), "content": "y"},
            "cwd": str(bonsai_home),
        }))
        # Drain the bus.
        for _ in range(50):
            await asyncio.sleep(0)
            if bus.qsize() == 0:
                break
        # Wait a couple of timer ticks so the periodic save fires.
        await asyncio.sleep(0.2)
        await asyncio.sleep(0)
        # Snapshot what the garden looks like mid-flight.
        partial_row = garden.get_session(sid)
        await runner.stop()
        final_row = garden.get_session(sid)
    garden.close()

    assert partial_row is not None, "periodic tick did not save"
    assert partial_row.status == SessionStatus.PARTIAL
    # After runner.stop() the row becomes complete (or stays
    # complete if SessionEnd already fired, which it didn't here).
    assert final_row is not None
    assert final_row.status == SessionStatus.COMPLETE


# ---------------------------------------------------------------------------
# Idle timeout finalises the session as complete
# ---------------------------------------------------------------------------


async def test_idle_timeout_finalises_complete(bonsai_home: Path) -> None:
    bus = reset_event_bus_for_tests()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journals = JournalRegistry(cfg_journals)
    garden = GardenStore()
    sid = "idle-finalise-001"
    (cfg_journals / f"{sid}.jsonl").write_text("", encoding="utf-8")

    app = RecorderApp(build_initial_state(sid))
    runner = GrowthRunner(
        app, bus, garden=garden, journals=journals,
        partial_save_every_n=99999,  # never fire the partial path
        idle_timeout_s=0.05,           # super-fast idle for the test
        timer_tick_s=0.02,
    )
    if True:
        await runner.start()
        await bus.publish(_ingested(0, {
            "session_id": sid, "hook_event_name": "SessionStart",
            "cwd": str(bonsai_home),
        }))
        for _ in range(40):
            await asyncio.sleep(0)
            if bus.qsize() == 0:
                break
        # Wait long enough for the idle timeout to fire.
        await asyncio.sleep(0.3)
        await asyncio.sleep(0)
        row = garden.get_session(sid)
        await runner.stop()
    garden.close()

    assert row is not None
    assert row.status == SessionStatus.COMPLETE


# ---------------------------------------------------------------------------
# Recovery against the real captured session — the user's bug
# ---------------------------------------------------------------------------


def test_real_session_recovers_from_journal(bonsai_home: Path) -> None:
    """The bug-report case: a real 33-event session whose daemon
    never wrote to the garden. The recovery scan must produce a
    populated row.
    """
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journals = JournalRegistry(cfg_journals)
    source = (
        Path(__file__).parent / "fixtures" / "real_session_2026-05-15.jsonl"
    )
    sid = "86abd5d6-881d-4a56-860d-fc2e2d199787"
    (cfg_journals / f"{sid}.jsonl").write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    garden = GardenStore()
    assert garden.get_session(sid) is None
    recovered = recover_orphan_sessions(garden, journals)
    row = garden.get_session(sid)
    garden.close()

    assert recovered == 1
    assert row is not None
    assert row.status == SessionStatus.RECOVERED
    assert row.tool_call_count >= 9
    assert row.final_ascii is not None
    # The wilted-leaf glyph from the captured failure event survives
    # the replay path.
    assert "," in row.final_ascii


# Silence unused-import warnings if any helper is dropped.
_ = (apply_all, replay_journal_into_bus, time, _drain)

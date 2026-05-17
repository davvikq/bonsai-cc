"""Tail-watcher for the journals directory (phase 11).

Pins the contract the daemon depends on: new lines in
``<home>/journals/<sid>.jsonl`` become :class:`IngestedEvent`
publications on the bus, with ``idx`` matching the line position.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from bonsai_cc.events.bus import IngestedEvent, reset_event_bus_for_tests
from bonsai_cc.events.watcher import JournalWatcher


def _hook_record(payload: dict[str, object]) -> str:
    """Mimic what the phase-11 hook client writes: ``{"ts", "raw"}``,
    no ``idx``, one JSON object per line."""
    return json.dumps({"ts": 0, "raw": payload}, separators=(",", ":")) + "\n"


def _append(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(_hook_record(payload))


# ---------------------------------------------------------------------------
# Catch-up scan: existing files at startup are processed in full.
# ---------------------------------------------------------------------------


async def test_initial_scan_publishes_existing_records(tmp_path: Path) -> None:
    """A daemon starting up after the hook wrote events must
    re-publish them to the bus — the contract that lets a
    crash-restart cycle replay the in-flight session."""
    journals = tmp_path / "journals"
    journals.mkdir()
    p = journals / "s1.jsonl"
    for i in range(3):
        _append(p, {
            "session_id": "s1",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": f"/p/f{i}.py", "content": "x"},
        })

    bus = reset_event_bus_for_tests()
    watcher = JournalWatcher(journals, bus)
    await watcher._scan_all()

    received: list[IngestedEvent] = []
    while not bus._queue.empty():
        received.append(bus._queue.get_nowait())
    assert [ev.idx for ev in received] == [0, 1, 2]
    assert all(ev.event.hook_event_name == "PostToolUse" for ev in received)


async def test_initial_scan_handles_missing_dir(tmp_path: Path) -> None:
    """``journals_dir`` may not exist on first daemon launch — the
    watcher must create it without raising."""
    journals = tmp_path / "journals-not-yet"
    bus = reset_event_bus_for_tests()
    watcher = JournalWatcher(journals, bus)
    # No raise expected.
    await watcher._scan_all()
    assert journals.exists()


# ---------------------------------------------------------------------------
# Incremental tail: subsequent writes only publish the new lines.
# ---------------------------------------------------------------------------


async def test_incremental_scan_does_not_replay_old_records(tmp_path: Path) -> None:
    """Once a record's offset is consumed, the watcher never
    re-publishes it. Appending one more line publishes exactly
    one more :class:`IngestedEvent`."""
    journals = tmp_path / "journals"
    journals.mkdir()
    p = journals / "s2.jsonl"
    _append(p, {"session_id": "s2", "hook_event_name": "SessionStart"})

    bus = reset_event_bus_for_tests()
    watcher = JournalWatcher(journals, bus)
    await watcher._scan_all()
    # Drain the bus.
    drained = 0
    while not bus._queue.empty():
        bus._queue.get_nowait()
        drained += 1
    assert drained == 1

    _append(p, {
        "session_id": "s2",
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "/p/a.py", "content": "y"},
    })
    await watcher._scan_one(p)

    received: list[IngestedEvent] = []
    while not bus._queue.empty():
        received.append(bus._queue.get_nowait())
    assert len(received) == 1
    assert received[0].idx == 1
    assert received[0].event.hook_event_name == "PostToolUse"


async def test_partial_trailing_line_is_held_until_complete(
    tmp_path: Path,
) -> None:
    """The hook fsyncs after every full line, but a curious test
    that opens the file mid-write must not see half a record
    published. The watcher splits on ``\\n``; any trailing remainder
    stays in the file for the next pass."""
    journals = tmp_path / "journals"
    journals.mkdir()
    p = journals / "s3.jsonl"
    # Append two full records, then a partial fragment with no
    # trailing newline.
    full = _hook_record({
        "session_id": "s3",
        "hook_event_name": "SessionStart",
    })
    full2 = _hook_record({
        "session_id": "s3",
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "/p/f.py", "content": "x"},
    })
    partial = '{"ts":0,"raw":{"session_id":"s3","hook'
    p.write_text(full + full2 + partial, encoding="utf-8")

    bus = reset_event_bus_for_tests()
    watcher = JournalWatcher(journals, bus)
    await watcher._scan_one(p)

    received: list[IngestedEvent] = []
    while not bus._queue.empty():
        received.append(bus._queue.get_nowait())
    # Only the two complete records get through.
    assert len(received) == 2
    assert [ev.idx for ev in received] == [0, 1]

    # Now complete the partial line, then re-scan: only the new
    # third record should publish.
    p.write_text(
        full + full2 + partial + '_event_name":"Stop"}}\n',
        encoding="utf-8",
    )
    await watcher._scan_one(p)
    third: list[IngestedEvent] = []
    while not bus._queue.empty():
        third.append(bus._queue.get_nowait())
    assert len(third) == 1
    assert third[0].idx == 2
    assert third[0].event.hook_event_name == "Stop"


# ---------------------------------------------------------------------------
# Idx is always line-position, regardless of malformed lines.
# ---------------------------------------------------------------------------


async def test_idx_advances_through_malformed_lines(tmp_path: Path) -> None:
    """Line position is the idx contract, period. A corrupt line
    still advances the counter so the next valid record gets the
    correct line-position idx for the deterministic-replay seed."""
    journals = tmp_path / "journals"
    journals.mkdir()
    p = journals / "s4.jsonl"
    # Hand-write: valid, corrupt, valid.
    full = _hook_record({
        "session_id": "s4", "hook_event_name": "SessionStart",
    })
    valid_after = _hook_record({
        "session_id": "s4", "hook_event_name": "Stop",
    })
    p.write_text(full + "{not even json\n" + valid_after, encoding="utf-8")

    bus = reset_event_bus_for_tests()
    watcher = JournalWatcher(journals, bus)
    await watcher._scan_one(p)

    received: list[IngestedEvent] = []
    while not bus._queue.empty():
        received.append(bus._queue.get_nowait())
    # Two valid records published; idx 0 and 2 (the middle slot is
    # the corrupt line — counted, never published).
    assert [ev.idx for ev in received] == [0, 2]
    assert [ev.event.hook_event_name for ev in received] == [
        "SessionStart",
        "Stop",
    ]


# ---------------------------------------------------------------------------
# End-to-end: actually run the watch loop and let watchfiles fire.
# ---------------------------------------------------------------------------


async def test_live_watch_picks_up_new_appends(tmp_path: Path) -> None:
    """Spin up the watcher, append after it starts, assert the
    event lands on the bus. This is the real "live tail" path
    that backs the daemon's SSE forward."""
    journals = tmp_path / "journals"
    journals.mkdir()
    bus = reset_event_bus_for_tests()
    watcher = JournalWatcher(journals, bus, poll_min_interval_ms=20)
    task = asyncio.create_task(watcher.run())
    try:
        # Tiny delay so the watcher has time to call ``awatch``.
        await asyncio.sleep(0.1)
        _append(journals / "s5.jsonl", {
            "session_id": "s5",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/p/a.py", "content": "z"},
        })
        # Wait for the event to show up on the bus.
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not bus._queue.empty():
                break
        ev = bus._queue.get_nowait()
    finally:
        watcher.request_stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    assert ev.event.hook_event_name == "PostToolUse"
    assert ev.idx == 0

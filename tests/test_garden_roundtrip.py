"""End-to-end: record → save → replay → byte-identical final_ascii.

The contract DESIGN.md §6 calls for: a session run through the live
pipeline, persisted to the garden, then replayed from the saved
``event_log_path`` must produce the same final ASCII as the
original. This is the strongest guarantee in the codebase — it
proves the durability path is honest.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from bonsai_cc.events.bus import reset_event_bus_for_tests
from bonsai_cc.events.journal import JournalRegistry
from bonsai_cc.events.models import parse_event
from bonsai_cc.garden.store import GardenStore, load_state_from_row, render_final_ascii
from bonsai_cc.growth.apply import apply_all
from bonsai_cc.runner import GrowthRunner, build_initial_state, replay_journal_into_bus
from tests.conftest import RecorderApp

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "events"


def _load_events(path: Path) -> list[tuple[int, object]]:
    out: list[tuple[int, object]] = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out.append((int(rec["idx"]), parse_event(rec["raw"])))
    return out


def _session_id_for(path: Path) -> str:
    with path.open(encoding="utf-8") as fp:
        return json.loads(fp.readline())["raw"]["session_id"]


async def test_record_save_replay_byte_identical(bonsai_home: Path) -> None:
    fixture = FIXTURES_DIR / "mixed_tools.jsonl"
    sid = _session_id_for(fixture)

    # Copy the fixture into the test sandbox so the saved
    # ``event_log_path`` points somewhere stable. The runner would
    # do this via the JournalRegistry; we mimic it.
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journal_in_home = cfg_journals / f"{sid}.jsonl"
    shutil.copy(fixture, journal_in_home)

    # 1. RECORD: drive the live pipeline.
    bus = reset_event_bus_for_tests()
    app = RecorderApp(build_initial_state(sid, theme="default", started_at_ms=0))
    journals = JournalRegistry(cfg_journals)
    garden = GardenStore()
    runner = GrowthRunner(
        app, bus, theme="default", garden=garden, journals=journals,
    )

    if True:
        await runner.start()
        await replay_journal_into_bus(journal_in_home, bus, speed=0.0)
        for _ in range(50):
            await asyncio.sleep(0)
            if bus.qsize() == 0:
                break
        await asyncio.sleep(0)
        await runner.stop()

    # 2. SAVE: the SessionEnd event in the fixture triggered a save.
    #    Confirm the row exists.
    row = garden.get_session(sid)
    garden.close()
    assert row is not None
    assert row.event_log_path == str(journal_in_home)

    # 3. REPLAY: re-fold the saved event log offline.
    replayed = apply_all(
        sid,
        _load_events(Path(row.event_log_path)),
        theme=row.theme,
        started_at_ms=row.started_at,
    )

    # 4. BYTE-IDENTICAL: the cached ASCII snapshot in the row equals
    #    the freshly-rendered ASCII of the replayed state.
    assert row.final_ascii == render_final_ascii(replayed)

    # And the JSON state round-trips back into the same TreeState.
    restored = load_state_from_row(row)
    # ``started_at_ms`` differs because the runner stamped wall-clock
    # at session bind; apply_all uses what we passed in. Normalise.
    from dataclasses import replace as _replace

    assert _replace(restored, started_at_ms=replayed.started_at_ms) == replayed


async def test_save_on_runner_stop_when_no_session_end(bonsai_home: Path) -> None:
    """If the user quits before SessionEnd fires, runner.stop() flushes."""
    sid = "no-end-test"
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journal = cfg_journals / f"{sid}.jsonl"
    journal.write_text(
        json.dumps(
            {
                "ts": 1,
                "idx": 0,
                "raw": {
                    "session_id": sid,
                    "hook_event_name": "SessionStart",
                    "cwd": "/work/proj",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    bus = reset_event_bus_for_tests()
    app = RecorderApp(build_initial_state(sid, theme="default", started_at_ms=0))
    journals = JournalRegistry(cfg_journals)
    garden = GardenStore()
    runner = GrowthRunner(
        app, bus, theme="default", garden=garden, journals=journals,
    )

    if True:
        await runner.start()
        await replay_journal_into_bus(journal, bus, speed=0.0)
        for _ in range(20):
            await asyncio.sleep(0)
            if bus.qsize() == 0:
                break
        await asyncio.sleep(0)
        await runner.stop()  # this should write the final flush

    row = garden.get_session(sid)
    garden.close()
    assert row is not None
    assert row.project_path == "/work/proj"


# Silence unused-import if the asyncio module is dragged in only for
# the async fixtures pytest-asyncio uses.
_ = (pytest, asyncio)

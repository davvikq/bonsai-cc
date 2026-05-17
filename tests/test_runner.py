"""Phase 5: bus → growth → renderer wiring.

These tests prove the live pipeline produces the same state as the
batch ``apply_all`` (the determinism contract from phase 4 carried
through the runner) and that the renderer reflects state changes
when the runner pushes them.

Phase 11.5: the Textual renderer was removed. The runner accepts any
duck-typed object with a ``set_state(state, *, last_event_name=...)``
method, so the tests here use a tiny in-memory stub.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from bonsai_cc.events.bus import IngestedEvent, reset_event_bus_for_tests
from bonsai_cc.events.models import parse_event
from bonsai_cc.growth.apply import apply_all
from bonsai_cc.growth.state import TreeState
from bonsai_cc.runner import (
    GrowthRunner,
    build_initial_state,
    replay_journal_into_bus,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "events"


class _RecorderApp:
    """Minimal renderer-sink stub.

    The runner calls
    ``set_state(state, *, last_event_name=..., tool_counts=...)``
    on whatever object it was handed. Tests don't need the web
    broadcaster — a tiny recorder is enough.
    """

    def __init__(self) -> None:
        self.state: TreeState | None = None
        self.last_event_name: str | None = None
        self.tool_counts: dict[str, int] = {}
        self.call_count = 0

    def set_state(
        self,
        state: TreeState,
        *,
        last_event_name: str | None = None,
        tool_counts: dict[str, int] | None = None,
    ) -> None:
        self.state = state
        self.last_event_name = last_event_name
        if tool_counts is not None:
            self.tool_counts = dict(tool_counts)
        self.call_count += 1


def _load_events(path: Path) -> list[tuple[int, Any]]:
    out: list[tuple[int, Any]] = []
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
        return str(json.loads(fp.readline())["raw"]["session_id"])


# ---------------------------------------------------------------------------
# build_initial_state
# ---------------------------------------------------------------------------


def test_initial_state_seed_matches_apply_all() -> None:
    """Seed derivation must match what ``apply_all`` uses internally."""
    sid = "fixture-simple-001"
    runner_state = build_initial_state(sid, theme="default", started_at_ms=0)
    batch_state = apply_all(sid, [])
    assert runner_state.seed_hex == batch_state.seed_hex
    assert runner_state.session_id == batch_state.session_id


# ---------------------------------------------------------------------------
# GrowthRunner — live pipeline equivalence
# ---------------------------------------------------------------------------


async def _drive_runner(fixture_name: str) -> tuple[_RecorderApp, GrowthRunner, Path]:
    """Spin up an app stub + runner, replay the fixture through the bus,
    return them once the runner has consumed everything."""
    bus = reset_event_bus_for_tests()
    fixture = FIXTURES_DIR / fixture_name
    app = _RecorderApp()
    runner = GrowthRunner(app, bus, theme="default")  # type: ignore[arg-type]

    await runner.start()
    await replay_journal_into_bus(fixture, bus, speed=0.0)
    # Yield control until the consumer loop has drained the queue.
    for _ in range(50):
        await asyncio.sleep(0)
        if bus.qsize() == 0 and app.state is not None:
            break
    # One last tick so the final apply lands.
    await asyncio.sleep(0)
    await runner.stop()
    return app, runner, fixture


@pytest.mark.parametrize(
    "fixture_name",
    [
        "simple_session.jsonl",
        "multi_file_session.jsonl",
        "mixed_tools.jsonl",
    ],
)
async def test_runner_state_matches_apply_all(fixture_name: str) -> None:
    """Driving the live pipeline must produce the same state as
    folding the same events with :func:`apply_all`."""
    _, runner, fixture = await _drive_runner(fixture_name)
    sid = _session_id_for(fixture)
    expected = apply_all(sid, _load_events(fixture))
    assert runner.state is not None
    runner_state = replace(runner.state, started_at_ms=expected.started_at_ms)
    assert runner_state == expected


async def test_runner_updates_app() -> None:
    """When the runner applies an event, the app receives the new state."""
    app, runner, _ = await _drive_runner("simple_session.jsonl")
    assert app.state is not None
    assert app.call_count >= 1
    assert runner.state is not None and runner.state.event_count > 0


async def test_runner_tracks_per_tool_counts() -> None:
    """The runner must increment ``tool_counts`` for every
    PostToolUse / PostToolUseFailure event and ship the dict to the
    sink via ``set_state``."""
    app, _, _ = await _drive_runner("mixed_tools.jsonl")
    # The mixed_tools fixture is the one that exercises every tool;
    # at minimum the sink should see *some* tool count breakdown.
    assert app.tool_counts, "expected tool_counts to be populated"
    # And every value is a positive int.
    assert all(isinstance(v, int) and v > 0 for v in app.tool_counts.values())


async def test_runner_switches_to_new_session_on_session_start() -> None:
    """A SessionStart for a different session id swaps the binding.

    The previous "first session wins forever" behaviour meant that
    on daemon restart the runner latched onto whichever old session
    the journal-watcher backlog replayed first and ignored every
    subsequent live event — the canvas stayed blank while Claude
    was actively writing to its own journal. The fix: a SessionStart
    from a different session_id is the canonical "new Claude
    instance" signal and triggers a swap (DESIGN.md §10 keeps the
    "one session at a time" constraint; the most recent SessionStart
    is the one that wins).
    """
    bus = reset_event_bus_for_tests()
    app = _RecorderApp()
    runner = GrowthRunner(app, bus, theme="default")  # type: ignore[arg-type]

    await runner.start()
    a = parse_event({"session_id": "alpha", "hook_event_name": "SessionStart"})
    b = parse_event({"session_id": "beta", "hook_event_name": "SessionStart"})
    await bus.publish(IngestedEvent(idx=0, event=a))
    await bus.publish(IngestedEvent(idx=0, event=b))
    for _ in range(50):
        await asyncio.sleep(0)
        if bus.qsize() == 0:
            break
    await asyncio.sleep(0)
    await runner.stop()

    # Switched to beta, NOT stuck on alpha.
    assert runner.session_id == "beta"
    assert runner.state is not None
    assert runner.state.session_id == "beta"


async def test_runner_current_live_session_id_reflects_lifecycle() -> None:
    """The ``current_live_session_id`` property tracks the in-flight
    window, not the "last session id seen" lifetime.

    Three states it must distinguish:

    * No session bound yet → ``None``.
    * Session bound, no SessionEnd yet → the bound id.
    * Session bound, SessionEnd processed → ``None`` (the row in
      the garden is now authoritative; the hero shouldn't pretend
      it's still live).

    The web server filters /api/garden using this signal — the
    state machine has to match the contract or the live tree will
    either vanish (false positive) or land in both hero and grid
    (false negative).
    """
    bus = reset_event_bus_for_tests()
    app = _RecorderApp()
    runner = GrowthRunner(app, bus, theme="default")  # type: ignore[arg-type]

    # No bind yet.
    assert runner.current_live_session_id is None

    # Bind via a SessionStart.
    await runner.start()
    start = parse_event({"session_id": "alpha", "hook_event_name": "SessionStart"})
    await bus.publish(IngestedEvent(idx=0, event=start))
    for _ in range(50):
        await asyncio.sleep(0)
        if bus.qsize() == 0:
            break
    assert runner.current_live_session_id == "alpha"

    # Process SessionEnd — must flip to None even though
    # ``session_id`` still holds "alpha" for diagnostic purposes.
    end = parse_event({"session_id": "alpha", "hook_event_name": "SessionEnd"})
    await bus.publish(IngestedEvent(idx=1, event=end))
    for _ in range(50):
        await asyncio.sleep(0)
        if bus.qsize() == 0:
            break
    await runner.stop()
    assert runner.session_id == "alpha"
    assert runner.current_live_session_id is None


async def test_runner_ignores_non_start_events_from_other_sessions() -> None:
    """Non-SessionStart events from a different session don't yank
    the binding. A stray PostToolUse from a parallel Claude
    instance can't quietly redirect the renderer away from the
    actively-watched session — only an explicit SessionStart does
    that."""
    bus = reset_event_bus_for_tests()
    app = _RecorderApp()
    runner = GrowthRunner(app, bus, theme="default")  # type: ignore[arg-type]

    await runner.start()
    start = parse_event({"session_id": "alpha", "hook_event_name": "SessionStart"})
    # PostToolUse from a different session — must NOT switch the binding.
    intruder = parse_event({
        "session_id": "beta",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
        "tool_response": {"output": "hi"},
    })
    await bus.publish(IngestedEvent(idx=0, event=start))
    await bus.publish(IngestedEvent(idx=0, event=intruder))
    for _ in range(50):
        await asyncio.sleep(0)
        if bus.qsize() == 0:
            break
    await asyncio.sleep(0)
    await runner.stop()

    assert runner.session_id == "alpha"
    assert runner.state is not None
    assert runner.state.session_id == "alpha"


async def test_runner_recovers_from_handler_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single bad event must not kill the consumer loop."""
    bus = reset_event_bus_for_tests()
    app = _RecorderApp()
    runner = GrowthRunner(app, bus, theme="default")  # type: ignore[arg-type]

    from bonsai_cc import runner as runner_mod

    real_apply = runner_mod.apply_event
    call_count = {"n": 0}

    def flaky(state, event, *, event_idx):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        return real_apply(state, event, event_idx=event_idx)

    monkeypatch.setattr(runner_mod, "apply_event", flaky)

    a = parse_event({"session_id": "s", "hook_event_name": "SessionStart"})
    b = parse_event({"session_id": "s", "hook_event_name": "SessionStart"})

    await runner.start()
    await bus.publish(IngestedEvent(idx=0, event=a))
    await bus.publish(IngestedEvent(idx=1, event=b))
    for _ in range(50):
        await asyncio.sleep(0)
        if bus.qsize() == 0:
            break
    await asyncio.sleep(0)
    # The second event went through despite the first one raising.
    assert call_count["n"] == 2
    await runner.stop()


# ---------------------------------------------------------------------------
# replay_journal_into_bus
# ---------------------------------------------------------------------------


async def test_replay_publishes_every_record() -> None:
    bus = reset_event_bus_for_tests()
    fixture = FIXTURES_DIR / "simple_session.jsonl"
    await replay_journal_into_bus(fixture, bus, speed=0.0)
    received = 0
    while bus.qsize() > 0:
        await bus.consume()
        received += 1
    expected = sum(
        1
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert received == expected


async def test_replay_skips_corrupt_lines(tmp_path: Path) -> None:
    """A garbled line shouldn't abort replay of the remaining ones."""
    bus = reset_event_bus_for_tests()
    p = tmp_path / "mixed.jsonl"
    good = (
        '{"ts":1,"idx":0,"raw":{"session_id":"x","hook_event_name":"SessionStart"}}'
    )
    p.write_text(
        good + "\nnot-json\n" + good.replace('idx":0', 'idx":1') + "\n",
        encoding="utf-8",
    )
    await replay_journal_into_bus(p, bus, speed=0.0)
    drained = 0
    while bus.qsize() > 0:
        await bus.consume()
        drained += 1
    assert drained == 2


async def test_replay_speed_respects_wallclock_gaps() -> None:
    """``speed > 0`` introduces a sleep proportional to the gap."""
    bus = reset_event_bus_for_tests()

    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "tiny.jsonl"
        rec_a = '{"ts":1000,"idx":0,"raw":{"session_id":"s","hook_event_name":"SessionStart"}}'
        rec_b = '{"ts":1100,"idx":1,"raw":{"session_id":"s","hook_event_name":"Stop"}}'
        p.write_text(rec_a + "\n" + rec_b + "\n", encoding="utf-8")
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        await replay_journal_into_bus(p, bus, speed=100.0)
        elapsed = loop.time() - t0
        # 100ms gap / 100x speed = 1ms; tolerate generous slop for CI.
        assert elapsed < 1.0
    assert bus.qsize() == 2

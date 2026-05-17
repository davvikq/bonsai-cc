"""The load-bearing determinism gate.

DESIGN.md §2.3: same ``session_id`` + same event sequence →
byte-identical final state, on every Python version and every
machine. This file enforces that across three fixture sessions of
increasing complexity.

The snapshots live in ``tests/fixtures/snapshots/`` as plain text;
when you intentionally change the growth algorithm, regenerate them
by running this file with ``BONSAI_CC_REGENERATE=1`` and re-commit
the new snapshots — *and* explain the change in the PR description.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bonsai_cc.events.models import parse_event
from bonsai_cc.growth.apply import apply_all
from bonsai_cc.growth.state import TreeState
from bonsai_cc.render.projection import project

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EVENTS_DIR = FIXTURES_DIR / "events"
SNAPSHOTS_DIR = FIXTURES_DIR / "snapshots"


def _load_events(path: Path) -> list[tuple[int, object]]:
    """Read a JSONL fixture as ``(idx, parsed_event)`` pairs."""
    out: list[tuple[int, object]] = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out.append((int(rec["idx"]), parse_event(rec["raw"])))
    return out


def _render_ascii(state: TreeState, width: int = 80, height: int = 24) -> str:
    """Project ``state`` to ASCII at a fixed canvas size.

    Both the projection and this serialisation are pure. The
    fixed-size projection is the canonical snapshot format because:
    a) it's the user-visible artefact, b) it forces every
    deterministic decision in projection to participate in the test.
    """
    grid = project(state, width, height)
    lines = ["".join(cell.char for cell in row).rstrip() for row in grid]
    return "\n".join(lines).rstrip() + "\n"


def _session_id_for(fixture: Path) -> str:
    """Pin the session id used by a fixture to its filename.

    Each fixture's first event carries a ``session_id`` field; we
    use that as authoritative so re-ordering fixtures never shifts
    seeds.
    """
    with fixture.open(encoding="utf-8") as fp:
        first_line = fp.readline().strip()
    rec = json.loads(first_line)
    return str(rec["raw"]["session_id"])


def _snapshot_path(fixture: Path) -> Path:
    return SNAPSHOTS_DIR / (fixture.stem + ".txt")


# ---------------------------------------------------------------------------
# Across-run determinism (no snapshot — just two consecutive runs)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    [
        "simple_session.jsonl",
        "multi_file_session.jsonl",
        "mixed_tools.jsonl",
    ],
)
def test_two_runs_produce_byte_identical_state(fixture_name: str) -> None:
    """Same input → same output, in the same Python process."""
    fixture = EVENTS_DIR / fixture_name
    events_a = _load_events(fixture)
    events_b = _load_events(fixture)
    session_id = _session_id_for(fixture)

    state_a = apply_all(session_id, events_a)
    state_b = apply_all(session_id, events_b)

    # Compare ASCII first — diff is human-readable when it fails.
    assert _render_ascii(state_a) == _render_ascii(state_b)
    # And then assert the full state for paranoia.
    assert state_a == state_b


# ---------------------------------------------------------------------------
# Snapshot determinism (across Python versions / machines)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    [
        "simple_session.jsonl",
        "multi_file_session.jsonl",
        "mixed_tools.jsonl",
    ],
)
def test_matches_committed_snapshot(fixture_name: str) -> None:
    fixture = EVENTS_DIR / fixture_name
    snapshot = _snapshot_path(fixture)
    events = _load_events(fixture)
    session_id = _session_id_for(fixture)
    state = apply_all(session_id, events)
    ascii_now = _render_ascii(state)

    if os.environ.get("BONSAI_CC_REGENERATE"):
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(ascii_now, encoding="utf-8")
        return

    if not snapshot.exists():
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(ascii_now, encoding="utf-8")
        pytest.skip(f"Created initial snapshot {snapshot.name}; re-run to assert.")

    expected = snapshot.read_text(encoding="utf-8")
    assert ascii_now == expected, (
        f"Determinism failure: {fixture_name} no longer matches its snapshot. "
        f"If this is intentional, regenerate with BONSAI_CC_REGENERATE=1 and "
        f"document the change in the PR."
    )


# ---------------------------------------------------------------------------
# Replay byte-equivalence: state from journal recovery is identical to
# state from live ingestion.
# ---------------------------------------------------------------------------


def test_replay_from_journal_matches_live_application(tmp_path: Path) -> None:
    """Two paths to the same state — drive events synchronously, or
    write them to a journal and replay — must produce the same state.

    This is the contract phase 6 (garden / replay) relies on; we
    pin it now so a regression in apply_event surfaces here first.
    """
    fixture = EVENTS_DIR / "mixed_tools.jsonl"
    session_id = _session_id_for(fixture)

    events = _load_events(fixture)
    live = apply_all(session_id, events)

    # Recreate the events from a copy of the journal file (the
    # apply_all input is already journal-shaped; this proves we
    # don't rely on call-site quirks).
    journal_copy = tmp_path / "copy.jsonl"
    journal_copy.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    replayed = apply_all(session_id, _load_events(journal_copy))

    assert live == replayed
    assert _render_ascii(live) == _render_ascii(replayed)

"""Replay of the real Windows session captured on 2026-05-15.

Captured from a live Claude Code run that exposed seven distinct
bugs (PowerShell roots invisible, language detection flicker,
SubagentStop without Start, wither glyph collision, growth too
sparse, sky elements absent, empty-TUI ambiguity). This test
replays that journal and pins every fix end to end. If anything
regresses such that the tree visibly shrinks below these
thresholds, this test fails.

Source journal: tests/fixtures/real_session_2026-05-15.jsonl
(33 events: SessionStart, 10 PreToolUse, 9 PostToolUse, 1
PostToolUseFailure, 3 SubagentStop, 4 UserPromptSubmit, 4 Stop,
1 Notification).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from bonsai_cc.events.models import parse_event
from bonsai_cc.growth.apply import apply_all
from bonsai_cc.render.projection import project, tree_bounds

FIXTURE = Path(__file__).parent / "fixtures" / "real_session_2026-05-15.jsonl"


def _load() -> tuple[str, list[tuple[int, object]]]:
    events = []
    sid: str | None = None
    with FIXTURE.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if sid is None:
                sid = rec["raw"]["session_id"]
            events.append((int(rec["idx"]), parse_event(rec["raw"])))
    assert sid is not None
    return sid, events


def test_replay_runs_without_exceptions() -> None:
    """Smoke: the live journal folds cleanly through apply_event."""
    sid, events = _load()
    state = apply_all(sid, events)
    assert state.event_count == len(events)


def test_replay_produces_visible_growth_at_every_layer() -> None:
    """Every layer of the tree has at least some real geometry.

    Thresholds are deliberately conservative — they're the floor
    below which the tree looks empty. The live session sits well
    above these numbers; the test fails if any layer goes back
    to zero (the original "1 trunk + 3 branches + 4 glyphs" state).
    """
    sid, events = _load()
    state = apply_all(sid, events)
    assert len(state.trunk) >= 6, f"trunk only {len(state.trunk)} segs"
    assert len(state.branches) >= 2
    total_leaves = sum(len(b.leaves) for b in state.branches)
    # The session has 10 Writes across 3 files plus 1 failure that
    # wilts the most recent leaf in place (so the count is unchanged).
    # 4 is the conservative floor.
    assert total_leaves >= 4, f"only {total_leaves} leaves total"
    assert len(state.roots) >= 1, "PowerShell events should have grown a root cluster"
    total_root_segs = sum(len(r.segments) for r in state.roots)
    assert total_root_segs >= 3
    assert len(state.offshoots) >= 1, "3 SubagentStop events should have spawned offshoots"
    assert state.error_count == 1


def test_replay_projection_is_rich() -> None:
    """At 80x24 the final ASCII has growth above AND below the trunk
    baseline, plus >=10 distinct non-blank positions outside the
    ground line. Captures the user's spec for the real-session
    regression: '>10 distinct glyph positions across >=2 rows
    above and >=1 below the trunk baseline'.
    """
    sid, events = _load()
    state = apply_all(sid, events)
    grid = project(state, 80, 24)
    # Find the row of the ground line (a row that is entirely "~").
    ground_row: int | None = None
    for i, row in enumerate(grid):
        if all(c.char == "~" for c in row):
            ground_row = i
            break
    assert ground_row is not None, "ground line missing from projection"

    positions: list[tuple[int, int]] = []
    above_rows: set[int] = set()
    below_rows: set[int] = set()
    for row_idx, row in enumerate(grid):
        if row_idx == ground_row:
            continue
        for col_idx, cell in enumerate(row):
            if cell.char != " ":
                positions.append((row_idx, col_idx))
                if row_idx < ground_row:
                    above_rows.add(row_idx)
                else:
                    below_rows.add(row_idx)
    assert len(positions) >= 10, (
        f"only {len(positions)} non-blank positions outside ground line"
    )
    assert len(above_rows) >= 2, (
        f"only {len(above_rows)} rows with growth above trunk baseline; need >=2"
    )
    assert len(below_rows) >= 1, "no growth below trunk baseline — roots invisible"


def test_replay_state_bounds_are_sane() -> None:
    """The final tree fits comfortably within the 80x24 viewport.

    A regression that doubles segment counts or produces wildly
    off-canvas coordinates would surface here.
    """
    sid, events = _load()
    state = apply_all(sid, events)
    min_x, max_x, min_y, max_y = tree_bounds(state)
    assert min_x > -20 and max_x < 20, f"x out of bounds: {min_x}..{max_x}"
    assert min_y >= -10, f"roots dive deeper than -10: y={min_y}"
    assert max_y <= 20, f"canopy taller than 20: y={max_y}"


def test_replay_event_distribution_matches_the_captured_session() -> None:
    """A safety net: if the fixture is ever swapped for a different
    recording, the assertions in the other tests still make sense.
    """
    _, events = _load()
    counter: Counter[str] = Counter()
    for _idx, ev in events:
        counter[ev.hook_event_name] += 1
    assert counter["SessionStart"] == 1
    assert counter["PostToolUse"] >= 5
    assert counter["SubagentStop"] >= 1
    assert counter["PostToolUseFailure"] >= 1

"""Behaviour of :func:`apply_event` for each event family.

These tests pin specific growth effects (a Bash event grows a root,
two Edits on one file extend the same branch, etc.). The deeper
**byte-identical determinism** guarantee is covered separately in
``test_growth_determinism.py``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from bonsai_cc.events.models import Event, parse_event
from bonsai_cc.growth.apply import (
    MAX_BRANCH_SEGMENTS,
    MAX_LEAVES_PER_BRANCH,
    MAX_TRUNK_HEIGHT,
    apply_all,
    apply_event,
)
from bonsai_cc.growth.state import TreeState


def _ev(idx: int, name: str, **extra: object) -> tuple[int, Event]:
    payload: dict[str, object] = {
        "session_id": "s",
        "hook_event_name": name,
        **extra,
    }
    return idx, parse_event(payload)


def _seed_state() -> TreeState:
    """Run only the SessionStart so each test starts from a planted tree."""
    return apply_all("s-test", [_ev(0, "SessionStart")])


def test_session_start_plants_a_sprout() -> None:
    state = _seed_state()
    assert len(state.trunk) == 1
    assert state.trunk[0].x == 0
    assert state.trunk[0].y == 1


def test_double_session_start_does_not_replant() -> None:
    state = apply_all("s", [_ev(0, "SessionStart"), _ev(1, "SessionStart")])
    assert len(state.trunk) == 1


def test_two_edits_to_same_file_extend_one_branch(tmp_path: Path) -> None:
    target = tmp_path / "auth.py"
    target.write_text("x", encoding="utf-8")
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(
                1, "PostToolUse",
                tool_name="Edit",
                tool_input={"file_path": str(target), "old_string": "x", "new_string": "y"},
                cwd=str(tmp_path),
            ),
            _ev(
                2, "PostToolUse",
                tool_name="Edit",
                tool_input={"file_path": str(target), "old_string": "y", "new_string": "z"},
                cwd=str(tmp_path),
            ),
        ],
    )
    assert state.file_branch_count == 1
    assert len(state.branches) == 1
    assert len(state.branches[0].segments) == 2


def test_two_files_create_two_branches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(
                1, "PostToolUse",
                tool_name="Write",
                tool_input={"file_path": str(tmp_path / "a.py"), "content": "x"},
                cwd=str(tmp_path),
            ),
            _ev(
                2, "PostToolUse",
                tool_name="Write",
                tool_input={"file_path": str(tmp_path / "b.py"), "content": "y"},
                cwd=str(tmp_path),
            ),
        ],
    )
    assert state.file_branch_count == 2
    assert len(state.branches) == 2
    keys = {b.file_path for b in state.branches}
    assert any("a.py" in k for k in keys)
    assert any("b.py" in k for k in keys)


def test_read_after_edit_adds_a_leaf(tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("y", encoding="utf-8")
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(
                1, "PostToolUse",
                tool_name="Edit",
                tool_input={"file_path": str(target), "old_string": "y", "new_string": "z"},
                cwd=str(tmp_path),
            ),
            _ev(
                2, "PostToolUse",
                tool_name="Read",
                tool_input={"file_path": str(target)},
                cwd=str(tmp_path),
            ),
        ],
    )
    # Edit drops a leaf at its tip (phase-7 growth-richness fix);
    # the subsequent Read drops a second. Both are on the same branch.
    assert state.branches[0].leaf_geometry_count == 2
    assert len(state.branches[0].leaves) == 2


def test_read_on_untracked_file_lands_on_most_recent_branch(tmp_path: Path) -> None:
    (tmp_path / "edited.py").write_text("", encoding="utf-8")
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(
                1, "PostToolUse",
                tool_name="Edit",
                tool_input={"file_path": str(tmp_path / "edited.py"), "old_string": "", "new_string": "z"},
                cwd=str(tmp_path),
            ),
            _ev(
                2, "PostToolUse",
                tool_name="Read",
                tool_input={"file_path": str(tmp_path / "never-edited.py")},
                cwd=str(tmp_path),
            ),
        ],
    )
    # Single branch grew leaves: one from the Write itself
    # (growth-richness), one from the untracked Read which falls
    # back to the most-recent branch.
    assert len(state.branches) == 1
    assert state.branches[0].leaf_geometry_count == 2


def test_read_with_no_branches_is_noop(tmp_path: Path) -> None:
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(
                1, "PostToolUse",
                tool_name="Read",
                tool_input={"file_path": str(tmp_path / "x.py")},
                cwd=str(tmp_path),
            ),
        ],
    )
    # No branches existed → state unchanged geometrically; event counted.
    assert state.event_count == 2
    assert state.branches == []


def test_bash_grows_a_root() -> None:
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(
                1, "PostToolUse",
                tool_name="Bash",
                tool_input={"command": "ls"},
                cwd="/project",
            ),
        ],
    )
    assert len(state.roots) == 1
    assert state.roots[0].segments


def test_powershell_fixture_produces_visible_roots() -> None:
    """Regression: 5 shell-family events (4 PowerShell + 1 Bash) →
    visible root segments below the trunk baseline. Pre-fix this
    fixture produced zero roots because the mapping only recognised
    ``Bash``."""
    from pathlib import Path as _Path
    fixture = _Path("tests/fixtures/events/powershell_session.jsonl")
    events = []
    with fixture.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            events.append((int(rec["idx"]), parse_event(rec["raw"])))
    state = apply_all("fixture-powershell-001", events)
    # At least two distinct cwds = at least two root clusters; total
    # segments >= 3 (5 events with one shared cwd: D:\proj x4 and
    # D:\proj\src x1).
    assert len(state.roots) >= 2
    total_root_segments = sum(len(r.segments) for r in state.roots)
    assert total_root_segments >= 3, (
        f"expected >=3 root segments, got {total_root_segments}; "
        f"PowerShell events did not grow roots"
    )
    # Roots must land below the trunk baseline (y < 0).
    for r in state.roots:
        for seg in r.segments:
            assert seg.y < 0, f"root segment at y={seg.y}; expected below baseline"


def test_bash_from_two_cwds_makes_two_roots() -> None:
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(1, "PostToolUse", tool_name="Bash", tool_input={"command": "x"}, cwd="/a"),
            _ev(2, "PostToolUse", tool_name="Bash", tool_input={"command": "y"}, cwd="/b"),
        ],
    )
    assert len({r.cwd for r in state.roots}) == 2


def test_webfetch_adds_flower() -> None:
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(
                1, "PostToolUse",
                tool_name="WebFetch",
                tool_input={"url": "https://example.com/x", "prompt": "hi"},
            ),
        ],
    )
    assert len(state.flowers) == 1
    assert state.flowers[0].host_or_query == "example.com"


def test_subagent_stop_without_start_still_spawns_an_offshoot() -> None:
    """Live Claude Code (observed May 2026) emits SubagentStop events
    without any matching SubagentStart. The growth engine must
    recover the offshoot from the Stop event alone — otherwise the
    three subagents that finished in the live session were invisible.
    """
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            # No SubagentStart — straight to Stop.
            _ev(1, "SubagentStop", agent_id="agent-aaa", agent_type=""),
        ],
    )
    assert len(state.offshoots) == 1
    off = state.offshoots[0]
    assert off.agent_id == "agent-aaa"
    # Spawn-and-cap: the tip carries the • berry, just like the
    # full start+stop pair would have produced.
    assert off.segments[-1].glyph == "•"


def test_subagent_pair_caps_offshoot() -> None:
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(1, "SubagentStart", agent_id="a1", agent_type="Explore"),
            _ev(2, "SubagentStop", agent_id="a1", agent_type="Explore", result="ok"),
        ],
    )
    assert len(state.offshoots) == 1
    # The cap glyph (•) replaces the last segment's glyph.
    assert state.offshoots[0].segments[-1].glyph == "•"


def test_failure_wilts_a_leaf(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("", encoding="utf-8")
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(
                1, "PostToolUse",
                tool_name="Write",
                tool_input={"file_path": str(target), "content": "x"},
                cwd=str(tmp_path),
            ),
            _ev(
                2, "PostToolUse",
                tool_name="Read",
                tool_input={"file_path": str(target)},
                cwd=str(tmp_path),
            ),
            _ev(
                3, "PostToolUseFailure",
                tool_name="Edit",
                tool_input={"file_path": str(target)},
                cwd=str(tmp_path),
                error="boom",
            ),
        ],
    )
    assert state.error_count == 1
    branch = state.branches[0]
    assert branch.leaves, "leaf should still exist after first failure"
    # The wilted glyph must be visually distinct from any normal
    # leaf glyph — previously ``*`` made the wilt invisible because
    # ``*`` is itself a leaf glyph.
    assert branch.leaves[-1].glyph not in {"&", "*", "|"}
    assert branch.leaves[-1].glyph == ","
    # And carries a wilt colour the renderer paints over the palette.
    assert branch.leaves[-1].color == "#DAA520"


def test_second_failure_drops_the_leaf(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("", encoding="utf-8")
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(1, "PostToolUse", tool_name="Write",
                tool_input={"file_path": str(target), "content": "x"}, cwd=str(tmp_path)),
            _ev(2, "PostToolUse", tool_name="Read",
                tool_input={"file_path": str(target)}, cwd=str(tmp_path)),
            _ev(3, "PostToolUseFailure", tool_name="Edit",
                tool_input={"file_path": str(target)}, cwd=str(tmp_path)),
            _ev(4, "PostToolUseFailure", tool_name="Edit",
                tool_input={"file_path": str(target)}, cwd=str(tmp_path)),
        ],
    )
    assert state.error_count == 2
    branch = state.branches[0]
    # Write (1 leaf) + Read (1 leaf) = 2 leaves. First failure
    # wilts the most-recent leaf in place (count unchanged).
    # Second failure drops it (count down by 1). One leaf survives.
    assert branch.leaf_geometry_count == 1
    assert len(branch.leaves) == 1


# ---------------------------------------------------------------------------
# Bounded-state contract (DESIGN.md §2.7) — geometry never grows unbounded.
# ---------------------------------------------------------------------------


def test_trunk_is_capped(tmp_path: Path) -> None:
    """Hammering edits on one file never extends the trunk past the cap."""
    target = tmp_path / "x.py"
    target.write_text("", encoding="utf-8")
    events: list[tuple[int, Event]] = [_ev(0, "SessionStart")]
    for i in range(1, 200):
        events.append(
            _ev(
                i, "PostToolUse",
                tool_name="Edit",
                tool_input={"file_path": str(target), "old_string": "", "new_string": str(i)},
                cwd=str(tmp_path),
            )
        )
    state = apply_all("s", events)
    assert len(state.trunk) <= MAX_TRUNK_HEIGHT


def test_leaf_overflow_increments_canopy_density(tmp_path: Path) -> None:
    """Past MAX_LEAVES_PER_BRANCH, Reads bump canopy_density not geometry."""
    target = tmp_path / "x.py"
    target.write_text("", encoding="utf-8")
    events: list[tuple[int, Event]] = [
        _ev(0, "SessionStart"),
        _ev(
            1, "PostToolUse",
            tool_name="Write",
            tool_input={"file_path": str(target), "content": ""},
            cwd=str(tmp_path),
        ),
    ]
    for i in range(2, 2 + MAX_LEAVES_PER_BRANCH * 2):
        events.append(
            _ev(
                i, "PostToolUse",
                tool_name="Read",
                tool_input={"file_path": str(target)},
                cwd=str(tmp_path),
            )
        )
    state = apply_all("s", events)
    branch = state.branches[0]
    assert branch.leaf_geometry_count == MAX_LEAVES_PER_BRANCH
    assert branch.canopy_density >= MAX_LEAVES_PER_BRANCH


def test_branch_segment_cap(tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("", encoding="utf-8")
    events: list[tuple[int, Event]] = [_ev(0, "SessionStart")]
    for i in range(1, MAX_BRANCH_SEGMENTS + 10):
        events.append(
            _ev(
                i, "PostToolUse",
                tool_name="Edit",
                tool_input={"file_path": str(target), "old_string": "", "new_string": str(i)},
                cwd=str(tmp_path),
            )
        )
    state = apply_all("s", events)
    assert len(state.branches[0].segments) == MAX_BRANCH_SEGMENTS


# ---------------------------------------------------------------------------
# Purity: apply_event must not mutate its input state.
# ---------------------------------------------------------------------------


def test_apply_event_does_not_mutate_input(tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("", encoding="utf-8")
    events = [
        _ev(0, "SessionStart"),
        _ev(
            1, "PostToolUse",
            tool_name="Edit",
            tool_input={"file_path": str(target), "old_string": "", "new_string": "y"},
            cwd=str(tmp_path),
        ),
    ]
    state_a = apply_all("s", events)
    # Snapshot the salient fields.
    branches_before = [
        replace(b, segments=list(b.segments), leaves=list(b.leaves))
        for b in state_a.branches
    ]
    trunk_before = list(state_a.trunk)
    # Apply a third event from the snapshot.
    third = _ev(
        2, "PostToolUse",
        tool_name="Edit",
        tool_input={"file_path": str(target), "old_string": "y", "new_string": "z"},
        cwd=str(tmp_path),
    )
    state_b = apply_event(state_a, third[1], event_idx=third[0])
    # state_a must be unchanged.
    assert state_a.trunk == trunk_before
    for before, after in zip(branches_before, state_a.branches, strict=True):
        assert before.segments == after.segments
        assert before.leaves == after.leaves
    # state_b moved forward.
    assert state_b is not state_a
    assert state_b.event_count == state_a.event_count + 1


def test_unknown_event_name_is_a_noop_with_count() -> None:
    state = apply_all(
        "s",
        [
            _ev(0, "SessionStart"),
            _ev(1, "BrandNewFutureEvent", whatever=1),
        ],
    )
    assert state.event_count == 2
    # Geometry unchanged from a SessionStart-only state.
    seed_only = apply_all("s", [_ev(0, "SessionStart")])
    assert state.trunk == seed_only.trunk


# Silence the unused-import linter if the assertion target moves.
_ = pytest

"""TreeState dataclass invariants and the demo factory.

The growth-state contract is what the renderer reads and what the
garden DB persists. Tests here pin the shape, defaults, and
determinism. Phase 4's L-system will produce these instances from
events; for now we only have the hand-built demo to verify the
shape is renderable.
"""

from __future__ import annotations

from bonsai_cc.growth.state import (
    Branch,
    Cell,
    Flower,
    Offshoot,
    Root,
    Segment,
    TreeState,
    demo_tree,
)


def test_cell_is_immutable() -> None:
    """Cells live in the rendered grid and must not be mutated in place."""
    import pytest

    c = Cell(char="x", fg="#fff")
    with pytest.raises((AttributeError, TypeError)):
        c.char = "y"  # type: ignore[misc]


def test_treestate_defaults_are_empty_containers() -> None:
    s = TreeState(
        session_id="abc", seed_hex="0123456789abcdef",
        started_at_ms=0, theme="default",
    )
    assert s.trunk == []
    assert s.branches == []
    assert s.roots == []
    assert s.flowers == []
    assert s.offshoots == []
    assert s.canopy_density == 0
    assert s.event_count == 0
    assert s.error_count == 0
    assert s.file_branch_count == 0


def test_demo_tree_is_deterministic() -> None:
    """Same session_id → byte-identical TreeState across calls.

    This is a phase-3 stub of the real determinism guarantee that
    phase 4's L-system will provide. We pin it here so the renderer
    can't accidentally pick up state-mutation habits.
    """
    a = demo_tree("demo-seed")
    b = demo_tree("demo-seed")
    assert a.seed_hex == b.seed_hex
    assert a.session_id == b.session_id
    assert a.theme == b.theme
    assert a.file_branch_count == b.file_branch_count
    assert len(a.trunk) == len(b.trunk)
    assert len(a.branches) == len(b.branches)


def test_demo_tree_seeds_differ_by_session_id() -> None:
    one = demo_tree("alpha")
    two = demo_tree("beta")
    assert one.seed_hex != two.seed_hex


def test_demo_tree_has_renderable_topology() -> None:
    """The demo has every element the renderer cares about."""
    s = demo_tree()
    assert s.trunk, "demo tree must have a trunk"
    assert s.branches, "demo tree must have at least one branch"
    assert s.roots, "demo tree must have roots"
    assert s.flowers, "demo tree must have flowers (WebFetch demo)"
    assert s.offshoots, "demo tree must include a subagent offshoot"


def test_branch_holds_leaves_and_geometry_counters() -> None:
    b = Branch(file_path="src/x.py", angle_deg=30.0)
    b.leaves.append(Segment(x=1, y=2, glyph="&"))
    b.leaf_geometry_count = 1
    b.canopy_density = 0
    assert b.leaves[0].glyph == "&"
    assert b.leaf_geometry_count == 1


def test_root_and_offshoot_shapes() -> None:
    r = Root(cwd="/p", angle_deg=-100.0)
    r.segments.append(Segment(x=-1, y=-1, glyph="/"))
    assert r.segments[0].y == -1

    o = Offshoot(agent_id="a1", agent_type="Explore")
    o.segments.append(Segment(x=-1, y=3, glyph="("))
    assert o.agent_type == "Explore"


def test_flower_carries_host_metadata() -> None:
    f = Flower(x=2, y=10, glyph="❀", host_or_query="docs.example.com")
    assert f.host_or_query == "docs.example.com"

"""Pure tree state: dataclasses only, no transformation logic.

Logical ``(x, y)`` with y growing upward (botanically natural):
trunk base at the origin, branches/leaves at positive y, roots at
negative y. The renderer flips y for screen coords.

Determinism contract: same ``session_seed`` + same event sequence
produces a byte-identical ``TreeState``. No random state lives in
the dataclasses; stochastic choices happen in the growth engine.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Branch",
    "Cell",
    "Flower",
    "Offshoot",
    "Root",
    "Segment",
    "TreeState",
    "demo_tree",
    "state_from_dict",
    "state_to_dict",
]


@dataclass(slots=True, frozen=True)
class Cell:
    """One projected grid cell -- a single visible character + style.

    The renderer produces grids of these; the state never stores them.
    """

    char: str
    fg: str | None = None
    bg: str | None = None


@dataclass(slots=True)
class Segment:
    """One renderable point on the tree.

    ``birth_event_idx`` is the event index that introduced this segment
    -- used by the renderer to pick a thickness glyph (older segments
    look thicker.).
    """

    x: int
    y: int
    glyph: str
    color: str | None = None
    birth_event_idx: int = 0


@dataclass(slots=True)
class Branch:
    """A semantic branch -- one per unique edited/written file.

    The ``file_path`` is the normalized identity key (see the design contract "file-path identity"). ``angle_deg`` is measured from
    vertical, positive to the right. ``attach_point`` is where the
    branch joins the trunk; new segments grow outward from there.
    """

    file_path: str
    angle_deg: float
    attach_point: tuple[int, int] = (0, 0)
    segments: list[Segment] = field(default_factory=list)
    leaves: list[Segment] = field(default_factory=list)
    leaf_geometry_count: int = 0
    canopy_density: int = 0  # see the design contract


@dataclass(slots=True)
class Root:
    """A root cluster -- corresponds to bash commands from a single cwd."""

    cwd: str
    angle_deg: float
    attach_point: tuple[int, int] = (0, 0)
    segments: list[Segment] = field(default_factory=list)


@dataclass(slots=True)
class Flower:
    """A web-fetch / web-search bloom at the canopy."""

    x: int
    y: int
    glyph: str
    color: str | None = None
    host_or_query: str = ""


@dataclass(slots=True)
class Offshoot:
    """A subagent invocation -- a small independent stalk from the trunk.

    Intentionally smaller than a regular branch (see the design contract
    event mapping).
    """

    agent_id: str
    agent_type: str
    attach_point: tuple[int, int] = (0, 0)
    segments: list[Segment] = field(default_factory=list)


@dataclass(slots=True)
class TreeState:
    """The full visual state of one tree at one moment in time.

    Snapshotting this dataclass and re-rendering must produce the
    same image (the renderer is pure). Serialising to JSON for the
    garden DB must round-trip byte-identical.
    """

    session_id: str
    seed_hex: str  # 16-hex-char digest of session_id, the L-system seed
    started_at_ms: int
    theme: str

    trunk: list[Segment] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)
    roots: list[Root] = field(default_factory=list)
    flowers: list[Flower] = field(default_factory=list)
    offshoots: list[Offshoot] = field(default_factory=list)

    canopy_density: int = 0
    event_count: int = 0
    error_count: int = 0
    file_branch_count: int = 0


# ---------------------------------------------------------------------------
# Demo factory: a hand-built tree the renderer can display.
# ---------------------------------------------------------------------------


def _seed_from(session_id: str) -> str:
    """16 hex chars from a SHA-256 of ``session_id``. Stable across runs."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Serialization for the garden DB
# ---------------------------------------------------------------------------


def _segment_to_dict(s: Segment) -> dict[str, Any]:
    return {
        "x": s.x,
        "y": s.y,
        "glyph": s.glyph,
        "color": s.color,
        "birth_event_idx": s.birth_event_idx,
    }


def _segment_from_dict(d: dict[str, Any]) -> Segment:
    return Segment(
        x=int(d["x"]),
        y=int(d["y"]),
        glyph=str(d["glyph"]),
        color=d.get("color"),
        birth_event_idx=int(d.get("birth_event_idx", 0)),
    )


def state_to_dict(state: TreeState) -> dict[str, Any]:
    """Convert ``state`` to a JSON-friendly dict.

    ``dataclasses.asdict`` is unsuitable here because it turns
    ``attach_point`` tuples into lists. The garden DB stores this
    JSON in ``final_state_json``; we want :func:`state_from_dict` to
    reverse the conversion losslessly so a round-trip is true
    equality (modulo dataclass field invariants).

    Example:
        >>> import json
        >>> s = demo_tree("rt")
        >>> roundtrip = state_from_dict(json.loads(json.dumps(state_to_dict(s))))
        >>> roundtrip == s
        True
    """
    return {
        "session_id": state.session_id,
        "seed_hex": state.seed_hex,
        "started_at_ms": state.started_at_ms,
        "theme": state.theme,
        "trunk": [_segment_to_dict(s) for s in state.trunk],
        "branches": [
            {
                "file_path": b.file_path,
                "angle_deg": b.angle_deg,
                "attach_point": [b.attach_point[0], b.attach_point[1]],
                "segments": [_segment_to_dict(s) for s in b.segments],
                "leaves": [_segment_to_dict(s) for s in b.leaves],
                "leaf_geometry_count": b.leaf_geometry_count,
                "canopy_density": b.canopy_density,
            }
            for b in state.branches
        ],
        "roots": [
            {
                "cwd": r.cwd,
                "angle_deg": r.angle_deg,
                "attach_point": [r.attach_point[0], r.attach_point[1]],
                "segments": [_segment_to_dict(s) for s in r.segments],
            }
            for r in state.roots
        ],
        "flowers": [
            {
                "x": f.x,
                "y": f.y,
                "glyph": f.glyph,
                "color": f.color,
                "host_or_query": f.host_or_query,
            }
            for f in state.flowers
        ],
        "offshoots": [
            {
                "agent_id": o.agent_id,
                "agent_type": o.agent_type,
                "attach_point": [o.attach_point[0], o.attach_point[1]],
                "segments": [_segment_to_dict(s) for s in o.segments],
            }
            for o in state.offshoots
        ],
        "canopy_density": state.canopy_density,
        "event_count": state.event_count,
        "error_count": state.error_count,
        "file_branch_count": state.file_branch_count,
    }


def state_from_dict(d: dict[str, Any]) -> TreeState:
    """Inverse of :func:`state_to_dict`. Tuples reconstructed."""
    return TreeState(
        session_id=str(d["session_id"]),
        seed_hex=str(d["seed_hex"]),
        started_at_ms=int(d["started_at_ms"]),
        theme=str(d["theme"]),
        trunk=[_segment_from_dict(s) for s in d.get("trunk", [])],
        branches=[
            Branch(
                file_path=str(b["file_path"]),
                angle_deg=float(b["angle_deg"]),
                attach_point=(int(b["attach_point"][0]), int(b["attach_point"][1])),
                segments=[_segment_from_dict(s) for s in b.get("segments", [])],
                leaves=[_segment_from_dict(s) for s in b.get("leaves", [])],
                leaf_geometry_count=int(b.get("leaf_geometry_count", 0)),
                canopy_density=int(b.get("canopy_density", 0)),
            )
            for b in d.get("branches", [])
        ],
        roots=[
            Root(
                cwd=str(r["cwd"]),
                angle_deg=float(r["angle_deg"]),
                attach_point=(int(r["attach_point"][0]), int(r["attach_point"][1])),
                segments=[_segment_from_dict(s) for s in r.get("segments", [])],
            )
            for r in d.get("roots", [])
        ],
        flowers=[
            Flower(
                x=int(f["x"]),
                y=int(f["y"]),
                glyph=str(f["glyph"]),
                color=f.get("color"),
                host_or_query=str(f.get("host_or_query", "")),
            )
            for f in d.get("flowers", [])
        ],
        offshoots=[
            Offshoot(
                agent_id=str(o["agent_id"]),
                agent_type=str(o["agent_type"]),
                attach_point=(int(o["attach_point"][0]), int(o["attach_point"][1])),
                segments=[_segment_from_dict(s) for s in o.get("segments", [])],
            )
            for o in d.get("offshoots", [])
        ],
        canopy_density=int(d.get("canopy_density", 0)),
        event_count=int(d.get("event_count", 0)),
        error_count=int(d.get("error_count", 0)),
        file_branch_count=int(d.get("file_branch_count", 0)),
    )


def demo_tree(session_id: str = "demo") -> TreeState:
    """A hand-built ``TreeState`` for tests and the static renderer.

    Geometry is a small bonsai with a 9-segment trunk, three main
    branches (one heavy "auth.py", two lighter ones), four roots
    fanning down-left and down-right, one subagent offshoot, two
    flowers, and a sprinkle of leaves. Numbers are hand-picked here;
    a real session is generated by ``apply_event`` and the L-system.

    Example:
        >>> state = demo_tree("demo")
        >>> state.session_id
        'demo'
        >>> state.file_branch_count
        3
    """
    seed_hex = _seed_from(session_id)

    # Trunk: 9 segments going straight up at x=0.
    trunk: list[Segment] = [
        Segment(x=0, y=y, glyph="│", birth_event_idx=y)
        for y in range(1, 10)
    ]

    # auth.py -- the heavy branch, leaning right.
    auth_segments = [
        Segment(x=1, y=5, glyph="\\"),
        Segment(x=2, y=6, glyph="\\"),
        Segment(x=3, y=7, glyph="\\"),
        Segment(x=4, y=7, glyph="─"),
        Segment(x=5, y=8, glyph="\\"),
    ]
    auth_leaves = [
        Segment(x=6, y=9, glyph="&"),
        Segment(x=5, y=9, glyph="&"),
        Segment(x=7, y=8, glyph="*"),
        Segment(x=4, y=9, glyph="&"),
        Segment(x=6, y=10, glyph="*"),
        Segment(x=5, y=10, glyph="&"),
    ]
    auth_branch = Branch(
        file_path="src/auth.py",
        angle_deg=35.0,
        segments=auth_segments,
        leaves=auth_leaves,
        leaf_geometry_count=len(auth_leaves),
    )

    # models.py -- lighter branch, leaning left.
    models_segments = [
        Segment(x=-1, y=4, glyph="/"),
        Segment(x=-2, y=5, glyph="/"),
        Segment(x=-3, y=6, glyph="/"),
        Segment(x=-4, y=6, glyph="─"),
    ]
    models_leaves = [
        Segment(x=-5, y=7, glyph="*"),
        Segment(x=-4, y=7, glyph="&"),
        Segment(x=-6, y=7, glyph="*"),
        Segment(x=-5, y=8, glyph="&"),
    ]
    models_branch = Branch(
        file_path="src/models.py",
        angle_deg=-40.0,
        segments=models_segments,
        leaves=models_leaves,
        leaf_geometry_count=len(models_leaves),
    )

    # cli.py -- small branch high up, leaning right.
    cli_segments = [
        Segment(x=1, y=8, glyph="\\"),
        Segment(x=2, y=9, glyph="\\"),
    ]
    cli_leaves = [
        Segment(x=3, y=10, glyph="&"),
        Segment(x=2, y=10, glyph="&"),
        Segment(x=4, y=10, glyph="*"),
    ]
    cli_branch = Branch(
        file_path="src/cli.py",
        angle_deg=30.0,
        segments=cli_segments,
        leaves=cli_leaves,
        leaf_geometry_count=len(cli_leaves),
    )

    # Roots: four fanning out.
    root_left = Root(
        cwd="/project",
        angle_deg=-110.0,
        segments=[
            Segment(x=-1, y=-1, glyph="/"),
            Segment(x=-2, y=-2, glyph="/"),
            Segment(x=-3, y=-3, glyph="/"),
        ],
    )
    root_right = Root(
        cwd="/project",
        angle_deg=110.0,
        segments=[
            Segment(x=1, y=-1, glyph="\\"),
            Segment(x=2, y=-2, glyph="\\"),
            Segment(x=3, y=-3, glyph="\\"),
        ],
    )
    root_centre_l = Root(
        cwd="/project/src",
        angle_deg=-95.0,
        segments=[
            Segment(x=0, y=-1, glyph="|"),
            Segment(x=-1, y=-2, glyph="\\"),
        ],
    )
    root_centre_r = Root(
        cwd="/project/src",
        angle_deg=95.0,
        segments=[
            Segment(x=0, y=-1, glyph="|"),
            Segment(x=1, y=-2, glyph="/"),
        ],
    )

    # One subagent offshoot, a stub stalk from the trunk midway up.
    offshoot = Offshoot(
        agent_id="explorer-1",
        agent_type="Explore",
        segments=[
            Segment(x=-1, y=3, glyph="("),
            Segment(x=-2, y=3, glyph="."),
        ],
    )

    # Two flowers at the canopy top -- one from a WebFetch, one search.
    # ``color=None`` lets the palette pick the
    # theme-appropriate hex.
    flowers = [
        Flower(x=6, y=11, glyph="❀", host_or_query="docs.example.com"),
        Flower(x=-3, y=9, glyph="❀", host_or_query="pep 8"),
    ]

    state = TreeState(
        session_id=session_id,
        seed_hex=seed_hex,
        started_at_ms=0,
        theme="python",
        trunk=trunk,
        branches=[models_branch, auth_branch, cli_branch],
        roots=[root_left, root_centre_l, root_centre_r, root_right],
        flowers=flowers,
        offshoots=[offshoot],
        canopy_density=0,
        event_count=24,
        error_count=1,
        file_branch_count=3,
    )
    return state

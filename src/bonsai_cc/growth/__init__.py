"""Pure tree state plus the deterministic growth engine.

Exposes :class:`TreeState` (the data shape), :func:`apply_event` (the
event-folding transformation), and the supporting L-system helpers.

Architectural seam: per DESIGN.md, this package consumes validated
events from :mod:`bonsai_cc.events.bus` and nothing else.
``tests/test_architectural_seam.py`` enforces this and will fail
the build if it is violated.
"""

from bonsai_cc.growth.apply import apply_all, apply_event
from bonsai_cc.growth.attach import (
    AttachmentIntent,
    AttachmentKind,
    attach_intent,
    normalize_path,
)
from bonsai_cc.growth.lsystem import (
    angle_for,
    event_rng,
    seed_from_session_id,
)
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

__all__ = [
    "AttachmentIntent",
    "AttachmentKind",
    "Branch",
    "Cell",
    "Flower",
    "Offshoot",
    "Root",
    "Segment",
    "TreeState",
    "angle_for",
    "apply_all",
    "apply_event",
    "attach_intent",
    "demo_tree",
    "event_rng",
    "normalize_path",
    "seed_from_session_id",
]

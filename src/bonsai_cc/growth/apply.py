"""``apply_event(state, event) -> state`` -- the pure heart of the engine.

The growth engine is one pure function. Same input state + same event
+ same event index produces byte-identical output state, on every
Python version we support and on every machine. That's the contract
that gives us replay, garden persistence, and the determinism test.

Implementation rules
--------------------
* No global mutable state. RNG is seeded per event via
  :func:`bonsai_cc.growth.lsystem.event_rng`.
* No in-place mutation of any input. Helpers return new
  ``TreeState`` instances via :func:`dataclasses.replace` and
  freshly-built lists. The renderer caches the previous state and
  must be able to compare it against the new one.
* No filesystem reads except via :func:`normalize_path`. Network /
  clock access is banned outright.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import replace

from bonsai_cc.events.models import (
    BaseHookEvent,
    PostToolUseEvent,
    PostToolUseFailureEvent,
    SessionStartEvent,
)
from bonsai_cc.growth.attach import (
    AttachmentIntent,
    AttachmentKind,
    attach_intent,
)
from bonsai_cc.growth.lsystem import (
    angle_for,
    branch_glyph_for_angle,
    event_rng,
    pick_one,
    root_glyph_for_angle,
    seed_from_session_id,
)
from bonsai_cc.growth.state import (
    Branch,
    Flower,
    Offshoot,
    Root,
    Segment,
    TreeState,
)

__all__ = ["MAX_LEAVES_PER_BRANCH", "MAX_TRUNK_HEIGHT", "apply_all", "apply_event"]


# Bounded-state contract from DESIGN.md §2.7. The apply path treats
# these as inviolable so state stays bounded regardless of how many
# events arrive.
MAX_TRUNK_HEIGHT = 14
MAX_LEAVES_PER_BRANCH = 8
MAX_BRANCH_SEGMENTS = 8
MAX_ROOT_SEGMENTS = 6
MAX_OFFSHOOT_SEGMENTS = 4

# Leaf glyphs the engine sprinkles. The renderer's palette decides
# colour; the glyph itself is part of state for the determinism gate.
_LEAF_GLYPHS = ("&", "*", "|")
_OFFSHOOT_STALK = "("

# Wilted-leaf glyph. Must be visually distinct from every value in
# ``_LEAF_GLYPHS`` -- the previous version used ``"*"`` which is
# already a normal leaf glyph, so the failure was invisible.
# ``,`` reads as a fallen / drooping leaf in monospace.
_WILTED_LEAF_GLYPH = ","


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def apply_event(
    state: TreeState, event: BaseHookEvent, *, event_idx: int
) -> TreeState:
    """Return a new ``TreeState`` reflecting ``event``.

    ``state`` is never mutated. ``event_idx`` is the per-session
    monotonically increasing event index that drives the RNG; the
    runner derives it from line position in the journal and passes
    it through here.
    """
    seed = _state_seed(state)
    rng = event_rng(seed, event_idx)
    intent = attach_intent(event)

    # Always bump the event counter, even for no-ops, so the stats
    # footer reflects real activity volume.
    next_state = replace(state, event_count=state.event_count + 1)

    # Trunk pulse: every tool call (success or failure) makes the
    # trunk a notch taller, capped by MAX_TRUNK_HEIGHT. This is what
    # turns "the session itself" into trunk growth (DESIGN.md §2.1).
    # We do this *before* the intent-specific effect so a freshly
    # created branch attaches at the new, taller trunk.
    if isinstance(event, PostToolUseEvent | PostToolUseFailureEvent):
        next_state = _extend_trunk(next_state, event_idx)

    if intent.kind == AttachmentKind.SEED:
        return _plant_seed(next_state, event, event_idx)
    if intent.kind == AttachmentKind.GROW_BRANCH:
        # Edit / Write extends the branch AND drops a leaf at the
        # new tip. Without this an all-write session (10 Writes
        # across 3 files in the May 2026 live recording) produced
        # only branch sticks with zero canopy -- visually sparse.
        grown = _grow_branch(next_state, intent, rng, event_idx)
        return _add_leaf(grown, intent, rng, event_idx)
    if intent.kind == AttachmentKind.ADD_LEAF:
        return _add_leaf(next_state, intent, rng, event_idx)
    if intent.kind == AttachmentKind.ADD_LEAF_CLUSTER:
        return _add_leaf_cluster(next_state, rng, event_idx)
    if intent.kind == AttachmentKind.GROW_ROOT:
        return _grow_root(next_state, intent, rng, event_idx)
    if intent.kind == AttachmentKind.ADD_FLOWER:
        return _add_flower(next_state, intent, rng, event_idx)
    if intent.kind == AttachmentKind.SPAWN_OFFSHOOT:
        return _spawn_offshoot(next_state, intent, event_idx)
    if intent.kind == AttachmentKind.CAP_OFFSHOOT:
        return _cap_offshoot(next_state, intent, event_idx)
    if intent.kind == AttachmentKind.WITHER:
        return _wither(next_state, intent, event_idx)
    return next_state  # NO_OP


def apply_all(
    session_id: str,
    events: Iterable[tuple[int, BaseHookEvent]],
    *,
    theme: str = "default",
    started_at_ms: int = 0,
) -> TreeState:
    """Convenience: build a fresh state and fold every event into it.

    ``events`` is a sequence of ``(event_idx, event)`` pairs -- same
    shape the journal produces (``record["idx"]`` + parsed payload).
    The return value is the final state; the determinism test
    asserts byte-identity between two ``apply_all`` runs with the
    same inputs.
    """
    state = _initial_state(session_id, theme=theme, started_at_ms=started_at_ms)
    for idx, ev in events:
        state = apply_event(state, ev, event_idx=idx)
    return state


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def _initial_state(
    session_id: str, *, theme: str, started_at_ms: int
) -> TreeState:
    """Construct the empty state for a fresh session."""
    seed = seed_from_session_id(session_id)
    seed_hex = f"{seed:016x}"
    return TreeState(
        session_id=session_id,
        seed_hex=seed_hex,
        started_at_ms=started_at_ms,
        theme=theme,
    )


def _state_seed(state: TreeState) -> int:
    """Recover the integer session seed from ``state.seed_hex``."""
    return int(state.seed_hex, 16)


# ---------------------------------------------------------------------------
# Trunk
# ---------------------------------------------------------------------------


def _plant_seed(state: TreeState, event: BaseHookEvent, event_idx: int) -> TreeState:
    """Initial sprout. Re-plants are idempotent (resume sessions).

    If the trunk already has segments (we're handling a
    ``source=resume`` ``SessionStart`` after replay), don't double-
    plant. ``started_at_ms`` is left alone -- replay restores the
    original timestamp.
    """
    if state.trunk:
        return state
    sprout = Segment(x=0, y=1, glyph="│", birth_event_idx=event_idx)
    started = state.started_at_ms
    if started == 0 and isinstance(event, SessionStartEvent):
        # We don't have a wall clock here (apply_event is pure); the
        # runner that drives apply_event passes started_at via
        # apply_all's started_at_ms argument.
        started = 0
    return replace(state, trunk=[sprout], started_at_ms=started)


def _extend_trunk(state: TreeState, event_idx: int) -> TreeState:
    """Grow one trunk segment, capped at :data:`MAX_TRUNK_HEIGHT`.

    Trunk segments stack vertically at ``x=0``. Glyph thickness is
    a render-time concern (DESIGN.md §2.2 step 6); state only stores
    the base glyph.
    """
    if not state.trunk:
        # Seed first if we somehow get an extend before SessionStart.
        return _plant_seed(state, _DummyEvent(), event_idx)
    if len(state.trunk) >= MAX_TRUNK_HEIGHT:
        return state
    next_y = state.trunk[-1].y + 1
    new_seg = Segment(x=0, y=next_y, glyph="│", birth_event_idx=event_idx)
    return replace(state, trunk=[*state.trunk, new_seg])


class _DummyEvent(BaseHookEvent):
    """Placeholder used only for ``_extend_trunk`` reseeding."""

    session_id: str = "_internal"
    hook_event_name: str = "_extend"


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------


def _find_branch_by_key(state: TreeState, key: str) -> int | None:
    for i, b in enumerate(state.branches):
        if b.file_path == key:
            return i
    return None


def _grow_branch(
    state: TreeState,
    intent: AttachmentIntent,
    rng: random.Random,
    event_idx: int,
) -> TreeState:
    """Extend (or create) the semantic branch for ``intent.file_path_key``.

    The first event for a file creates the branch and gives it one
    segment. Subsequent events extend the branch in its established
    direction until ``MAX_BRANCH_SEGMENTS``; beyond that, additional
    edits are no-ops at the geometry level (events still count).
    """
    if not intent.file_path_key:
        return state

    state = _ensure_trunk(state, event_idx)
    branch_idx = _find_branch_by_key(state, intent.file_path_key)
    if branch_idx is None:
        return _create_branch(state, intent.file_path_key, rng, event_idx)
    branch = state.branches[branch_idx]
    if len(branch.segments) >= MAX_BRANCH_SEGMENTS:
        return state
    new_seg = _next_branch_segment(branch, event_idx)
    new_branch = replace(branch, segments=[*branch.segments, new_seg])
    return _replace_branch(state, branch_idx, new_branch)


def _create_branch(
    state: TreeState, file_path_key: str, rng: random.Random, event_idx: int
) -> TreeState:
    """Create a fresh branch for a previously-unseen file.

    Attachment height is drawn from the rng (uniformly across the
    upper two-thirds of the current trunk) and the angle is hashed
    from the file path so re-renders look the same. Angle is
    clamped so every branch lands ≥1 column off the trunk on its
    very first segment -- no branch overlaps its own root.
    """
    state = _ensure_trunk(state, event_idx)
    angle = angle_for(file_path_key, min_deg=38.0, max_deg=62.0)

    trunk_height = len(state.trunk)
    min_attach = max(1, trunk_height // 3)
    # Clamp to range we actually have so a 1-segment trunk still works.
    max_attach = max(min_attach, trunk_height - 1)
    attach_y = (
        rng.randint(min_attach, max_attach)
        if max_attach > min_attach
        else max_attach
    )
    attach_point = (0, attach_y)

    branch = Branch(
        file_path=file_path_key,
        angle_deg=angle,
        attach_point=attach_point,
    )
    first_seg = _next_branch_segment(branch, event_idx)
    branch = replace(branch, segments=[first_seg])
    return replace(
        state,
        branches=[*state.branches, branch],
        file_branch_count=state.file_branch_count + 1,
    )


def _next_branch_segment(branch: Branch, event_idx: int) -> Segment:
    """Compute the next segment along ``branch``'s direction.

    Uses integer stair-stepping (instead of round(sin*step)) because:
    a) it lives natively on the grid -- no decision to round across
    the trunk column, b) it produces the cbonsai-style stepped
    silhouette without needing curve interpolation.

    Three regimes by absolute angle:

    * shallow (<42°) -- branch climbs, with ``dx`` accumulating once
      every two steps. Tight, near-vertical.
    * mid (42-58°) -- diagonal: ``dx`` and ``dy`` track each other.
    * wide (>58°) -- bowed: ``dx`` per step, ``dy`` only every other.
    """
    step = len(branch.segments) + 1
    abs_a = abs(branch.angle_deg)
    direction = 1 if branch.angle_deg > 0 else -1
    if abs_a < 42:
        dx = direction * ((step + 1) // 2)
        dy = step
    elif abs_a < 58:
        dx = direction * step
        dy = step
    else:
        dx = direction * step
        dy = (step + 1) // 2
    attach_x, attach_y = branch.attach_point
    return Segment(
        x=attach_x + dx,
        y=attach_y + dy,
        glyph=branch_glyph_for_angle(branch.angle_deg),
        birth_event_idx=event_idx,
    )


# ---------------------------------------------------------------------------
# Leaves
# ---------------------------------------------------------------------------


def _add_leaf(
    state: TreeState,
    intent: AttachmentIntent,
    rng: random.Random,
    event_idx: int,
) -> TreeState:
    """Add a single leaf to the most-relevant branch.

    Resolution order:

    1. If the intent names a tracked file, attach to that branch.
    2. Otherwise attach to the most-recently-grown branch.
    3. If there are no branches at all yet, the event is a no-op
       (we don't conjure a branch for a Read of an untracked file).
    """
    if not state.branches:
        return state
    if intent.file_path_key:
        idx = _find_branch_by_key(state, intent.file_path_key)
        if idx is None:
            idx = len(state.branches) - 1
    else:
        idx = len(state.branches) - 1
    branch = state.branches[idx]
    new_branch = _append_leaf(branch, rng, event_idx, glyph=None)
    return _replace_branch(state, idx, new_branch)


def _add_leaf_cluster(
    state: TreeState, rng: random.Random, event_idx: int
) -> TreeState:
    """Drop a small cluster (3 leaves) on the most-recent branch.

    Grep / Glob produce these -- they probe many files but don't
    bind to a specific one. Three is the smallest count that reads
    as a cluster and stays inside the bounded-state contract.
    """
    if not state.branches:
        return state
    idx = len(state.branches) - 1
    branch = state.branches[idx]
    for _ in range(3):
        branch = _append_leaf(branch, rng, event_idx, glyph=None)
    return _replace_branch(state, idx, branch)


def _append_leaf(
    branch: Branch,
    rng: random.Random,
    event_idx: int,
    *,
    glyph: str | None,
) -> Branch:
    """Return a copy of ``branch`` with one more leaf (subject to caps).

    Once the geometry cap is reached, the leaf count moves into
    ``canopy_density`` instead -- see DESIGN.md §2.7. The renderer
    uses that counter to deepen the canopy's shading; geometry stays
    bounded forever.
    """
    if branch.leaf_geometry_count >= MAX_LEAVES_PER_BRANCH:
        return replace(branch, canopy_density=branch.canopy_density + 1)
    tip_x, tip_y = _branch_tip(branch)
    dx = rng.choice((-1, 0, 1))
    dy = rng.choice((0, 0, 1))  # bias toward the canopy
    leaf = Segment(
        x=tip_x + dx,
        y=tip_y + dy,
        glyph=glyph or pick_one(rng, _LEAF_GLYPHS),
        birth_event_idx=event_idx,
    )
    return replace(
        branch,
        leaves=[*branch.leaves, leaf],
        leaf_geometry_count=branch.leaf_geometry_count + 1,
    )


def _branch_tip(branch: Branch) -> tuple[int, int]:
    """Return the (x, y) of the most recent segment, or the attach
    point if the branch is still a stub."""
    if not branch.segments:
        return branch.attach_point
    last = branch.segments[-1]
    return last.x, last.y


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


def _find_root_by_cwd(state: TreeState, cwd_key: str) -> int | None:
    for i, r in enumerate(state.roots):
        if r.cwd == cwd_key:
            return i
    return None


def _grow_root(
    state: TreeState,
    intent: AttachmentIntent,
    rng: random.Random,
    event_idx: int,
) -> TreeState:
    """Extend (or create) a root cluster for the event's cwd."""
    cwd_key = intent.cwd_key or ""
    idx = _find_root_by_cwd(state, cwd_key)
    if idx is None:
        return _create_root(state, cwd_key, rng, event_idx)
    root = state.roots[idx]
    if len(root.segments) >= MAX_ROOT_SEGMENTS:
        return state
    new_seg = _next_root_segment(root, event_idx)
    new_root = replace(root, segments=[*root.segments, new_seg])
    return _replace_root(state, idx, new_root)


def _create_root(
    state: TreeState, cwd_key: str, rng: random.Random, event_idx: int
) -> TreeState:
    """Spawn a new root cluster. Direction hashed from ``cwd_key``.

    Roots use angles in ``[110°, 145°]`` measured from upward
    vertical -- i.e. always pointing down-and-out. Sign decides left
    vs right; the rng provides a stable per-cwd flip so roots from
    different cwds don't all pile on the same side.
    """
    magnitude = angle_for(cwd_key or "_unknown", min_deg=110.0, max_deg=145.0)
    angle = -abs(magnitude) if rng.random() < 0.5 else abs(magnitude)
    root = Root(cwd=cwd_key, angle_deg=angle, attach_point=(0, 0))
    first = _next_root_segment(root, event_idx)
    return replace(state, roots=[*state.roots, replace(root, segments=[first])])


def _next_root_segment(root: Root, event_idx: int) -> Segment:
    """Step the root one notch further from the base, going *down*.

    Same integer-stair scheme as branches but with negative ``dy``.
    "Steepness" is measured as deviation from straight-down (180°):

    * 0-25° (steep) -- mostly straight down, ``dx`` every two steps.
    * 25-45° (diagonal) -- ``dx`` and ``-dy`` track.
    * 45° + (shallow) -- mostly sideways, ``-dy`` every two steps.
    """
    step = len(root.segments) + 1
    direction = 1 if root.angle_deg > 0 else -1
    deviation = 180.0 - abs(root.angle_deg)  # 0 = straight down
    if deviation < 25.0:
        dx = direction * ((step + 1) // 2)
        dy = -step
    elif deviation < 45.0:
        dx = direction * step
        dy = -step
    else:
        dx = direction * step
        dy = -((step + 1) // 2)
    attach_x, attach_y = root.attach_point
    return Segment(
        x=attach_x + dx,
        y=attach_y + dy - 1,  # offset below the trunk base
        glyph=root_glyph_for_angle(root.angle_deg),
        birth_event_idx=event_idx,
    )


# ---------------------------------------------------------------------------
# Flowers
# ---------------------------------------------------------------------------


def _add_flower(
    state: TreeState,
    intent: AttachmentIntent,
    rng: random.Random,
    event_idx: int,
) -> TreeState:
    """Add a flower somewhere in the canopy.

    Position is sampled around the highest current canopy point so
    flowers feel like bloomings, not floating decorations.
    """
    canopy_top = _canopy_top(state)
    x = rng.randint(-3, 3)
    y = canopy_top + rng.randint(1, 2)
    glyph = "❀" if rng.random() < 0.7 else "*"
    flower = Flower(
        x=x,
        y=y,
        glyph=glyph,
        host_or_query=intent.host_or_query or "",
    )
    return replace(state, flowers=[*state.flowers, flower])


def _canopy_top(state: TreeState) -> int:
    top = 0
    for seg in state.trunk:
        top = max(top, seg.y)
    for branch in state.branches:
        for seg in branch.segments:
            top = max(top, seg.y)
        for leaf in branch.leaves:
            top = max(top, leaf.y)
    return top


# ---------------------------------------------------------------------------
# Offshoots
# ---------------------------------------------------------------------------


def _spawn_offshoot(
    state: TreeState,
    intent: AttachmentIntent,
    event_idx: int,
    *,
    capped: bool = False,
) -> TreeState:
    """Add a sub-agent offshoot stalk from the trunk.

    When ``capped=True`` the tip glyph is the ``•`` berry rather
    than the running ``.``, so a single ``SubagentStop`` (no
    matching ``SubagentStart``) still produces the same visual as
    a start+stop pair would.
    """
    state = _ensure_trunk(state, event_idx)
    attach_y = max(1, len(state.trunk) // 2)
    sign = -1 if (event_idx % 2 == 0) else 1
    tip_glyph = "•" if capped else "."
    segments = [
        Segment(
            x=sign * 1, y=attach_y, glyph=_OFFSHOOT_STALK,
            birth_event_idx=event_idx,
        ),
        Segment(
            x=sign * 2, y=attach_y, glyph=tip_glyph,
            birth_event_idx=event_idx,
        ),
    ]
    offshoot = Offshoot(
        agent_id=intent.agent_id or "",
        agent_type=intent.agent_type or "",
        attach_point=(0, attach_y),
        segments=segments,
    )
    return replace(state, offshoots=[*state.offshoots, offshoot])


def _cap_offshoot(
    state: TreeState, intent: AttachmentIntent, event_idx: int = 0
) -> TreeState:
    """Spawn-or-cap: mark the subagent's offshoot complete.

    Originally we only capped an existing offshoot (assuming a
    ``SubagentStart`` had already created the geometry). In
    practice -- confirmed by a May-2026 live Windows session -- real
    Claude Code may emit ``SubagentStop`` events without any
    matching ``SubagentStart``. To recover that growth, we now:

    1. Look up by ``agent_id``: if the offshoot exists, cap its
       tip with ``•`` (the original behaviour).
    2. Otherwise spawn a fresh offshoot fully-formed *and* capped
       in one step. The visual is identical to "spawn then cap";
       only the bookkeeping differs.

    See DESIGN.md §3 for the updated event mapping.
    """
    if not intent.agent_id:
        return state
    for i, off in enumerate(state.offshoots):
        if off.agent_id == intent.agent_id:
            if off.segments and off.segments[-1].glyph != "•":
                last = off.segments[-1]
                new_last = replace(last, glyph="•")
                new_off = replace(off, segments=[*off.segments[:-1], new_last])
                return _replace_offshoot(state, i, new_off)
            return state
    # No existing offshoot -- spawn one already capped.
    return _spawn_offshoot(state, intent, event_idx, capped=True)


# ---------------------------------------------------------------------------
# Wither
# ---------------------------------------------------------------------------


def _wither(
    state: TreeState, intent: AttachmentIntent, event_idx: int
) -> TreeState:
    """Mark a leaf wilted on the relevant branch; repeat → falls off.

    Originally the wilted glyph was ``"*"`` -- which is also one of
    the regular leaf glyphs, so a withered leaf was visually
    indistinguishable from a normal one. We now use a dedicated
    glyph (:data:`_WILTED_LEAF_GLYPH`) plus the palette's
    ``leaf_dim`` colour so the wither is obvious on both colour and
    monochrome terminals. The renderer reads ``leaf.color`` and
    applies it directly when set.

    Repeated failure on the same branch removes the wilted leaf
    entirely (the "leaf falls" behaviour from DESIGN.md §3).
    """
    if not state.branches:
        # No branches yet -- count the error without geometry change.
        return replace(state, error_count=state.error_count + 1)

    idx: int | None = None
    if intent.file_path_key:
        idx = _find_branch_by_key(state, intent.file_path_key)
    if idx is None:
        idx = len(state.branches) - 1
    branch = state.branches[idx]
    if not branch.leaves:
        return replace(state, error_count=state.error_count + 1)
    last = branch.leaves[-1]
    if last.glyph != _WILTED_LEAF_GLYPH:
        # First failure → yellow + change to the wilted glyph.
        # ``color`` carries an explicit hex marker the renderer
        # honours over the palette default (autumn-tone yellowing).
        new_last = replace(
            last,
            glyph=_WILTED_LEAF_GLYPH,
            color="#DAA520",  # goldenrod -- palette-independent wilt tint
            birth_event_idx=event_idx,
        )
        new_branch = replace(branch, leaves=[*branch.leaves[:-1], new_last])
    else:
        # Already wilted → fall (remove geometry).
        new_branch = replace(
            branch,
            leaves=branch.leaves[:-1],
            leaf_geometry_count=max(0, branch.leaf_geometry_count - 1),
        )
    return replace(
        _replace_branch(state, idx, new_branch),
        error_count=state.error_count + 1,
    )


# ---------------------------------------------------------------------------
# Replacement helpers -- keep apply_event's contract: never mutate state.
# ---------------------------------------------------------------------------


def _replace_branch(state: TreeState, idx: int, branch: Branch) -> TreeState:
    new_list = list(state.branches)
    new_list[idx] = branch
    return replace(state, branches=new_list)


def _replace_root(state: TreeState, idx: int, root: Root) -> TreeState:
    new_list = list(state.roots)
    new_list[idx] = root
    return replace(state, roots=new_list)


def _replace_offshoot(state: TreeState, idx: int, off: Offshoot) -> TreeState:
    new_list = list(state.offshoots)
    new_list[idx] = off
    return replace(state, offshoots=new_list)


def _ensure_trunk(state: TreeState, event_idx: int) -> TreeState:
    """Ensure the trunk has at least one segment.

    Some sessions race a PostToolUse before our SessionStart hook
    fires (the Claude Code event ordering is not contractual). Plant
    a sprout so subsequent branch creation has somewhere to attach.
    """
    if state.trunk:
        return state
    return _plant_seed(state, _DummyEvent(), event_idx)

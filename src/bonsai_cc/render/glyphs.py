"""Glyph tables -- the visual vocabulary of the bonsai.

These are *just data*. The mapping from event semantics to glyph
happens in :mod:`bonsai_cc.growth`; the renderer only draws what
the state tells it to draw.

The age-thickness ladder is exposed here so the growth layer can
quantize ``birth_event_idx`` deltas into a glyph index without
re-deriving the ramp. Older segments get thicker glyphs.
"""

from __future__ import annotations

# Trunk thickness ramp by age. Index 0 = freshly-grown sprout;
# higher indices = older, thicker bark. Picked for visual continuity:
# each step adds visible weight without breaking the silhouette.
TRUNK_THICKNESS: tuple[str, ...] = (".", ":", "|", "│", "║", "┃")

# Branch glyphs by direction. Anchored to vertical (0°). The growth
# engine picks left vs right based on the branch's signed angle.
BRANCH_LEFT: str = "/"
BRANCH_RIGHT: str = "\\"
BRANCH_HORIZONTAL: str = "─"

# Leaf glyphs. Narrow vs broad covers the language-themed variants
#: bamboo / pine use ``|``; oak / willow use ``&``.
LEAF_NARROW: str = "|"
LEAF_BROAD: str = "&"
LEAF_SMALL: str = "*"

# Root glyphs use slashes mirrored about the base.
ROOT_LEFT: str = "/"
ROOT_RIGHT: str = "\\"
ROOT_VERTICAL: str = "|"

# Flowers and decorations.
FLOWER_BLOOM: str = "❀"
FLOWER_SMALL: str = "*"
FRUIT: str = "•"
WITHERED_BUD: str = "˚"  # PermissionDenied
QUESTION_BUD: str = "?"  # AskUserQuestion (transient)

# Ground / sky decorations rendered around the tree.
GROUND_LINE: str = "~"
SKY_EMPTY: str = " "
MOON: str = "☾"
FIREFLY: str = "·"
DEW: str = "'"

# Seasonal overlays.
SNOWFLAKE: str = "❄"
BUD: str = "•"

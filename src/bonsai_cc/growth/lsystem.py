"""Deterministic stochastic primitives for the growth engine.

Same ``(session_seed, event_idx)`` -> same outputs, on every Python
version. We hash with ``blake2b`` and feed the int to
``random.Random`` -- the Mersenne Twister output from a fixed int
seed has been stable since Python 3.2.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from typing import TypeVar

# Inline the few glyph chars we need. Duplicating these one-character
# strings (vs importing from ``bonsai_cc.render.glyphs``) keeps growth
# free of any render dependency -- growth produces state, render reads
# it. Should the canonical glyph table ever move, both copies update
# together; the test corpus would catch any drift.
_BRANCH_LEFT = "/"
_BRANCH_RIGHT = "\\"
_BRANCH_HORIZONTAL = "─"  # box-drawings ─
_ROOT_LEFT = "/"
_ROOT_RIGHT = "\\"
_ROOT_VERTICAL = "|"


__all__ = [
    "angle_for",
    "branch_glyph_for_angle",
    "event_rng",
    "pick_one",
    "root_glyph_for_angle",
    "seed_from_session_id",
    "weighted_choice",
]


T = TypeVar("T")

# Quantization step (degrees) for any persisted angle. the design contract
# calls for 0.01 rad ≈ 0.57°; we quantize to a finer 0.01° so two
# slightly-different float paths to the same logical angle always
# round to identical bits and the determinism contract holds across
# CPython versions.
_ANGLE_QUANT_DEG = 0.01

# Stable digest size (8 bytes = 64 bits) -- plenty of entropy for
# Mersenne Twister seeds while staying inside Python's native int
# range for fast arithmetic.
_DIGEST_BYTES = 8


def seed_from_session_id(session_id: str) -> int:
    """Map a session id to the 64-bit integer seed used everywhere.

    Stable across Python versions because ``blake2b`` is itself
    bit-stable. Matches the ``seed_hex`` stored in ``TreeState`` --
    that's just the same bytes as hex.

    Example:
        >>> seed_from_session_id("demo") == seed_from_session_id("demo")
        True
        >>> isinstance(seed_from_session_id("demo"), int)
        True
    """
    digest = hashlib.blake2b(
        session_id.encode("utf-8"), digest_size=_DIGEST_BYTES
    ).digest()
    return int.from_bytes(digest, "big")


def event_rng(session_seed: int, event_idx: int) -> random.Random:
    """Return a ``random.Random`` deterministically keyed by event.

    Each event in a session gets its own independent stream so that
    inserting / re-ordering events in development does not perturb
    the choices made for unrelated events. The contract is::

        event_rng(seed, idx) is independent of event_rng(seed, idx ± k)

    in the sense that no useful correlation exists across event
    indices for the same session seed.
    """
    payload = f"{session_seed}:{event_idx}".encode()
    digest = hashlib.blake2b(payload, digest_size=_DIGEST_BYTES).digest()
    return random.Random(int.from_bytes(digest, "big"))


def pick_one(rng: random.Random, options: Sequence[T]) -> T:
    """Choose one option uniformly. Equivalent to ``rng.choice`` but
    types better and rejects empty sequences early."""
    if not options:
        msg = "pick_one called with no options"
        raise ValueError(msg)
    return options[rng.randrange(len(options))]


def weighted_choice(
    rng: random.Random, options: Sequence[tuple[T, float]]
) -> T:
    """Choose one item by weight. Weights need not sum to 1.

    Inputs are pairs of ``(item, weight)``. Weight ordering matters
    for determinism: pass the same sequence in the same order on
    every call. Negative weights are rejected.
    """
    if not options:
        msg = "weighted_choice called with no options"
        raise ValueError(msg)
    total = 0.0
    for _, w in options:
        if w < 0:
            msg = "weighted_choice weights must be non-negative"
            raise ValueError(msg)
        total += w
    if total <= 0:
        # Degenerate: fall back to uniform pick so we never divide by
        # zero or hand back an arbitrary first element.
        return pick_one(rng, [item for item, _ in options])
    target = rng.random() * total
    acc = 0.0
    for item, w in options:
        acc += w
        if target <= acc:
            return item
    return options[-1][0]


def angle_for(key: str, *, min_deg: float = 20.0, max_deg: float = 60.0) -> float:
    """Hash a string into a stable angle in degrees within ``[min, max]``.

    Used to make a file's branch lean in the same direction across
    re-renders. Sign is decided by the second-byte parity so files
    spread roughly evenly between left and right.

    Example:
        >>> a = angle_for("src/auth.py")
        >>> b = angle_for("src/auth.py")
        >>> a == b
        True
        >>> abs(a) >= 20.0 and abs(a) <= 60.0
        True
    """
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=4).digest()
    magnitude_bits = int.from_bytes(digest[:2], "big")
    sign_bit = digest[2] & 1
    span = max_deg - min_deg
    magnitude = min_deg + (magnitude_bits / 0xFFFF) * span
    angle = magnitude if sign_bit else -magnitude
    return round(angle / _ANGLE_QUANT_DEG) * _ANGLE_QUANT_DEG


def branch_glyph_for_angle(angle_deg: float) -> str:
    """Pick the segment glyph for a branch leaning at ``angle_deg``.

    Positive angles lean right (``\\``), negative lean left (``/``).
    Near-horizontal angles use ``─``.
    """
    if abs(angle_deg) < 12.0:
        return _BRANCH_HORIZONTAL
    return _BRANCH_RIGHT if angle_deg > 0 else _BRANCH_LEFT


def root_glyph_for_angle(angle_deg: float) -> str:
    """Pick the glyph for a root growing at ``angle_deg`` from vertical.

    Roots use the same direction glyphs as branches but mirrored:
    positive (right-leaning) → ``\\``, negative → ``/``, near-
    vertical → ``|``.
    """
    if abs(angle_deg) < 12.0:
        return _ROOT_VERTICAL
    return _ROOT_RIGHT if angle_deg > 0 else _ROOT_LEFT

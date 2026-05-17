"""Anthropic-inspired color tokens for the web renderer.

Restricted palette: warm cream backgrounds, Crail accent, muted
moss for foliage. Renderers consume these names directly and never
use raw hex literals. If a new tone is needed, add it here first
with a clear semantic name.

Token names follow the CSS custom-property convention used in
:root in static/index.html, with the ``--bcc-`` prefix dropped.

Constraints (enforced by ``tests/test_render_tokens.py``):

* Every renderer output is scanned for ``#[0-9A-F]{6}`` literals;
  every hit must be present in this token registry.
* No hex appears twice under different names -- semantic naming
  matters so renderers reach for the right token.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Backgrounds: sky bands, ground, surfaces.
# ---------------------------------------------------------------------------

PAMPAS: Final[str] = "#F4F3EE"          # warm cream -- day sky base
PAMPAS_DEEP: Final[str] = "#E8E5DC"     # deeper cream -- ground / pot inner
DUSK: Final[str] = "#D4A27F"            # warm sand -- dusk gradient stop
DAWN: Final[str] = "#E8C8A8"            # peach cream -- dawn gradient stop
NIGHT: Final[str] = "#2A2826"           # warm near-black -- night sky
NIGHT_GLOW: Final[str] = "#4A4540"      # warm dark -- night sky midtone
NIGHT_DEEP: Final[str] = "#1A1816"      # darkest warm -- dark-mode sky top

# ---------------------------------------------------------------------------
# Foreground accents (Anthropic Crail + supporting neutrals).
# ---------------------------------------------------------------------------

CRAIL: Final[str] = "#C15F3C"           # primary accent -- sun, ripe fruit
CRAIL_DEEP: Final[str] = "#8E3D24"      # deep terracotta -- pot, dark fruit
CLOUDY: Final[str] = "#B1ADA1"          # warm gray -- clouds, deadwood
INK: Final[str] = "#191919"             # near-black -- text, deep shadow
INK_SOFT: Final[str] = "#4A4540"        # warm dark gray -- secondary text

# ---------------------------------------------------------------------------
# Foliage / bark base.
# ---------------------------------------------------------------------------

MOSS: Final[str] = "#6B7A4A"            # muted olive -- default leaf
LEAF_HIGHLIGHT: Final[str] = "#7A8C5C"  # slightly yellower olive -- two-tone edges
BARK: Final[str] = "#6B5A48"            # warm wood brown -- default trunk
BARK_DEEP: Final[str] = "#3D3328"       # dark bark crevices / inner shadow

# ---------------------------------------------------------------------------
# Theme-specific accents.
# ---------------------------------------------------------------------------

SAKURA_PALE: Final[str] = "#E8B8C8"     # pale cherry blossom
SAKURA_DEEP: Final[str] = "#D89AB0"     # deep cherry blossom

BAMBOO_YOUNG: Final[str] = "#B8C28F"    # younger / higher bamboo highlight

# Willow + TS-blue fruits.
WILLOW_LEAF: Final[str] = "#A8C28A"     # soft drooping willow green
TS_FRUIT: Final[str] = "#3D7AB8"        # typescript blue accent fruit

# Maple autumn.
MAPLE_RED: Final[str] = "#A33D24"       # deep autumn red
MAPLE_GOLD: Final[str] = "#D89A4A"      # warm autumn gold

# Ginkgo gold.
GINKGO_GOLD: Final[str] = "#D4A027"     # ginkgo signature gold
GINKGO_GOLD_LIGHT: Final[str] = "#E8C147"  # lighter ginkgo highlight

# Birch.
BIRCH_BARK: Final[str] = "#E8E0D4"      # warm off-white birch bark
BIRCH_LEAF: Final[str] = "#A8C8A0"      # fresh light green

# ---------------------------------------------------------------------------
# Registry -- every named token shows up here so the lint test can
# enforce "no raw hex outside the registry."
# ---------------------------------------------------------------------------

REGISTRY: Final[dict[str, str]] = {
    "PAMPAS": PAMPAS,
    "PAMPAS_DEEP": PAMPAS_DEEP,
    "DUSK": DUSK,
    "DAWN": DAWN,
    "NIGHT": NIGHT,
    "NIGHT_GLOW": NIGHT_GLOW,
    "NIGHT_DEEP": NIGHT_DEEP,
    "CRAIL": CRAIL,
    "CRAIL_DEEP": CRAIL_DEEP,
    "CLOUDY": CLOUDY,
    "INK": INK,
    "INK_SOFT": INK_SOFT,
    "MOSS": MOSS,
    "LEAF_HIGHLIGHT": LEAF_HIGHLIGHT,
    "BARK": BARK,
    "BARK_DEEP": BARK_DEEP,
    "SAKURA_PALE": SAKURA_PALE,
    "SAKURA_DEEP": SAKURA_DEEP,
    "BAMBOO_YOUNG": BAMBOO_YOUNG,
    "WILLOW_LEAF": WILLOW_LEAF,
    "TS_FRUIT": TS_FRUIT,
    "MAPLE_RED": MAPLE_RED,
    "MAPLE_GOLD": MAPLE_GOLD,
    "GINKGO_GOLD": GINKGO_GOLD,
    "GINKGO_GOLD_LIGHT": GINKGO_GOLD_LIGHT,
    "BIRCH_BARK": BIRCH_BARK,
    "BIRCH_LEAF": BIRCH_LEAF,
}


def sky_stops(hour: int) -> tuple[str, str]:
    """Top, bottom colors for the sky gradient at ``hour``.

    Four bands -- dawn, day, dusk, night. Day is intentionally a
    near-flat warm cream so the tree is the subject; only dawn and
    dusk get warm color shifts; night goes near-black.
    """
    if 5 <= hour < 8:
        return DAWN, PAMPAS
    if 8 <= hour < 18:
        return PAMPAS, PAMPAS_DEEP
    if 18 <= hour < 22:
        return DUSK, CRAIL
    return NIGHT, NIGHT_GLOW


__all__ = [
    "BAMBOO_YOUNG",
    "BARK",
    "BARK_DEEP",
    "BIRCH_BARK",
    "BIRCH_LEAF",
    "CLOUDY",
    "CRAIL",
    "CRAIL_DEEP",
    "DAWN",
    "DUSK",
    "GINKGO_GOLD",
    "GINKGO_GOLD_LIGHT",
    "INK",
    "INK_SOFT",
    "LEAF_HIGHLIGHT",
    "MAPLE_GOLD",
    "MAPLE_RED",
    "MOSS",
    "NIGHT",
    "NIGHT_GLOW",
    "PAMPAS",
    "PAMPAS_DEEP",
    "REGISTRY",
    "SAKURA_DEEP",
    "SAKURA_PALE",
    "TS_FRUIT",
    "WILLOW_LEAF",
    "sky_stops",
]

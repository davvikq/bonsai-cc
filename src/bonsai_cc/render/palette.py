"""Color palettes -- one per language theme (DESIGN.md §2.6).

Each palette defines the color for every semantic role; the
renderer reads them when it converts the projected grid into styled
text. Hex colours are picked so the nearest 16-color neighbour is
also acceptable, since terminal downgrades happen on
non-truecolor consoles.

Adding a new theme is one entry. The growth engine never imports
this module, so a new palette can't accidentally affect
determinism (DESIGN.md §1.1 seam).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PALETTES", "Palette", "palette_for"]


@dataclass(frozen=True, slots=True)
class Palette:
    """Hex colors for every role in the tree, one theme at a time."""

    name: str
    trunk: str
    branch: str
    leaf: str
    leaf_dim: str  # autumn / yellowing
    root: str
    flower: str
    ground: str
    sky: str
    accent: str  # language-specific touch (TS-blue, sakura-pink, etc.)


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------


_DEFAULT = Palette(
    name="default",
    trunk="#8B5A2B",
    branch="#A0522D",
    leaf="#3CB371",
    leaf_dim="#DAA520",
    root="#5C3317",
    flower="#FF69B4",
    ground="#7C6E59",
    sky="#2F4F4F",
    accent="#3CB371",
)

# Python -- bamboo: yellow-green tones, narrow leaves.
_PYTHON = Palette(
    name="python",
    trunk="#6B8E23",  # olive drab
    branch="#9ACD32",
    leaf="#ADFF2F",
    leaf_dim="#BDB76B",
    root="#556B2F",
    flower="#FFD700",
    ground="#7C6E59",
    sky="#2F4F4F",
    accent="#FFD700",  # python yellow
)

# Rust -- pine: brown trunk, needle leaves.
_RUST = Palette(
    name="rust",
    trunk="#5D2A0A",
    branch="#8B4513",
    leaf="#2E8B57",
    leaf_dim="#B8860B",
    root="#3E1F08",
    flower="#CD5C5C",
    ground="#6B4423",
    sky="#2F4F4F",
    accent="#CE422B",  # rust orange
)

# Go -- oak: thick trunk, wide canopy.
_GO = Palette(
    name="go",
    trunk="#6B4423",
    branch="#8B4513",
    leaf="#4682B4",
    leaf_dim="#708090",
    root="#3E1F08",
    flower="#00ACD7",  # go cyan
    ground="#7C6E59",
    sky="#2F4F4F",
    accent="#00ACD7",
)

# TypeScript -- willow with TS-blue accent.
_TYPESCRIPT = Palette(
    name="typescript",
    trunk="#5C5470",
    branch="#7A6E91",
    leaf="#9CA8B5",
    leaf_dim="#6E7A85",
    root="#3F3850",
    flower="#3178C6",  # TS brand blue
    ground="#5A5F66",
    sky="#1B2330",
    accent="#3178C6",
)

# JavaScript -- willow.
_JAVASCRIPT = Palette(
    name="javascript",
    trunk="#7A5C3A",
    branch="#A0825F",
    leaf="#9CAF88",
    leaf_dim="#C6B576",
    root="#4F3722",
    flower="#F0DB4F",  # js yellow
    ground="#7C6E59",
    sky="#2F4F4F",
    accent="#F0DB4F",
)

# Swift -- sakura: pink blossoms.
_SWIFT = Palette(
    name="swift",
    trunk="#8B5C5C",
    branch="#B07C7C",
    leaf="#FAD0C9",
    leaf_dim="#E6A6A1",
    root="#5C3D3D",
    flower="#FF8FAB",  # cherry blossom
    ground="#7C6E59",
    sky="#FFE4E1",
    accent="#FF8FAB",
)

# Ruby -- maple: red autumn palette.
_RUBY = Palette(
    name="ruby",
    trunk="#722F37",
    branch="#A0524D",
    leaf="#C0392B",
    leaf_dim="#D88060",
    root="#4F1F26",
    flower="#E0115F",
    ground="#7C6E59",
    sky="#2F4F4F",
    accent="#E0115F",
)

# C/C++ -- old-growth oak, dark bark.
_CPP = Palette(
    name="cpp",
    trunk="#3D2B1F",
    branch="#5C4033",
    leaf="#4A6B4A",
    leaf_dim="#7C7755",
    root="#2A1E0E",
    flower="#00599C",  # C++ blue
    ground="#5A5036",
    sky="#22293A",
    accent="#00599C",
)

# Java / Kotlin -- banyan: massive, aerial roots.
_JAVA = Palette(
    name="java",
    trunk="#5A4634",
    branch="#7C6754",
    leaf="#6B8A7A",
    leaf_dim="#9B9670",
    root="#3A2E22",
    flower="#F89820",  # java orange
    ground="#7C6E59",
    sky="#2F4F4F",
    accent="#F89820",
)

# Haskell / OCaml / Elm -- ginkgo: golden fan leaves.
_HASKELL = Palette(
    name="haskell",
    trunk="#6B5C3E",
    branch="#8B7E5C",
    leaf="#E8C547",  # ginkgo gold
    leaf_dim="#C6A85E",
    root="#4D4127",
    flower="#5E5086",  # haskell purple
    ground="#7C6E59",
    sky="#2F4F4F",
    accent="#5E5086",
)

# Zig -- birch: pale, slender.
_ZIG = Palette(
    name="zig",
    trunk="#D4C8B0",
    branch="#A89B82",
    leaf="#7BAE7F",
    leaf_dim="#C0B47A",
    root="#7C7160",
    flower="#F7A41D",  # zig orange
    ground="#7C6E59",
    sky="#2F4F4F",
    accent="#F7A41D",
)


PALETTES: dict[str, Palette] = {
    "default": _DEFAULT,
    "python": _PYTHON,
    "rust": _RUST,
    "go": _GO,
    "typescript": _TYPESCRIPT,
    "javascript": _JAVASCRIPT,
    "swift": _SWIFT,
    "ruby": _RUBY,
    "cpp": _CPP,
    "java": _JAVA,
    "haskell": _HASKELL,
    "zig": _ZIG,
}


def palette_for(theme: str) -> Palette:
    """Return the palette for ``theme``, falling back to ``default``.

    Unknown themes return the default palette so the renderer never
    crashes on a freshly-named theme (e.g. a future Zig palette
    addition arriving before the user has updated their installed
    bonsai-cc).

    Example:
        >>> palette_for("python").name
        'python'
        >>> palette_for("not-a-theme").name
        'default'
    """
    return PALETTES.get(theme, _DEFAULT)

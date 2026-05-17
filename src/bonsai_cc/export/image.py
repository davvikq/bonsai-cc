"""Image exports (PNG today, SVG / GIF stubbed).

PNG renders the final ASCII through Pillow's default font onto a
clean background. Output is a tight crop around the tree. The
default bitmap font may eventually be swapped for a packaged TTF so
glyphs land cleanly on every platform; for v1 the default is good
enough for screenshots.

GIF would need a frame-per-event animation (replaying the event log
through the projection function and concatenating frames via
Pillow); SVG would need a hand-rolled vector pass. Both deferred.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bonsai_cc.export import ExportError
from bonsai_cc.garden.store import SessionRow

__all__ = ["export_gif_stub", "export_png", "export_svg_stub"]


# Tuned for legibility at 1x: 12px monospace cells, 1.6x line spacing.
_CELL_W = 9
_CELL_H = 16
_BG = (24, 24, 28)  # dark slate
_FG = (220, 220, 200)
_PADDING = 24


def export_png(row: SessionRow, out_path: Path) -> Path:
    """Render ``row.final_ascii`` as a PNG.

    The output uses a fixed-cell monospace layout -- every cell is
    ``_CELL_W x _CELL_H`` pixels, so the proportions of the tree are
    preserved exactly. No anti-aliasing dance with proportional
    fonts; the renderer guarantees a clean grid.
    """
    if not row.final_ascii:
        msg = (
            f"session {row.id} has no cached ASCII snapshot; "
            "regenerate with a replay first."
        )
        raise ExportError(msg)

    lines = row.final_ascii.splitlines() or [""]
    cols = max(len(line) for line in lines)
    rows = len(lines)
    width_px = cols * _CELL_W + 2 * _PADDING
    height_px = rows * _CELL_H + 2 * _PADDING

    img = Image.new("RGB", (width_px, height_px), _BG)
    draw = ImageDraw.Draw(img)
    font = _default_monospace_font()
    for r, line in enumerate(lines):
        for c, ch in enumerate(line):
            if ch == " ":
                continue
            x = _PADDING + c * _CELL_W
            y = _PADDING + r * _CELL_H
            draw.text((x, y), ch, fill=_FG, font=font)
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    return out_path


def export_svg_stub(_: SessionRow, __: Path) -> Path:  # pragma: no cover - stub
    msg = "SVG export is not implemented (planned: cairosvg-based pipeline)."
    raise ExportError(msg)


def export_gif_stub(_: SessionRow, __: Path) -> Path:  # pragma: no cover - stub
    msg = (
        "GIF export is not implemented (planned: frame-per-event replay "
        "through the projection function, concatenated via Pillow)."
    )
    raise ExportError(msg)


def _default_monospace_font() -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Return a usable monospace font.

    Pillow's default bitmap font is universally available but very
    small. We try a handful of system TTFs first and fall back to
    the bitmap default when nothing's found.
    """
    candidates = [
        "DejaVuSansMono.ttf",
        "consola.ttf",
        "Menlo.ttc",
        "Cousine-Regular.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, 13)
        except OSError:
            continue
    return ImageFont.load_default()

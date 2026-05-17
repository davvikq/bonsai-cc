"""Export persisted sessions to various formats.

Shipped: **txt** (plain ASCII) and **png** (Pillow rendering of the
snapshot). SVG and GIF are stubs that raise ``ExportError`` rather
than pretending to work.

All exports take a :class:`bonsai_cc.garden.SessionRow` because that
already carries everything the renderer needs (the final ASCII text,
the JSON state, the on-disk event log path for replay-based exports).
"""


class ExportError(RuntimeError):
    """Raised when an export can't be produced (missing data, etc.)."""


# ``ExportError`` is defined above so the submodules can import it
# without a circular reference.
from bonsai_cc.export.image import export_png  # noqa: E402
from bonsai_cc.export.text import export_text  # noqa: E402

__all__ = ["ExportError", "export_png", "export_text"]

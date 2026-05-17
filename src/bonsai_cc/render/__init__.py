"""Static text-projection helpers used by the export path.

The live renderer is the web SVG renderer in :mod:`bonsai_cc.web`.
What remains here is what the static / export commands still need:

* ``bonsai-cc show`` / ``bonsai-cc export --format txt`` -- render the
  saved TreeState to a fixed-size ASCII string via
  :func:`bonsai_cc.render.projection.project`.
* The SVG renderer's palette tokens -- see
  :mod:`bonsai_cc.render.palette` (used by per-theme SVG renderers).

The architectural seam test forbids this package from importing
:mod:`bonsai_cc.events.watcher` or :mod:`bonsai_cc.events.journal`.
"""

from bonsai_cc.render.projection import project

__all__ = ["project"]

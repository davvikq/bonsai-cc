"""Persistent garden: SQLite-backed history of every saved tree.

The garden is a single file at ``<home>/garden.db``. One row per
session, plus a small schema-metadata table for migrations.

This package owns:

* :class:`GardenStore` -- connection + CRUD. SQLite is stdlib; no ORM.
* :class:`SessionRow` -- what the store hands back from queries.
* :func:`render_final_ascii` -- projects a ``TreeState`` to a fixed
  80x24 grid for ``bonsai-cc show`` and ``export --format txt``.
  The live garden lives in the web view (``bonsai_cc.web``).
"""

from bonsai_cc.garden.store import (
    GardenStore,
    SessionFilter,
    SessionRow,
    render_final_ascii,
)

__all__ = [
    "GardenStore",
    "SessionFilter",
    "SessionRow",
    "render_final_ascii",
]

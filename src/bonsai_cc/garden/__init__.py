"""Persistent garden: SQLite-backed history of every saved tree.

The garden is a single file at ``<home>/garden.db``. One row per
session, plus a small schema-metadata table for migrations.

This package owns:

* :class:`GardenStore` -- connection + CRUD. SQLite is stdlib; no ORM.
* :class:`SessionRow` -- what the store hands back from queries.
* :func:`render_final_ascii` -- projects a ``TreeState`` to a fixed
  80x24 grid and returns the multi-line string the store persists as
  ``final_ascii``.

The browser TUI lives in :mod:`bonsai_cc.garden.browser`.
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

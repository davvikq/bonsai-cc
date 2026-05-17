"""SQLite-backed session storage. No ORM.

The schema is tiny (one table) so an ORM would be overkill. We use
stdlib ``sqlite3`` directly with a thin wrapper that handles:

* PRAGMA-set connection pragmas (WAL, busy_timeout, foreign keys);
* schema bootstrap from ``schema.sql``;
* future migration runs based on ``schema_meta.version``;
* parameterised CRUD with typed row dataclasses.

Concurrency note: SQLite's WAL mode lets the daemon write while a
``bonsai-cc list`` in another terminal reads concurrently. For the
single-process case the busy_timeout = 5000 covers the rare lock
contention between the runner's save loop and a manual CLI read.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from bonsai_cc.config import Config, get_config
from bonsai_cc.growth.state import TreeState, state_from_dict, state_to_dict
from bonsai_cc.log import get_logger
from bonsai_cc.render.projection import project

__all__ = [
    "DEFAULT_ASCII_HEIGHT",
    "DEFAULT_ASCII_WIDTH",
    "GardenStore",
    "SessionFilter",
    "SessionRow",
    "SessionStatus",
    "load_state_from_row",
    "render_final_ascii",
]


class SessionStatus:
    """How a row landed in the garden. See schema.sql for the full
    set of values; using the constants here makes them grep-able."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    RECOVERED = "recovered"


_log = get_logger("bonsai_cc.garden.store")


DEFAULT_ASCII_WIDTH = 80
DEFAULT_ASCII_HEIGHT = 24


# ---------------------------------------------------------------------------
# Row + filter dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionRow:
    """One row from the ``sessions`` table."""

    id: str
    seed_hex: str
    started_at: int
    ended_at: int | None
    project_path: str
    detected_language: str | None
    theme: str
    tool_call_count: int
    error_count: int
    file_branch_count: int
    final_ascii: str | None
    final_state_json: str | None
    event_log_path: str
    tags: str | None
    status: str = SessionStatus.COMPLETE

    @property
    def duration_ms(self) -> int | None:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    def state_dict(self) -> dict[str, Any] | None:
        if self.final_state_json is None:
            return None
        data = json.loads(self.final_state_json)
        return data if isinstance(data, dict) else None


@dataclass(frozen=True, slots=True)
class SessionFilter:
    """Query filter for :meth:`GardenStore.list_sessions`."""

    project_path: str | None = None
    detected_language: str | None = None
    started_after: int | None = None
    started_before: int | None = None
    tag_contains: str | None = None
    limit: int = 100


# ---------------------------------------------------------------------------
# ASCII rendering helper (used both at save time and on demand)
# ---------------------------------------------------------------------------


def render_final_ascii(
    state: TreeState,
    *,
    width: int = DEFAULT_ASCII_WIDTH,
    height: int = DEFAULT_ASCII_HEIGHT,
) -> str:
    """Project ``state`` to a fixed-size ASCII string.

    This is what gets stored in ``sessions.final_ascii`` and what
    ``bonsai-cc show`` / ``bonsai-cc export --format txt`` print. A
    fixed size means snapshots compare cleanly in CI -- the live
    surface is the SVG web view; this path is the static-snapshot
    side door for piping into chat or commit messages.
    """
    grid = project(state, width, height)
    lines = ["".join(cell.char for cell in row).rstrip() for row in grid]
    # Trim trailing blank lines so the snapshot is compact, but keep
    # the in-tree blank lines (between canopy and trunk) intact.
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def load_state_from_row(row: SessionRow) -> TreeState | None:
    """Reconstruct a ``TreeState`` from the row's JSON payload.

    Returns ``None`` if no payload was stored (older row predating
    serialisation, or a corrupt save). Replay from
    ``event_log_path`` is always an alternative.
    """
    d = row.state_dict()
    if d is None:
        return None
    return state_from_dict(d)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


_SCHEMA_RESOURCE = "schema.sql"


class GardenStore:
    """A garden DB connection. Caller is responsible for ``close()``.

    The store is **not** an async object -- SQLite calls are
    synchronous and fast for our row sizes. Callers that need to
    avoid blocking the event loop can wrap calls in
    ``asyncio.to_thread``; the saver task in the runner does so.
    """

    def __init__(self, path: Path | None = None, *, config: Config | None = None) -> None:
        cfg = config or get_config()
        cfg.ensure_dirs()
        self._path = path or cfg.garden_db
        self._conn = sqlite3.connect(
            str(self._path),
            isolation_level=None,  # autocommit; we control transactions explicitly
            check_same_thread=False,  # we may be called from asyncio.to_thread
        )
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> GardenStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _apply_schema(self) -> None:
        sql = resources.files(__package__).joinpath(_SCHEMA_RESOURCE).read_text(
            encoding="utf-8"
        )
        with closing(self._conn.cursor()) as cur:
            cur.executescript(sql)
            self._migrate(cur)

    def _migrate(self, cur: sqlite3.Cursor) -> None:
        """Apply incremental schema migrations.

        Strategy: ``schema.sql`` reflects the *latest* schema. A
        fresh DB picks it up directly. Older DBs (e.g. a v1 garden
        sitting on a user's disk before v2 shipped) need targeted
        ALTERs to add the new columns. We dispatch on
        ``schema_meta.version``.
        """
        cur.execute("SELECT value FROM schema_meta WHERE key='version'")
        row = cur.fetchone()
        current = int(row[0]) if row else 1

        if current < 2:
            # v1 → v2: add the ``status`` column. ``ALTER TABLE
            # ADD COLUMN ... NOT NULL DEFAULT`` is supported by
            # SQLite >= 3.35 (every Python 3.11+ install ships
            # a newer SQLite than that); we additionally
            # backfill in case the default trigger is missed
            # on very old engines.
            existing = {r[1] for r in cur.execute("PRAGMA table_info(sessions)")}
            if "status" not in existing:
                cur.execute(
                    "ALTER TABLE sessions "
                    "ADD COLUMN status TEXT NOT NULL DEFAULT 'complete'"
                )
                cur.execute(
                    "UPDATE sessions SET status='complete' WHERE status IS NULL"
                )
            cur.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', '2')"
            )
            _log.info("garden_schema_migrated", from_version=current, to_version=2)
            current = 2

        if current < 3:
            # v2 → v3: add the ``thumbnail_svg`` column so the web
            # garden can serve cached SVG cards instead of
            # re-rendering on every fetch.
            existing = {r[1] for r in cur.execute("PRAGMA table_info(sessions)")}
            if "thumbnail_svg" not in existing:
                cur.execute(
                    "ALTER TABLE sessions ADD COLUMN thumbnail_svg TEXT"
                )
            cur.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', '3')"
            )
            _log.info("garden_schema_migrated", from_version=current, to_version=3)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_session(
        self,
        state: TreeState,
        *,
        project_path: str,
        event_log_path: Path | str,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
        detected_language: str | None = None,
        tags: Iterable[str] | None = None,
        status: str = SessionStatus.COMPLETE,
    ) -> None:
        """Persist ``state`` as one row (idempotent, INSERT OR REPLACE).

        ``state.session_id`` is the primary key. Re-saves of the same
        session are routine: the runner writes once on each periodic
        tick, once on SessionEnd, and once on shutdown.

        ``status`` is one of :class:`SessionStatus`:
        ``complete`` (default -- SessionEnd, daemon stop, idle
        timeout), ``partial`` (periodic snapshot of an in-progress
        session), or ``recovered`` (rebuilt from an orphan journal).
        Wall-clock times default sensibly.
        """
        started = started_at_ms if started_at_ms is not None else state.started_at_ms
        ended = ended_at_ms if ended_at_ms is not None else _now_ms()
        ascii_snapshot = render_final_ascii(state)
        state_json = json.dumps(state_to_dict(state), ensure_ascii=False)
        tags_str = ",".join(sorted(tags)) if tags else None
        thumbnail_svg = _render_thumbnail(state)

        with closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO sessions
                  (id, seed_hex, started_at, ended_at, project_path,
                   detected_language, theme, tool_call_count, error_count,
                   file_branch_count, final_ascii, final_state_json,
                   event_log_path, tags, status, thumbnail_svg, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 3)
                """,
                (
                    state.session_id,
                    state.seed_hex,
                    int(started),
                    int(ended) if ended is not None else None,
                    project_path,
                    detected_language,
                    state.theme,
                    int(state.event_count),
                    int(state.error_count),
                    int(state.file_branch_count),
                    ascii_snapshot,
                    state_json,
                    str(event_log_path),
                    tags_str,
                    status,
                    thumbnail_svg,
                ),
            )
        _log.info(
            "garden_saved",
            session_id=state.session_id,
            project=project_path,
            event_count=state.event_count,
            status=status,
        )

    def delete_session(self, session_id: str) -> bool:
        with closing(self._conn.cursor()) as cur:
            cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> SessionRow | None:
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
        return _row_from_sqlite(row) if row else None

    def list_sessions(self, flt: SessionFilter | None = None) -> list[SessionRow]:
        flt = flt or SessionFilter()
        clauses: list[str] = []
        params: list[Any] = []
        if flt.project_path:
            clauses.append("project_path = ?")
            params.append(flt.project_path)
        if flt.detected_language:
            clauses.append("detected_language = ?")
            params.append(flt.detected_language)
        if flt.started_after is not None:
            clauses.append("started_at >= ?")
            params.append(flt.started_after)
        if flt.started_before is not None:
            clauses.append("started_at < ?")
            params.append(flt.started_before)
        if flt.tag_contains:
            clauses.append("tags LIKE ?")
            params.append(f"%{flt.tag_contains}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM sessions {where} ORDER BY started_at DESC LIMIT ?"
        params.append(int(flt.limit))
        with closing(self._conn.cursor()) as cur:
            cur.execute(sql, tuple(params))
            return [_row_from_sqlite(r) for r in cur.fetchall()]

    def get_thumbnail(self, session_id: str) -> str | None:
        """Return the cached SVG thumbnail for ``session_id``, if any.

        Rows saved before schema v3 don't have a thumbnail. The web
        SVG endpoint falls back to rendering on the fly in that case
        and writes the result back via :meth:`set_thumbnail`.
        """
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT thumbnail_svg FROM sessions WHERE id = ?",
                (session_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        value = row[0]
        return str(value) if value else None

    def set_thumbnail(self, session_id: str, svg: str) -> None:
        """Write ``svg`` to the row's ``thumbnail_svg`` cell.

        Used by the lazy fall-back path in the web SVG endpoint:
        rows pre-dating schema v3 get their thumbnail computed once,
        on first GET, and stashed for the rest of the row's life.
        """
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "UPDATE sessions SET thumbnail_svg = ? WHERE id = ?",
                (svg, session_id),
            )

    def count_sessions(self) -> int:
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT COUNT(*) FROM sessions")
            (n,) = cur.fetchone()
            return int(n)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _row_from_sqlite(r: sqlite3.Row) -> SessionRow:
    # ``status`` is v2+. Pre-migration rows return ``None`` from
    # sqlite3.Row for missing columns; treat that as "complete"
    # so a stale row reads naturally.
    try:
        status_value = r["status"]
    except (IndexError, KeyError):
        status_value = None
    return SessionRow(
        id=r["id"],
        seed_hex=r["seed_hex"],
        started_at=int(r["started_at"]),
        ended_at=int(r["ended_at"]) if r["ended_at"] is not None else None,
        project_path=r["project_path"],
        detected_language=r["detected_language"],
        theme=r["theme"],
        tool_call_count=int(r["tool_call_count"]),
        error_count=int(r["error_count"]),
        file_branch_count=int(r["file_branch_count"]),
        final_ascii=r["final_ascii"],
        final_state_json=r["final_state_json"],
        event_log_path=r["event_log_path"],
        tags=r["tags"],
        status=status_value or SessionStatus.COMPLETE,
    )


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _render_thumbnail(state: TreeState) -> str | None:
    """Render a thumbnail SVG for ``state``. Best-effort.

    Imported lazily so this module doesn't pull the entire web
    renderer (and its color tokens) into every garden caller. The
    web renderer is the canonical source of truth for what a
    session looks like, so the thumbnail is just the same SVG --
    the browser displays it small via CSS ``aspect-ratio``.

    Returns ``None`` if rendering raises (e.g. a state with no
    trunk and no theme renderer). The save path treats ``None``
    as "no cached thumbnail yet"; the web endpoint will compute
    one lazily on first GET.
    """
    try:
        from bonsai_cc.web.svg_render import state_to_svg
    except ImportError:  # pragma: no cover - aiohttp missing in some test envs
        return None
    try:
        return state_to_svg(state)
    except Exception:  # noqa: BLE001 - thumbnail failure must never block save
        _log.warning(
            "garden_thumbnail_render_failed", session_id=state.session_id
        )
        return None

"""aiohttp web app exposing the SSE stream and static client.

Routes:

* ``GET /``                            -- single-page client (HTML).
* ``GET /events``                      -- SSE stream of state updates.
* ``GET /api/garden``                  -- JSON list of saved sessions.
* ``GET /api/session/<id>``            -- JSON of one session row.
* ``GET /api/session/<id>/events``     -- JSON dump of the session's journal.
* ``GET /replay/<id>``                 -- replay page (client app, replay mode).
* ``GET /favicon.ico``                 -- small inline icon.

The server is constructed via :func:`build_app` which wires the
``WebBroadcaster`` and the ``GardenStore`` into the route handlers.
The pipeline (``run_web_pipeline``) binds the app to a random
loopback port, writes the port to ``<home>/web.port`` atomically,
and prints the URL to stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from aiohttp import web

from bonsai_cc.garden.store import GardenStore
from bonsai_cc.log import get_logger
from bonsai_cc.runner import GrowthRunner
from bonsai_cc.web.broadcaster import WebBroadcaster

__all__ = [
    "ALLOWED_HOSTS",
    "KEEPALIVE_INTERVAL_S",
    "PORT_FILE_NAME",
    "SSE_PRELUDE",
    "build_app",
    "host_guard_middleware",
    "write_port_file",
]


# Hostnames a loopback server is willing to answer for. Anything else
# is rejected with 403 to defeat DNS rebinding: a remote site can flip
# its A record to 127.0.0.1, but the browser still sends the original
# hostname in the ``Host`` header. Refusing those requests keeps the
# garden DB and journals (which include prompts and tool inputs)
# unreachable from a rogue page. ``localhost.localdomain`` is included
# because some Linux distros set it as the default loopback name.
ALLOWED_HOSTS: frozenset[str] = frozenset({
    "127.0.0.1",
    "localhost",
    "localhost.localdomain",
    "[::1]",
    "::1",
})


_log = get_logger("bonsai_cc.web.server")


# Per-connection keepalive: write a comment line every 15s so idle
# networks (proxies, NAT timeouts) don't reap the SSE connection,
# and so a broken socket is detected within bounded time. EventSource
# silently drops comment lines, so the client doesn't have to care.
KEEPALIVE_INTERVAL_S = 15.0
PORT_FILE_NAME = "web.port"


# A single ~3 KB SSE comment line. SSE comments start with ``:`` and
# end at the next ``\n``; EventSource discards them -- but the bytes
# still count toward the browser's response buffer, so prefixing
# this guarantees the next real event is dispatched immediately
# rather than after the browser's flush threshold (~2 KB).
#
# Kept on a single line (no embedded \n) so a downstream SSE parser
# sees ONE comment, not 32 separate broken lines.
SSE_PRELUDE: bytes = (
    b": bonsai-cc SSE prelude - pads the response past Chrome's "
    b"named-event flush threshold so the snapshot arrives "
    b"immediately. EventSource discards comment lines; this is "
    b"invisible to the client. "
    + b"bonsai-cc " * 200
    + b"\n\n"
)

# Stash keys for app-scope context -- typed for mypy.
KEY_BROADCASTER: web.AppKey[WebBroadcaster] = web.AppKey("broadcaster", WebBroadcaster)
KEY_GARDEN: web.AppKey[GardenStore] = web.AppKey("garden", GardenStore)
KEY_RUNNER: web.AppKey[GrowthRunner] = web.AppKey("runner", GrowthRunner)
KEY_JOURNALS_DIR: web.AppKey[Path] = web.AppKey("journals_dir", Path)


@web.middleware
async def host_guard_middleware(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    """Reject cross-origin DNS-rebinding attempts by validating ``Host``.

    The server only binds to ``127.0.0.1`` (see ``pipeline.py``), but
    a malicious public site can short-TTL its DNS to flip ``A`` records
    to ``127.0.0.1`` and trick a victim browser into talking to this
    daemon. The browser still sends the *original* hostname in
    ``Host`` -- checking it against an allowlist closes the rebinding
    hole. Without this check, a rogue page could read the garden DB
    (which includes prompts, tool inputs, project paths) and even
    ``DELETE`` saved sessions.

    The check is intentionally on the hostname only; the port part is
    ignored because the loopback bind already pins it.
    """
    raw_host = request.headers.get("Host", "")
    # Strip the optional ``:port`` suffix. IPv6 hosts arrive as
    # ``[::1]:NNNN`` -- keep the brackets intact in that case.
    hostname = raw_host
    if hostname.startswith("["):
        end = hostname.find("]")
        if end != -1:
            hostname = hostname[: end + 1]
    elif ":" in hostname:
        hostname = hostname.rsplit(":", 1)[0]
    if hostname.lower() not in ALLOWED_HOSTS:
        _log.warning("web_host_header_rejected", host=raw_host)
        raise web.HTTPForbidden(reason="host header not in loopback allowlist")
    response: web.StreamResponse = await handler(request)
    return response


def build_app(
    broadcaster: WebBroadcaster,
    *,
    garden: GardenStore | None = None,
    journals_dir: Path | None = None,
    runner: GrowthRunner | None = None,
) -> web.Application:
    """Construct the aiohttp ``Application`` with our routes.

    The store is optional: the headless smoke test that wants to
    exercise just the SSE path can pass ``garden=None`` and the
    ``/api/garden*`` routes will return empty payloads.

    The ``runner`` is optional -- passing it lets the garden handler
    filter out the currently-live session so the client's hero-vs-
    grid dedup doesn't fire on the live session's partial saves.
    Tests that don't exercise the runner pathway can leave it unset.
    """
    app = web.Application(middlewares=[host_guard_middleware])
    app[KEY_BROADCASTER] = broadcaster
    # AppKey doesn't model Optional, so we only stash when present --
    # handlers check the in-key absence rather than a None value.
    if garden is not None:
        app[KEY_GARDEN] = garden
    if runner is not None:
        app[KEY_RUNNER] = runner
    app[KEY_JOURNALS_DIR] = journals_dir or Path()

    app.router.add_get("/", index_handler)
    app.router.add_get("/replay/{session_id}", index_handler)
    app.router.add_get("/favicon.ico", favicon_handler)
    app.router.add_get("/events", sse_handler)
    app.router.add_get("/api/garden", api_garden_handler)
    app.router.add_get("/api/garden/stats", api_garden_stats_handler)
    app.router.add_get("/api/session/{session_id}", api_session_handler)
    app.router.add_get(
        "/api/session/{session_id}/events", api_session_events_handler
    )
    app.router.add_get("/api/session/{session_id}/svg", api_session_svg_handler)
    app.router.add_delete("/api/session/{session_id}", api_session_delete_handler)
    app.router.add_get("/api/replay/{session_id}", api_replay_handler)
    return app


# ---------------------------------------------------------------------------
# Static HTML
# ---------------------------------------------------------------------------


_INDEX_TEXT_CACHE: str | None = None


def _index_html() -> str:
    """Read and cache the single-page client.

    The file lives in package resources so ``uv tool install``
    picks it up. Cached after first read; the file never changes
    at runtime.
    """
    global _INDEX_TEXT_CACHE
    if _INDEX_TEXT_CACHE is None:
        _INDEX_TEXT_CACHE = (
            resources.files("bonsai_cc.web.static")
            .joinpath("index.html")
            .read_text(encoding="utf-8")
        )
    return _INDEX_TEXT_CACHE


async def index_handler(request: web.Request) -> web.Response:
    return web.Response(
        text=_index_html(),
        content_type="text/html",
        charset="utf-8",
    )


async def favicon_handler(_: web.Request) -> web.Response:
    """Tiny inline favicon so browsers don't 404 the tab icon."""
    # 16x16 green circle PNG (base64); keeps the static dir minimal.
    import base64

    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAQUlEQVR42mNgGAWj"
        "YBSMghEFjAxQwMjAwIAOGBkIaWAERhAwgvWAOkAJYGRgIKQBaMAo2EXAGRgY6QmI"
        "EQAA1IkD/SrM4mEAAAAASUVORK5CYII="
    )
    return web.Response(body=data, content_type="image/png")


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


async def sse_handler(request: web.Request) -> web.StreamResponse:
    """Push state updates as they happen.

    Each request gets its own ``asyncio.Queue`` registered with the
    broadcaster, which is pre-seeded with an ``event: snapshot``
    frame so the first read never hangs -- even when the daemon is
    idle and no events have been published. After the snapshot the
    loop drains deltas; on a 15s idle window it writes a ``:keepalive``
    comment line to keep the connection warm.

    ``?theme=<name>`` overrides the renderer for the duration of
    this subscription -- the broadcaster's pre-rendered SVGs (which
    use ``state.theme``) are ignored and a fresh payload is built
    from the broadcaster's latest state with the override applied.
    Unknown theme names silently fall back to the auto-detected
    theme so a typo in the URL doesn't break the stream.
    """
    broadcaster: WebBroadcaster = request.app[KEY_BROADCASTER]
    theme_override = request.query.get("theme")
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )
    await resp.prepare(request)

    # SSE buffer-flush prelude. Some browsers (notably Chrome) won't
    # dispatch named SSE events until the response body has reached
    # ~2 KB. The initial idle snapshot was small enough to sit below
    # that threshold, leaving the page stuck on "connecting…" until
    # the first real state event arrived. We pre-flush a 2 KB
    # comment block so the snapshot -- which we write immediately
    # afterward -- is delivered to the EventSource right away.
    await resp.write(SSE_PRELUDE)

    queue = broadcaster.subscribe()
    try:
        while True:
            try:
                line = await asyncio.wait_for(
                    queue.get(), timeout=KEEPALIVE_INTERVAL_S
                )
            except TimeoutError:
                # Per-connection keepalive comment. EventSource
                # silently drops comment lines, so the client never
                # has to handle them.
                await resp.write(b":keepalive\n\n")
                continue
            if theme_override:
                # Re-emit with theme override applied. We read the
                # event name from the original line and rebuild the
                # payload from broadcaster.state -- so the override
                # subscriber always paints the latest frame, even if
                # the queue backlogs.
                event_name = "state"
                head = line.split("\n", 1)[0]
                if head.startswith("event: "):
                    event_name = head[len("event: "):].strip()
                line = broadcaster.format_for_theme_override(
                    event_name, theme_override
                )
            await resp.write(line.encode("utf-8"))
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        broadcaster.unsubscribe(queue)
        with contextlib.suppress(ConnectionResetError, RuntimeError):
            await resp.write_eof()
    return resp


# ---------------------------------------------------------------------------
# Garden read-only API
# ---------------------------------------------------------------------------


async def api_garden_handler(request: web.Request) -> web.Response:
    """Return the list of saved sessions, newest first.

    Query parameters:

    * ``language`` -- filter by detected_language exact match
    * ``project_path`` -- filter by project_path exact match
    * ``after`` -- only sessions started at or after this UTC ms
    * ``before`` -- only sessions started before this UTC ms
    * ``q`` -- case-insensitive substring search over session id and
      project path (client-side filter, applied after the SQL
      query to keep the store API surface small)
    * ``limit`` -- page size; default 100, capped at 500
    """
    from bonsai_cc.garden.store import SessionFilter

    store: GardenStore | None = request.app.get(KEY_GARDEN)
    if store is None:
        return web.json_response([])
    q = request.query
    try:
        limit = min(500, max(1, int(q.get("limit", "100"))))
    except ValueError:
        limit = 100
    try:
        started_after = int(q["after"]) if "after" in q else None
    except ValueError:
        started_after = None
    try:
        started_before = int(q["before"]) if "before" in q else None
    except ValueError:
        started_before = None
    flt = SessionFilter(
        detected_language=q.get("language") or None,
        project_path=q.get("project_path") or None,
        started_after=started_after,
        started_before=started_before,
        limit=limit,
    )
    rows = store.list_sessions(flt)
    # Exclude the currently-live session from the garden grid. The
    # client's hero already shows it via SSE; including it here
    # would land its session_id in the page's `gardenSessionIds`
    # set, and the hero-vs-grid dedup gate would then forceHeroIdle
    # on the next SSE state event -- the regression that made the
    # live tree disappear after the runner's first partial save.
    # The session reappears here naturally once it's finalised
    # (SessionEnd flips `current_live_session_id` to None).
    runner: GrowthRunner | None = request.app.get(KEY_RUNNER)
    if runner is not None:
        live_id = runner.current_live_session_id
        if live_id:
            rows = [r for r in rows if r.id != live_id]
    needle = (q.get("q") or "").strip().lower()
    if needle:
        rows = [
            r for r in rows
            if needle in r.id.lower()
            or (r.project_path or "").lower().find(needle) != -1
        ]
    return web.json_response([_row_to_dict(r) for r in rows])


async def api_garden_stats_handler(request: web.Request) -> web.Response:
    """Hero-band aggregate stats: total time, sessions, streak.

    Computed on every request because the underlying SQLite query
    is cheap (a single full-table scan) and the values aren't worth
    caching -- the user expects an exact count when they reload.
    """
    from bonsai_cc.garden.stats import SessionTime, compute_stats
    from bonsai_cc.garden.store import SessionFilter

    store: GardenStore | None = request.app.get(KEY_GARDEN)
    if store is None:
        return web.json_response({
            "total_seconds": 0,
            "sessions_count": 0,
            "sessions_this_month": 0,
            "streak_days": 0,
        })
    # The stats are aggregates over the entire garden, not a page --
    # pass a generous limit so a busy user with 1 000+ sessions
    # still gets accurate numbers.
    rows = store.list_sessions(SessionFilter(limit=100_000))
    times = [
        SessionTime(
            started_at_ms=r.started_at,
            ended_at_ms=r.ended_at,
            status=r.status,
        )
        for r in rows
    ]
    stats = compute_stats(times)
    return web.json_response({
        "total_seconds": stats.total_seconds,
        "sessions_count": stats.sessions_count,
        "sessions_this_month": stats.sessions_this_month,
        "streak_days": stats.streak_days,
    })


async def api_session_handler(request: web.Request) -> web.Response:
    store: GardenStore | None = request.app.get(KEY_GARDEN)
    sid = request.match_info["session_id"]
    if store is None:
        raise web.HTTPNotFound(reason=f"no garden store for {sid}")
    row = store.get_session(sid)
    if row is None:
        raise web.HTTPNotFound(reason=f"session {sid!r} not found")
    return web.json_response(_row_to_dict(row))


async def api_session_svg_handler(request: web.Request) -> web.Response:
    """Return the saved session's SVG thumbnail.

    Reads the cached ``thumbnail_svg`` column when present (the
    common case for sessions saved on schema v3+). Falls back to
    a one-shot live render for older rows, writing the result back
    so the next GET is cache-hit.

    Response always carries an aggressive cache header: the
    thumbnail is a function of the final state and never changes
    after the row is saved.
    """
    from bonsai_cc.garden.store import load_state_from_row
    from bonsai_cc.web.svg_render import state_to_svg

    store: GardenStore | None = request.app.get(KEY_GARDEN)
    sid = request.match_info["session_id"]
    if store is None:
        raise web.HTTPNotFound(reason=f"no garden store for {sid}")
    # ``?mode=dark`` renders the dark-mode variant fresh. The name
    # used to be ``?theme=dark`` but ``theme`` now carries the
    # renderer override (``?theme=sakura``); the rename keeps the
    # two concerns separate. ``?theme=<name>`` overrides the
    # renderer regardless of the saved row's theme -- pickers in
    # the grid use this to preview a different bonsai theme.
    mode = "dark" if request.query.get("mode") == "dark" else "light"
    theme_override = request.query.get("theme")
    # Only the canonical (mode=light, no override) variant is
    # cached in the DB -- dark/override are rendered fresh.
    cacheable = mode == "light" and not theme_override
    if cacheable:
        cached = store.get_thumbnail(sid)
        if cached:
            return web.Response(
                text=cached,
                content_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
    row = store.get_session(sid)
    if row is None:
        raise web.HTTPNotFound(reason=f"session {sid!r} not found")
    state = load_state_from_row(row)
    if state is None:
        raise web.HTTPNotFound(reason=f"no state payload for {sid!r}")
    svg = state_to_svg(state, theme=mode, theme_override=theme_override)
    if cacheable:
        # Lazy backfill: writes the freshly-rendered SVG into the
        # row so the next GET is cache-hit. Best-effort; a write
        # failure must not block the response.
        with contextlib.suppress(Exception):
            store.set_thumbnail(sid, svg)
    return web.Response(
        text=svg,
        content_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


async def api_session_delete_handler(request: web.Request) -> web.Response:
    """Delete a saved session. Returns 204 on success, 404 if missing."""
    store: GardenStore | None = request.app.get(KEY_GARDEN)
    sid = request.match_info["session_id"]
    if store is None:
        raise web.HTTPNotFound(reason=f"no garden store for {sid}")
    deleted = store.delete_session(sid)
    if not deleted:
        raise web.HTTPNotFound(reason=f"session {sid!r} not found")
    return web.Response(status=204)


async def api_session_events_handler(request: web.Request) -> web.Response:
    """Dump every record in a session's journal as a JSON array.

    Used by the client's replay mode. Memory: a session journal
    typically fits in a few hundred KiB, so loading it all into
    memory is fine for v1. If we ever see sessions north of 100 MB,
    switch to a streaming reader.
    """
    store: GardenStore | None = request.app.get(KEY_GARDEN)
    sid = request.match_info["session_id"]
    if store is None:
        raise web.HTTPNotFound(reason=f"no garden store for {sid}")
    row = store.get_session(sid)
    if row is None:
        raise web.HTTPNotFound(reason=f"session {sid!r} not found")
    path = Path(row.event_log_path)
    if not path.exists():
        raise web.HTTPNotFound(
            reason=f"journal for {sid!r} no longer on disk: {path}"
        )
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return web.json_response(records)


async def api_replay_handler(request: web.Request) -> web.StreamResponse:
    """Server-side replay: walk the journal, push state snapshots
    over SSE with a configurable delay between events.

    Query params:

    * ``speed`` -- speed multiplier (default 1.0). 200 ms / speed
      between events. ``speed=0`` plays everything as fast as
      possible (smoke-test path).
    * ``from`` -- start replay from this event index (default 0).
      Lets the client implement resume after pause: it tracks how
      many events it's received and reconnects with ``from=<n>``.
    * ``base_delay_ms`` -- override the 200 ms base delay (for
      testing).

    Reuses the same SSE format as ``/events`` so the client doesn't
    need a separate handler for live vs replay. Each payload now
    carries ``replay_idx`` and ``replay_total`` so the client can
    render a progress bar without re-counting the journal.
    """
    store: GardenStore | None = request.app.get(KEY_GARDEN)
    sid = request.match_info["session_id"]
    if store is None:
        raise web.HTTPNotFound(reason=f"no garden store for {sid}")
    row = store.get_session(sid)
    if row is None:
        raise web.HTTPNotFound(reason=f"session {sid!r} not found")
    journal_path = Path(row.event_log_path)
    if not journal_path.exists():
        raise web.HTTPNotFound(
            reason=f"journal for {sid!r} no longer on disk: {journal_path}"
        )
    speed_raw = request.query.get("speed", "1.0")
    base_ms_raw = request.query.get("base_delay_ms", "200")
    from_raw = request.query.get("from", "0")
    theme_override = request.query.get("theme")
    try:
        speed = max(0.0, float(speed_raw))
    except ValueError:
        speed = 1.0
    try:
        base_delay_ms = max(0.0, float(base_ms_raw))
    except ValueError:
        base_delay_ms = 200.0
    try:
        start_from = max(0, int(from_raw))
    except ValueError:
        start_from = 0
    delay_s = 0.0 if speed == 0.0 else (base_delay_ms / 1000.0) / speed

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)
    # Same prelude trick as /events so the first replay state lands
    # in the browser immediately rather than after a few KB of
    # buffering.
    await resp.write(SSE_PRELUDE)
    try:
        await _stream_replay(
            resp, row, journal_path, delay_s, start_from,
            theme_override=theme_override,
        )
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(ConnectionResetError, RuntimeError):
            await resp.write_eof()
    return resp


def _count_journal_records(journal_path: Path) -> int:
    """Count the number of valid records in ``journal_path``.

    Used to surface a total to the client's progress bar. Cheap
    line-count + JSON sniff; the journal is opened twice (once for
    count, once for replay) but the file is in-cache by then.
    """
    n = 0
    with journal_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("raw"), dict):
                n += 1
    return n


async def _stream_replay(
    resp: web.StreamResponse,
    row: Any,
    journal_path: Path,
    delay_s: float,
    start_from: int = 0,
    *,
    theme_override: str | None = None,
) -> None:
    """Yield-and-write loop for replay SSE.

    Walks the journal from line 0, building state via ``apply_event``.
    Records with index < ``start_from`` advance the state silently
    (so resume from position N produces the same state as a full
    play to N). From ``start_from`` onward each record produces an
    ``event: state`` SSE frame.

    Kept separate so the route handler stays small and the test
    suite can exercise this helper without spinning up an aiohttp
    request.
    """
    from bonsai_cc.events.journal import read_journal
    from bonsai_cc.events.models import (
        PostToolUseEvent,
        PostToolUseFailureEvent,
        parse_event,
    )
    from bonsai_cc.growth.apply import apply_event
    from bonsai_cc.growth.state import state_to_dict
    from bonsai_cc.runner import build_initial_state
    from bonsai_cc.web.svg_render import state_to_svg

    total = _count_journal_records(journal_path)

    state = build_initial_state(
        row.id,
        theme=row.theme or "default",
        started_at_ms=row.started_at,
    )
    last_event_name: str | None = None
    sent = 0
    # Per-tool counts kept in lockstep with apply_event so the
    # sidebar updates as the replay walks the journal.
    tool_counts: dict[str, int] = {}
    for rec in read_journal(journal_path):
        raw = rec.get("raw")
        idx_val = rec.get("idx")
        if not isinstance(raw, dict) or not isinstance(idx_val, int):
            continue
        try:
            event = parse_event(raw)
        except Exception:  # noqa: BLE001 - skip malformed records
            continue
        state = apply_event(state, event, event_idx=idx_val)
        last_event_name = event.hook_event_name
        if isinstance(event, (PostToolUseEvent, PostToolUseFailureEvent)):
            tname = event.tool_name or "Other"
            tool_counts[tname] = tool_counts.get(tname, 0) + 1
        if idx_val < start_from:
            # Catch-up: silently apply but don't emit. The client
            # already has this prefix's resulting state because it
            # got it on the previous (now-closed) connection.
            continue
        svg_light = state_to_svg(state, theme="light", theme_override=theme_override)
        svg_dark = state_to_svg(state, theme="dark", theme_override=theme_override)
        payload = {
            "state": state_to_dict(state),
            # The replay payload mirrors the broadcaster's live
            # payload so the client's ``applyPayload`` paints the
            # canvas the same way and the sidebar receives a
            # populated counts dict. Both theme variants are
            # included so the client can flip dark mode mid-replay
            # without reconnecting the SSE stream; ``svg`` aliases
            # the light variant for older payload consumers.
            "svg": svg_light,
            "svg_light": svg_light,
            "svg_dark": svg_dark,
            "tool_counts": dict(tool_counts),
            "error_count": int(state.error_count),
            "idle": False,
            "project_root": row.project_path or "",
            "event_name": last_event_name,
            "seconds_ago": 0.0,
            "banner": f"replay of {row.id[:8]} - event {idx_val + 1}",
            "ts": int(datetime.now(UTC).timestamp() * 1000),
            "replay": True,
            "replay_idx": idx_val,
            "replay_total": total,
        }
        line_out = (
            f"event: state\ndata: "
            f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
        )
        await resp.write(line_out.encode("utf-8"))
        sent += 1
        if delay_s > 0:
            await asyncio.sleep(delay_s)
    # Final marker so the client knows replay is over.
    await resp.write(
        f"event: replay_done\ndata: {json.dumps({'count': sent})}\n\n".encode()
    )


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Marshal a ``SessionRow`` to JSON-friendly dict."""
    return {
        "id": row.id,
        "seed_hex": row.seed_hex,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "project_path": row.project_path,
        "detected_language": row.detected_language,
        "theme": row.theme,
        "tool_call_count": row.tool_call_count,
        "error_count": row.error_count,
        "file_branch_count": row.file_branch_count,
        "event_log_path": row.event_log_path,
        "tags": row.tags,
        "status": row.status,
    }


# ---------------------------------------------------------------------------
# Port file (atomic write -- same approach as the daemon socket port file)
# ---------------------------------------------------------------------------


def write_port_file(port_path: Path, port: int) -> None:
    """Write ``port`` to ``port_path`` via tmp + atomic rename.

    Atomic on both POSIX and Windows so concurrent reads never see
    a torn integer. Mirrors ``ipc.server._write_port_file_atomically``.
    """
    port_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = port_path.with_suffix(port_path.suffix + ".tmp")
    try:
        tmp.write_text(str(port), encoding="utf-8")
        tmp.replace(port_path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)

"""``WebBroadcaster`` -- the runner's sink for live state updates.

The growth runner calls ``renderer.set_state(state, last_event_name=...)``
on whatever object it was handed. ``WebBroadcaster`` implements that
contract by pushing a JSON payload onto every SSE subscriber queue.
The runner is renderer-agnostic on purpose (the architectural seam);
the duck-typed ``set_state`` shape lets tests substitute a recorder.

The broadcaster also holds the most recent state so a late-joining
client (e.g. a browser tab opened after the daemon has already
processed events) receives the full picture on first connect.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from bonsai_cc.growth.state import TreeState, state_to_dict
from bonsai_cc.log import get_logger
from bonsai_cc.web.render.canvas import build_idle_svg
from bonsai_cc.web.svg_render import state_to_svg

__all__ = ["WebBroadcaster"]


_log = get_logger("bonsai_cc.web.broadcaster")


class WebBroadcaster:
    """Holds the current ``TreeState`` and fans it out to SSE clients.

    Subscribers are anonymous ``asyncio.Queue``s; each represents
    one open browser tab. New subscribers receive the current
    state as their first message; subsequent ``set_state`` calls
    enqueue an update on every queue. Closing a tab removes the
    queue from the pool.

    The broadcaster is intentionally synchronous on the writer
    side (``set_state``) -- the runner calls it inside a tight
    handler loop and shouldn't ``await``. ``put_nowait`` keeps the
    fan-out cheap; a misbehaving client whose queue is full takes
    the loss rather than backpressuring the runner.
    """

    # Per-subscriber queue depth. ~3s at 30 events/s before drops.
    _QUEUE_MAX = 100

    def __init__(
        self,
        *,
        banner: str | None = None,
        project_root: str | None = None,
    ) -> None:
        self._state: TreeState | None = None
        self._banner = banner
        self._project_root = project_root
        self._subscribers: list[asyncio.Queue[str]] = []
        self._last_event_name: str | None = None
        self._last_event_monotonic: float | None = None
        self._created_ms = int(datetime.now(UTC).timestamp() * 1000)
        # Per-tool cumulative counts for the web sidebar. The runner
        # passes a fresh snapshot on every ``set_state``; we forward
        # it verbatim in the SSE payload.
        self._tool_counts: dict[str, int] = {}
        # Optional back-reference to the runner so the payload can
        # carry the currently-live session id. Wired up post-init
        # via ``attach_runner`` because runner and broadcaster
        # construct each other in opposing order. Duck-typed:
        # anything with a ``current_live_session_id`` attribute works,
        # so the tests can pass a small stub.
        self._runner: Any | None = None

    def attach_runner(self, runner: Any) -> None:
        """Wire a runner reference into the broadcaster.

        Allows the payload to expose ``live_session_id`` so the
        client can react to the live → idle transition (refresh the
        garden grid immediately on SessionEnd instead of waiting up
        to 30s for the next poll).
        """
        self._runner = runner

    @property
    def banner(self) -> str | None:
        return self._banner

    @property
    def state(self) -> TreeState | None:
        return self._state

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------------
    # Writer side -- what the runner calls.
    # ------------------------------------------------------------------

    def set_state(
        self,
        state: TreeState,
        *,
        last_event_name: str | None = None,
        tool_counts: dict[str, int] | None = None,
    ) -> None:
        """Replace the current state + broadcast to every subscriber.

        Tracks the wall-monotonic timestamp of the latest event so
        the status payload can carry "Xs ago" hints for the client.
        ``tool_counts`` flows through to the SSE payload for the
        right-side tool stats sidebar.
        """
        prev_count = self._state.event_count if self._state else 0
        self._state = state
        if state.event_count > prev_count and last_event_name is not None:
            self._last_event_monotonic = time.monotonic()
            self._last_event_name = last_event_name
        if tool_counts is not None:
            self._tool_counts = dict(tool_counts)
        payload = self._build_payload()
        self._fanout("state", payload)

    def set_banner(self, text: str) -> None:
        self._banner = text
        self._fanout("state", self._build_payload())

    # ------------------------------------------------------------------
    # Reader side -- what the SSE handler calls.
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[str]:
        """Register a new SSE listener. Returns its message queue.

        Always pre-seeds the queue with an ``event: snapshot`` frame
        so a freshly-connected client *immediately* has something to
        render -- even when the daemon just started and no Claude
        Code session has fired its first event yet. The client uses
        the arrival of this snapshot to flip its UI out of the
        "connecting…" placeholder. Without this, an idle daemon
        leaves the page stuck on "connecting…" forever and the SSE
        endpoint serves 0 bytes -- the bug the overnight build
        shipped with.

        Subsequent updates fan out as ``event: state`` deltas.
        """
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._QUEUE_MAX)
        self._subscribers.append(q)
        with contextlib.suppress(asyncio.QueueFull):  # pragma: no cover
            q.put_nowait(_sse_format("snapshot", self._build_payload()))
        _log.info("web_subscriber_added", count=len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            return
        _log.info("web_subscriber_removed", count=len(self._subscribers))

    def heartbeat(self) -> None:
        """Push a heartbeat message to every subscriber.

        Some proxies and load balancers reap idle HTTP connections
        after 30s. A 25s heartbeat from the server side keeps the
        SSE stream open even during long quiet stretches between
        Claude Code events.
        """
        ts = int(datetime.now(UTC).timestamp() * 1000)
        self._fanout("heartbeat", {"ts": ts})

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fanout(self, event_name: str, payload: dict[str, Any]) -> None:
        if not self._subscribers:
            return
        line = _sse_format(event_name, payload)
        dropped = 0
        for q in self._subscribers:
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            _log.warning(
                "web_fanout_dropped",
                sse_event=event_name,
                dropped=dropped,
                total=len(self._subscribers),
            )

    def format_for_theme_override(
        self, event_name: str, theme_override: str
    ) -> str:
        """SSE-formatted state/snapshot line with ``theme_override``.

        Used by SSE handlers that received a ``?theme=`` query
        parameter -- they re-build the payload from the broadcaster's
        latest state with the override applied, ignoring the
        broadcaster's own pre-rendered SVGs (which were rendered
        for ``state.theme``, the auto-detected language).

        The override mode reads ``self._state`` directly, so a
        subscriber in override mode always sees the most recent
        state -- slightly more drift-tolerant than the regular
        fanout (which queues each frame), and that's the desired
        behaviour: we don't need every tick of an override
        subscriber, just the latest.
        """
        payload = self._build_payload(theme_override=theme_override)
        return _sse_format(event_name, payload)

    def _build_payload(self, theme_override: str | None = None) -> dict[str, Any]:
        """Snapshot/state payload -- the canonical wire envelope.

        ``state`` is ``None`` when the daemon is idle (no Claude
        Code session has fired its first event). ``idle: True`` is
        the client's signal to render the empty-pot / waiting UI
        instead of a tree. Once a real event lands, ``state`` is
        populated and ``idle: False``.

        Includes a pre-rendered ``svg`` string of the current state
        -- the client just drops it into the DOM, guaranteeing the
        live browser shows the same image as the headless test
        screenshots. Without this we'd have to mirror twelve
        per-theme renderers in JavaScript and keep them in lock-step.
        """
        seconds_ago: float | None = None
        if self._last_event_monotonic is not None:
            seconds_ago = max(0.0, time.monotonic() - self._last_event_monotonic)
        # Render both theme variants up-front so the client can
        # toggle dark mode without reconnecting the SSE stream.
        # ~5-8KB per variant on loopback; doubling is well within
        # the per-frame budget. The client picks `svg_light` or
        # `svg_dark` based on the active theme; `svg` is kept as
        # an alias for the light variant so older tests / fixture
        # consumers don't have to change shape.
        if self._state is not None:
            svg_light = state_to_svg(
                self._state, theme="light", theme_override=theme_override
            )
            svg_dark = state_to_svg(
                self._state, theme="dark", theme_override=theme_override
            )
        else:
            # Idle path: render a placeholder SVG (pot + seedling +
            # waiting text) server-side so (a) the snapshot payload
            # is large enough to clear the browser's SSE buffer
            # threshold, and (b) the client doesn't need its own
            # renderer for the empty state. ``theme_override`` is
            # ignored here -- the idle placeholder has no tree, so
            # there's nothing to re-render.
            svg_light = build_idle_svg(
                project_root=self._project_root, theme="light"
            )
            svg_dark = build_idle_svg(
                project_root=self._project_root, theme="dark"
            )
        live_session_id: str | None = None
        if self._runner is not None:
            live_session_id = getattr(self._runner, "current_live_session_id", None)
        return {
            "state": state_to_dict(self._state) if self._state is not None else None,
            "svg": svg_light,
            "svg_light": svg_light,
            "svg_dark": svg_dark,
            "event_name": self._last_event_name,
            "seconds_ago": seconds_ago,
            "banner": self._banner,
            "ts": int(datetime.now(UTC).timestamp() * 1000),
            "idle": self._state is None,
            # The runner's currently-tracked live session, or None
            # once the bound session has been finalised via
            # SessionEnd. Client uses the non-null → null transition
            # to refresh the garden grid immediately.
            "live_session_id": live_session_id,
            "project_root": self._project_root,
            "tool_counts": dict(self._tool_counts),
            "error_count": int(self._state.error_count) if self._state else 0,
        }


def _sse_format(event: str, payload: dict[str, Any]) -> str:
    """Serialise one SSE message.

    The text/event-stream protocol expects ``event: <name>`` then
    ``data: <line>`` then a blank line. We compact the JSON since
    the payload travels over localhost and bandwidth doesn't
    matter, but readability of single-line ``data:`` lines does.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n"

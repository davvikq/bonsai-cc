"""``WebBroadcaster`` unit tests.

The end-to-end SSE behaviour is exercised by ``test_web_server`` —
this file pins down the broadcaster's internal contracts directly:
heartbeat fanout, slow-client backpressure (drop, don't block),
banner updates, and late-join seeding.
"""

from __future__ import annotations

import asyncio
import json

from bonsai_cc.growth.state import demo_tree
from bonsai_cc.web.broadcaster import WebBroadcaster


def _parse_sse_line(line: str) -> tuple[str, dict[str, object]]:
    """Crack a single ``event: ... \\ndata: ...\\n\\n`` blob."""
    parts = line.strip().split("\n")
    event = parts[0].split(":", 1)[1].strip()
    data = parts[1].split(":", 1)[1].strip()
    return event, json.loads(data)


async def test_payload_carries_live_session_id_from_attached_runner() -> None:
    """The broadcaster's payload exposes ``live_session_id`` so the
    client can detect the live → idle transition and refresh the
    garden grid immediately on SessionEnd. The value comes from the
    attached runner's ``current_live_session_id`` property; a duck-
    typed stub is enough here."""
    class _StubRunner:
        current_live_session_id = "alpha"

    b = WebBroadcaster()
    q = b.subscribe()
    # Pre-attach snapshot — no runner yet → live_session_id is None.
    msg0 = await asyncio.wait_for(q.get(), timeout=1.0)
    _, payload0 = _parse_sse_line(msg0)
    assert payload0["live_session_id"] is None

    # Attach the runner; the NEXT state push should expose its id.
    b.attach_runner(_StubRunner())
    b.set_state(demo_tree("alpha"), last_event_name="SessionStart")
    msg1 = await asyncio.wait_for(q.get(), timeout=1.0)
    _, payload1 = _parse_sse_line(msg1)
    assert payload1["live_session_id"] == "alpha"


async def test_runner_session_end_broadcasts_with_null_live_session_id() -> None:
    """Reorder gate: SessionEnd flips ``_saved_on_session_end`` to
    True BEFORE ``set_state`` runs, so the SessionEnd-bearing SSE
    frame goes out with ``live_session_id: null``. Without that
    reorder the client never sees the transition and has to wait
    for the next 30s ``/api/garden`` poll to discover the saved
    session."""
    from bonsai_cc.events.bus import IngestedEvent, reset_event_bus_for_tests
    from bonsai_cc.events.models import parse_event
    from bonsai_cc.runner import GrowthRunner

    bus = reset_event_bus_for_tests()
    b = WebBroadcaster()
    runner = GrowthRunner(b, bus, theme="default")  # type: ignore[arg-type]
    b.attach_runner(runner)

    q = b.subscribe()
    # Drop the initial empty snapshot.
    await asyncio.wait_for(q.get(), timeout=1.0)

    await runner.start()
    await bus.publish(IngestedEvent(
        idx=0,
        event=parse_event({"session_id": "live-1", "hook_event_name": "SessionStart"}),
    ))
    for _ in range(50):
        await asyncio.sleep(0)
        if bus.qsize() == 0:
            break
    # SessionStart frame: live_session_id should be the bound id.
    msg_start = await asyncio.wait_for(q.get(), timeout=1.0)
    _, payload_start = _parse_sse_line(msg_start)
    assert payload_start["live_session_id"] == "live-1"

    await bus.publish(IngestedEvent(
        idx=1,
        event=parse_event({"session_id": "live-1", "hook_event_name": "SessionEnd"}),
    ))
    for _ in range(50):
        await asyncio.sleep(0)
        if bus.qsize() == 0:
            break
    await runner.stop()

    msg_end = await asyncio.wait_for(q.get(), timeout=1.0)
    _, payload_end = _parse_sse_line(msg_end)
    # The whole point of the reorder: SessionEnd frame must carry
    # the null transition so the client refreshes the grid now.
    assert payload_end["live_session_id"] is None, (
        f"expected live_session_id=null on SessionEnd frame, got "
        f"{payload_end['live_session_id']!r} — runner ordered "
        f"set_state before flipping _saved_on_session_end"
    )


async def test_set_state_fans_out_to_every_subscriber() -> None:
    """One state push should land on every queue in the pool."""
    b = WebBroadcaster()
    q1 = b.subscribe()
    q2 = b.subscribe()
    assert b.subscriber_count == 2
    # Drain the idle seed snapshot from each queue.
    await asyncio.wait_for(q1.get(), timeout=1.0)
    await asyncio.wait_for(q2.get(), timeout=1.0)

    b.set_state(demo_tree("multi-sub"), last_event_name="SessionStart")

    msg1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    msg2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    name1, payload1 = _parse_sse_line(msg1)
    name2, payload2 = _parse_sse_line(msg2)
    assert name1 == name2 == "state"
    assert payload1 == payload2
    assert payload1["state"]["session_id"] == "multi-sub"  # type: ignore[index]


async def test_heartbeat_pushes_to_every_subscriber() -> None:
    """``heartbeat()`` is no longer wired into the SSE handler
    (per-connection ``:keepalive`` comment lines replaced it) but
    the fanout method stays around because the broadcaster owns the
    subscriber pool. Keep the contract pinned in case a future
    caller wants JSON heartbeats."""
    b = WebBroadcaster()
    q = b.subscribe()
    # Drain the idle seed snapshot.
    await asyncio.wait_for(q.get(), timeout=1.0)
    b.heartbeat()
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    name, payload = _parse_sse_line(msg)
    assert name == "heartbeat"
    assert "ts" in payload


async def test_full_subscriber_queue_drops_message_without_blocking() -> None:
    """A misbehaving (slow) client must not backpressure the runner.

    Saturate one subscriber's queue, then prove a second subscriber
    still receives the next push. The dropped subscriber stays in
    the pool; only the in-flight message for it is discarded.
    """
    b = WebBroadcaster()
    b.subscribe()  # slow client: deliberately never drained
    q_fast = b.subscribe()

    # Saturate the slow subscriber's queue without draining it.
    for _ in range(b._QUEUE_MAX):
        b.set_state(demo_tree("filler"), last_event_name="PostToolUse")
    # Drain q_fast so it has headroom.
    while not q_fast.empty():
        q_fast.get_nowait()

    # One more push: q_fast must still receive; q_slow drops it silently.
    b.set_state(demo_tree("survivor"), last_event_name="PostToolUse")
    msg = await asyncio.wait_for(q_fast.get(), timeout=1.0)
    name, payload = _parse_sse_line(msg)
    assert name == "state"
    assert payload["state"]["session_id"] == "survivor"  # type: ignore[index]
    assert b.subscriber_count == 2


async def test_new_subscriber_is_seeded_with_snapshot_of_current_state() -> None:
    """Late-join contract: a tab opened mid-session sees the tree
    immediately, not after the next event. The seed always uses
    ``event: snapshot`` so the client can distinguish it from a
    delta."""
    b = WebBroadcaster(banner="hello")
    b.set_state(demo_tree("seed-me"), last_event_name="SessionStart")
    q = b.subscribe()
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    name, payload = _parse_sse_line(msg)
    assert name == "snapshot"
    assert payload["state"]["session_id"] == "seed-me"  # type: ignore[index]
    assert payload["banner"] == "hello"
    assert payload["idle"] is False


async def test_subscribe_before_any_state_seeds_idle_snapshot() -> None:
    """The bug the overnight build shipped with: an idle daemon was
    sending zero bytes on /events. Now every new subscriber is
    seeded with an ``event: snapshot`` carrying ``idle: true`` so
    the client immediately knows the connection is up — even though
    no Claude Code session has fired its first event yet."""
    b = WebBroadcaster(project_root="/some/where")
    q = b.subscribe()
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    name, payload = _parse_sse_line(msg)
    assert name == "snapshot"
    assert payload["state"] is None
    assert payload["idle"] is True
    assert payload["project_root"] == "/some/where"


async def test_set_banner_repushes_state_when_available() -> None:
    """Banner edits propagate to open clients (so the status line
    updates without waiting for the next hook event)."""
    b = WebBroadcaster(banner="old")
    b.set_state(demo_tree("b1"), last_event_name="SessionStart")
    q = b.subscribe()
    # Drain the seed snapshot push.
    await asyncio.wait_for(q.get(), timeout=1.0)
    b.set_banner("new banner")
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    name, payload = _parse_sse_line(msg)
    assert name == "state"
    assert payload["banner"] == "new banner"


async def test_payload_includes_prerendered_svg_when_state_exists() -> None:
    """Phase 10 batch 2 contract: the broadcaster pre-renders the
    SVG server-side and ships it in the SSE payload so the browser
    matches the headless screenshots exactly (no JS-side renderer
    mirror to keep in lock-step)."""
    b = WebBroadcaster()
    q = b.subscribe()
    # Drain the idle snapshot — even when state is None, the
    # broadcaster ships a placeholder SVG so the client always has
    # something to drop into the DOM.
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    _, idle_payload = _parse_sse_line(msg)
    assert idle_payload["state"] is None
    assert isinstance(idle_payload["svg"], str)
    assert idle_payload["svg"].startswith("<svg")
    assert idle_payload["svg"].endswith("</svg>")

    b.set_state(demo_tree("svg-1"), last_event_name="SessionStart")
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    _, payload = _parse_sse_line(msg)
    assert isinstance(payload["svg"], str)
    assert payload["svg"].startswith("<svg")
    assert payload["svg"].endswith("</svg>")


async def test_idle_snapshot_payload_is_substantial() -> None:
    """The "connecting…" stuck-banner bug was caused by the idle
    snapshot being too small (~200 bytes) — under the browser SSE
    buffer threshold. The fix is to render a placeholder SVG so the
    payload exceeds the threshold. Pin a floor of 2 KB.

    If a future refactor drops the SVG body or substitutes a tiny
    placeholder, this test fails and the bug returns."""
    b = WebBroadcaster(project_root="/work/demo")
    q = b.subscribe()
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    _, payload = _parse_sse_line(msg)
    assert isinstance(payload["svg"], str)
    assert len(payload["svg"]) > 2000, (
        f"idle snapshot SVG only {len(payload['svg'])} bytes — "
        f"browser SSE buffer threshold is ~2 KB"
    )
    # The placeholder must name the launch directory so users know
    # where they are.
    assert "/work/demo" in payload["svg"]
    # And it must say something readable.
    assert "Waiting" in payload["svg"]


async def test_tool_counts_flow_through_payload() -> None:
    """The runner-supplied ``tool_counts`` dict must round-trip
    through the broadcaster onto the SSE wire so the sidebar
    can render."""
    b = WebBroadcaster()
    q = b.subscribe()
    # Drain the idle snapshot.
    await asyncio.wait_for(q.get(), timeout=1.0)
    b.set_state(
        demo_tree("tools-1"),
        last_event_name="PostToolUse",
        tool_counts={"Bash": 3, "Edit": 7, "Read": 1},
    )
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    _, payload = _parse_sse_line(msg)
    assert payload["tool_counts"] == {"Bash": 3, "Edit": 7, "Read": 1}
    # ``error_count`` is sourced from state; demo_tree has 1.
    assert payload["error_count"] == 1


async def test_unsubscribe_is_idempotent() -> None:
    """Double-unsubscribe must not raise — defensive against
    unclean tab close races on the SSE handler side."""
    b = WebBroadcaster()
    q = b.subscribe()
    b.unsubscribe(q)
    b.unsubscribe(q)  # second call is a no-op
    assert b.subscriber_count == 0

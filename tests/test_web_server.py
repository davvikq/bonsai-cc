"""HTTP + SSE end-to-end: bus → broadcaster → /events → client.

These tests prove the web renderer is actually fed by the live
event pipeline. They use aiohttp's TestClient so no real port is
bound; the SSE stream is consumed as a streaming HTTP response.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bonsai_cc.events.bus import IngestedEvent, reset_event_bus_for_tests
from bonsai_cc.events.journal import JournalRegistry
from bonsai_cc.events.models import parse_event
from bonsai_cc.garden.store import GardenStore
from bonsai_cc.growth.state import demo_tree
from bonsai_cc.runner import GrowthRunner
from bonsai_cc.web.broadcaster import WebBroadcaster
from bonsai_cc.web.server import build_app

# ---------------------------------------------------------------------------
# Static-asset and offline guarantees
# ---------------------------------------------------------------------------


async def test_index_html_is_offline_capable() -> None:
    """No CDN / external dependency — the bug report's hard
    requirement. The page is pure HTML + CSS + vanilla JS; SVG
    bodies are streamed from the server over SSE rather than
    embedded in the page so the client stays small."""
    b = WebBroadcaster()
    app = build_app(b)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        assert resp.status == 200
        body = await resp.text()
    # Crude but effective: no fetch of CDN assets.
    for needle in ('src="http://', 'src="https://',
                   'href="http://', 'href="https://'):
        assert needle not in body, f"external asset reference: {needle}"
    # The canvas host element exists — that's where the streamed
    # SVG lands.
    assert 'id="canvas-host"' in body


async def test_index_html_wires_up_idle_empty_state() -> None:
    """The shipped client must transition out of "connecting…" on
    the first snapshot frame, and must render the idle UI from the
    server-supplied SVG (no JS-side idle renderer). Pin the strings
    a regression would silently delete."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    # Snapshot handler exists.
    assert 'addEventListener("snapshot"' in body
    # First snapshot/state payload flips the ready flag and clears
    # the connecting banner. The variable name is load-bearing.
    assert "ready = true" in body
    # Idle footer copy is wired in the client (the exact string the
    # museum-label STATUS cell renders when no session is active).
    assert "waiting for Claude Code activity" in body


async def test_favicon_is_inline() -> None:
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/favicon.ico")
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("image/")
        body = await resp.read()
        # PNG magic number.
        assert body[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# DNS-rebinding hardening: Host header validation
# ---------------------------------------------------------------------------


async def test_host_header_loopback_names_pass() -> None:
    """The allowlist accepts every name that resolves to the loopback
    interface: ``127.0.0.1``, ``localhost``, ``::1`` (bracketed and
    plain), plus the ``localhost.localdomain`` Linux quirk.
    """
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        for host in (
            "127.0.0.1",
            "127.0.0.1:8080",
            "localhost",
            "localhost:8080",
            "localhost.localdomain",
            "[::1]",
            "[::1]:8080",
        ):
            resp = await client.get("/", headers={"Host": host})
            assert resp.status == 200, f"loopback host {host!r} rejected"


async def test_host_header_rebinding_attempt_is_rejected() -> None:
    """A request carrying a non-loopback ``Host`` is the DNS-rebinding
    signature: the attacker's domain initially resolved to their
    server, then the TTL expired and the second resolution returned
    ``127.0.0.1``. The browser still sends the original hostname in
    the header. Rejecting that with 403 keeps the garden DB
    (which holds prompts, tool inputs, project paths) and the
    ``DELETE`` endpoint unreachable from a rogue page.
    """
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        for evil_host in (
            "attacker.example.com",
            "attacker.example.com:8080",
            "192.0.2.1",
            "evil.localhost.attacker.com",
        ):
            for path in ("/", "/api/garden", "/events"):
                resp = await client.get(path, headers={"Host": evil_host})
                assert resp.status == 403, (
                    f"non-loopback host {evil_host!r} on {path} "
                    f"unexpectedly returned {resp.status}"
                )


# ---------------------------------------------------------------------------
# SSE plumbing
# ---------------------------------------------------------------------------


async def _read_one_sse_event(resp: Any) -> tuple[str, dict[str, Any]]:
    """Pull one ``event: <name>\\ndata: <json>\\n\\n`` block.

    Skips comment-only lines (``: idle`` heartbeats injected by the
    server when no real event has arrived).
    """
    name: str | None = None
    data_lines: list[str] = []
    async for raw in resp.content:
        line = raw.decode("utf-8").rstrip("\n").rstrip("\r")
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
        elif line == "" and name is not None:
            payload = json.loads("".join(data_lines)) if data_lines else {}
            return name, payload
    msg = "stream closed before a complete event arrived"
    raise AssertionError(msg)


async def test_sse_delivers_snapshot_immediately_after_set() -> None:
    """Set the state, then subscribe, then expect to receive the
    seeded state on first read — the late-join contract. The first
    frame is always ``event: snapshot`` so the client can flip out
    of "connecting…" unconditionally."""
    b = WebBroadcaster(banner="hello")
    b.set_state(demo_tree("sse-1"), last_event_name="SessionStart")

    async with TestClient(TestServer(build_app(b))) as client, client.get("/events") as resp:
        assert resp.status == 200
        assert "text/event-stream" in resp.headers["Content-Type"]
        name, payload = await asyncio.wait_for(
            _read_one_sse_event(resp), timeout=2.0
        )
    assert name == "snapshot"
    assert payload["state"]["session_id"] == "sse-1"
    assert payload["event_name"] == "SessionStart"
    assert payload["banner"] == "hello"
    assert payload["idle"] is False


async def test_sse_idle_daemon_sends_snapshot_within_one_second() -> None:
    """Regression for the overnight ship: a fresh daemon with **no
    state set yet** must still send a frame on /events within ~1s.

    Previously the SSE endpoint returned 200 OK + 0 bytes when the
    broadcaster had never seen a Claude Code event, leaving the
    browser stuck on "connecting…" indefinitely."""
    b = WebBroadcaster(project_root="/tmp/proj")
    async with TestClient(TestServer(build_app(b))) as client, client.get("/events") as resp:
        assert resp.status == 200
        name, payload = await asyncio.wait_for(
            _read_one_sse_event(resp), timeout=1.0
        )
    assert name == "snapshot"
    assert payload["state"] is None
    assert payload["idle"] is True
    assert payload["project_root"] == "/tmp/proj"
    # The placeholder SVG (with seedling + pot + waiting text) is
    # part of the snapshot payload — large enough to clear the
    # browser SSE buffer threshold.
    assert isinstance(payload["svg"], str)
    assert payload["svg"].startswith("<svg")


async def test_sse_response_prelude_clears_browser_buffer() -> None:
    """The SSE response opens with a >2 KB comment block so the
    browser dispatches the snapshot frame immediately instead of
    sitting on it until enough bytes accumulate. The "connecting…"
    stuck-banner bug was caused by that buffering threshold; the
    prelude is the fix."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client, client.get("/events") as resp:
        # Read the first chunk: should start with ":" (an SSE
        # comment line) and be ≥2 KB before the snapshot arrives.
        first_chunk = await resp.content.readany()
    assert first_chunk[:1] == b":", (
        f"SSE stream must open with a comment block; got {first_chunk[:40]!r}"
    )
    assert len(first_chunk) >= 2000, (
        f"prelude must be ≥2 KB to defeat browser buffering; got {len(first_chunk)} bytes"
    )


async def test_sse_pushes_to_open_clients_on_set_state() -> None:
    """A subscriber that connected BEFORE the first state event
    must still receive that event when set_state fires."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client, client.get("/events") as resp:
        assert resp.status == 200
        # Give aiohttp a tick to register the subscriber.
        for _ in range(20):
            await asyncio.sleep(0.01)
            if b.subscriber_count >= 1:
                break
        assert b.subscriber_count == 1
        # Drain the idle snapshot that every new subscriber receives.
        first_name, first_payload = await asyncio.wait_for(
            _read_one_sse_event(resp), timeout=1.0
        )
        assert first_name == "snapshot"
        assert first_payload["idle"] is True
        b.set_state(demo_tree("sse-2"), last_event_name="PostToolUse")
        name, payload = await asyncio.wait_for(
            _read_one_sse_event(resp), timeout=2.0
        )
    assert name == "state"
    assert payload["state"]["session_id"] == "sse-2"
    assert payload["event_name"] == "PostToolUse"


async def test_sse_unsubscribes_on_client_disconnect() -> None:
    """The broadcaster's subscriber list must shrink when a tab
    closes — otherwise memory grows unbounded over a long session."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        async with client.get("/events") as resp:
            for _ in range(20):
                await asyncio.sleep(0.01)
                if b.subscriber_count >= 1:
                    break
            assert b.subscriber_count == 1
            resp.close()
        # Give aiohttp a beat to run the finally clause.
        for _ in range(20):
            await asyncio.sleep(0.01)
            if b.subscriber_count == 0:
                break
    assert b.subscriber_count == 0


async def test_runner_to_broadcaster_to_sse_end_to_end(
    bonsai_home: Path,
) -> None:
    """The contract the brief is built on: a hook event published
    onto the bus → growth runner applies → broadcaster pushes →
    SSE client receives the new state."""
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journals = JournalRegistry(cfg_journals)
    garden = GardenStore()
    sid = "e2e-sse-001"

    b = WebBroadcaster()
    bus = reset_event_bus_for_tests()
    runner = GrowthRunner(
        b,  # type: ignore[arg-type]  # duck-typed set_state target
        bus,
        garden=garden,
        journals=journals,
        partial_save_every_n=9999,
        idle_timeout_s=99999.0,
        timer_tick_s=99999.0,
    )
    await runner.start()

    try:
        async with TestClient(TestServer(build_app(b, garden=garden))) as client:
            async with client.get("/events") as resp:
                # Wait for the subscriber to register.
                for _ in range(20):
                    await asyncio.sleep(0.01)
                    if b.subscriber_count >= 1:
                        break
                # Drain the idle snapshot every subscriber gets first.
                snap_name, snap_payload = await asyncio.wait_for(
                    _read_one_sse_event(resp), timeout=2.0
                )
                assert snap_name == "snapshot"
                assert snap_payload["idle"] is True
                # Push the first event onto the bus.
                ev = parse_event(
                    {
                        "session_id": sid,
                        "hook_event_name": "SessionStart",
                        "cwd": str(bonsai_home),
                    }
                )
                await bus.publish(IngestedEvent(idx=0, event=ev))
                name, payload = await asyncio.wait_for(
                    _read_one_sse_event(resp), timeout=3.0
                )
            assert name == "state"
            assert payload["state"]["session_id"] == sid
            assert payload["event_name"] == "SessionStart"
            assert payload["state"]["event_count"] == 1
    finally:
        await runner.stop()
        garden.close()


# ---------------------------------------------------------------------------
# Garden API
# ---------------------------------------------------------------------------


async def test_api_garden_returns_rows_in_newest_first_order(
    bonsai_home: Path,
) -> None:
    store = GardenStore()
    for sid, started in [("old", 100), ("mid", 200), ("new", 300)]:
        store.save_session(
            demo_tree(sid),
            project_path="/p",
            event_log_path=bonsai_home / "journals" / f"{sid}.jsonl",
            started_at_ms=started,
            ended_at_ms=started + 1,
        )
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.get("/api/garden")
        assert resp.status == 200
        rows = await resp.json()
    store.close()
    assert [r["id"] for r in rows] == ["new", "mid", "old"]
    # Required fields for the client renderer.
    for r in rows:
        for key in ("id", "started_at", "theme", "project_path", "status"):
            assert key in r


async def test_api_session_404_for_missing_id(bonsai_home: Path) -> None:
    store = GardenStore()
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.get("/api/session/does-not-exist")
        assert resp.status == 404
    store.close()


async def test_api_garden_stats_aggregates(bonsai_home: Path) -> None:
    """The new ``/api/garden/stats`` endpoint must return the four
    hero-band fields. Real values are tested in detail under
    ``test_garden_stats``; here we pin the wire shape and a
    minimal end-to-end."""
    from datetime import datetime as _dt

    store = GardenStore()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    started = int(_dt(2026, 5, 14, 12, 0).timestamp() * 1000)
    ended = started + 600_000  # +10 minutes
    store.save_session(
        demo_tree("stats-001"),
        project_path="/p",
        event_log_path=cfg_journals / "stats-001.jsonl",
        started_at_ms=started,
        ended_at_ms=ended,
    )
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.get("/api/garden/stats")
        assert resp.status == 200
        data = await resp.json()
    store.close()
    for key in ("total_seconds", "sessions_count", "sessions_this_month", "streak_days"):
        assert key in data, f"missing stats field: {key}"
    assert data["sessions_count"] == 1
    assert data["total_seconds"] >= 600  # at least the 10-min session


async def test_api_garden_stats_returns_zeros_with_no_store() -> None:
    """If the broadcaster is mounted without a garden store, the
    stats endpoint should return all zeros rather than crashing —
    matches the existing ``/api/garden`` contract."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/api/garden/stats")
        assert resp.status == 200
        data = await resp.json()
    assert data == {
        "total_seconds": 0,
        "sessions_count": 0,
        "sessions_this_month": 0,
        "streak_days": 0,
    }


async def test_index_html_includes_idle_hero() -> None:
    """Pin the idle-hero markup + the JS loader.

    The idle hero replaces the older thin garden-hero band: when
    there's no live Claude Code session, the same three stats
    (total time / sessions / streak) fill the hero zone as large
    centered numbers, with a one-line tagline below. CSS swaps
    `#idle-hero` and `#live-area` based on body.live so the same
    vertical space serves both states.
    """
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    assert 'id="idle-hero"' in body
    assert 'id="hs-total-value"' in body
    assert 'id="hs-sessions-value"' in body
    assert 'id="hs-streak-value"' in body
    assert "loadHeroStats" in body
    assert "/api/garden/stats" in body
    # The wordmark tagline lives in the idle hero — pin it so a
    # future refactor that strips the copy fails loudly.
    assert "Plant a tree in any project" in body
    # The old thin band is gone — make sure it doesn't sneak back.
    assert 'id="garden-hero"' not in body


async def test_index_html_refreshes_garden_on_session_end_transition() -> None:
    """The client must reload /api/garden the moment SSE reports
    that the live session ended (live_session_id non-null →
    null). Without this trigger users wait up to 30s for the next
    poll to see the just-finished session land in the grid —
    reads as broken after typing ``/exit``."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    # The transition guard pins the trigger; loadGarden is the
    # action. A regression that removes one or the other would
    # leave the grid stale until the next 30s poll.
    assert "lastLiveSessionId" in body
    assert "live_session_id" in body
    # The actual fire site: non-null → null implies loadGarden.
    assert "if (lastLiveSessionId && !incomingLive" in body


async def test_api_replay_honours_theme_override(bonsai_home: Path) -> None:
    """``/api/replay/<id>?theme=sakura`` must render the replay
    state via the sakura renderer regardless of the session's
    auto-detected language. Pin the contract so the URL-share path
    (open ``/replay/<id>?theme=sakura`` in a browser → see sakura)
    can't quietly regress."""
    from bonsai_cc.web.render import tokens as _t

    store = GardenStore()
    sid = "replay-theme-001"
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journal_path = cfg_journals / f"{sid}.jsonl"
    records = [
        {"ts": 1, "idx": 0, "raw": {
            "session_id": sid,
            "hook_event_name": "SessionStart",
            "cwd": str(bonsai_home),
        }},
        {"ts": 2, "idx": 1, "raw": {
            "session_id": sid,
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(bonsai_home / "f.py"),
                "content": "y" * 30,
            },
            "cwd": str(bonsai_home),
        }},
    ]
    journal_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    store.save_session(
        demo_tree(sid),
        project_path=str(bonsai_home),
        event_log_path=journal_path,
        detected_language="python",
    )

    b = WebBroadcaster()
    async with (
        TestClient(TestServer(build_app(b, garden=store))) as client,
        client.get(f"/api/replay/{sid}?speed=0&theme=sakura") as resp,
    ):
        # First state frame is enough — the override applies to
        # every frame in the stream.
        name, payload = await asyncio.wait_for(
            _read_one_sse_event(resp), timeout=2.0
        )
    store.close()
    assert name == "state"
    assert _t.SAKURA_DEEP in payload["svg"], (
        "?theme=sakura on a python session must render with "
        f"sakura colors (SAKURA_DEEP={_t.SAKURA_DEEP} expected); "
        f"got SVG without it"
    )
    # And the dual-light/dark payload must also carry the override.
    assert _t.SAKURA_DEEP in payload["svg_dark"]
    assert _t.SAKURA_DEEP in payload["svg_light"]


async def test_index_html_includes_theme_picker() -> None:
    """The 12-theme override picker is part of the page bundle.
    Pin its markup hooks so a refactor that drops the picker or
    one of its themes fails loudly."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    assert 'id="theme-picker"' in body
    assert 'class="theme-btn auto-btn"' in body
    # All 12 themes + auto must appear as data-theme buttons.
    for theme in (
        "auto", "generic", "bamboo", "pine", "oak", "willow",
        "willow_ts", "sakura", "maple", "old_oak", "banyan",
        "ginkgo", "birch",
    ):
        assert f'data-theme="{theme}"' in body, (
            f"theme picker missing button for {theme!r}"
        )
    # Picker JS: URL/localStorage persistence + reconnect path.
    assert "function pickTheme" in body
    assert "bcc-default-theme" in body  # localStorage key
    assert 'searchParams.set("theme"' in body  # URL persistence
    # "make default" button wired up.
    assert 'id="theme-make-default"' in body
    # Picker hides in idle hero — only visible when a tree is on
    # screen (body.live covers active live AND /replay/<id>).
    # Pin both halves of the CSS rule so a regression that flips
    # the default back to ``display: flex`` fails loudly.
    assert "#theme-picker {\n    display: none;" in body
    assert "body.live #theme-picker { display: flex; }" in body


async def test_api_garden_excludes_currently_live_session(bonsai_home: Path) -> None:
    """The live session is owned by the hero, not the grid.

    Without this filter, the runner's periodic partial save (every
    N events) wrote the live session to the garden DB, the page's
    30s ``loadGarden`` poll picked it up, and the client's hero-vs-
    grid dedup gate (``gardenSessionIds.has(p.state.session_id)``)
    fired on the next SSE state event — the live tree disappeared
    mid-session and never came back. Server filter is the
    authoritative fix.
    """
    store = GardenStore()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    for sid in ("done-1", "live-1"):
        store.save_session(
            demo_tree(sid),
            project_path=f"/p/{sid}",
            event_log_path=cfg_journals / f"{sid}.jsonl",
        )

    class _StubLiveRunner:
        # Duck-typed runner — the only thing the handler reads.
        current_live_session_id = "live-1"

    b = WebBroadcaster()
    app = build_app(
        b,
        garden=store,
        runner=_StubLiveRunner(),  # type: ignore[arg-type]
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/garden")
        rows = await resp.json()
    store.close()
    assert {r["id"] for r in rows} == {"done-1"}, (
        "live session must be filtered out so the client doesn't "
        "dedup the hero against its own SSE state"
    )


async def test_api_garden_includes_session_after_runner_finalises(
    bonsai_home: Path,
) -> None:
    """Once the runner's session is finalised, it reappears in the grid.

    A finalised session (SessionEnd processed) is no longer "live"
    — the hero stops painting it, and the grid is its rightful home.
    The runner reports ``current_live_session_id is None`` in that
    state.
    """
    store = GardenStore()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    for sid in ("done-1", "just-ended"):
        store.save_session(
            demo_tree(sid),
            project_path=f"/p/{sid}",
            event_log_path=cfg_journals / f"{sid}.jsonl",
        )

    class _StubIdleRunner:
        current_live_session_id = None

    b = WebBroadcaster()
    app = build_app(
        b,
        garden=store,
        runner=_StubIdleRunner(),  # type: ignore[arg-type]
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/garden")
        rows = await resp.json()
    store.close()
    assert {r["id"] for r in rows} == {"done-1", "just-ended"}


async def test_api_garden_filters_by_language(bonsai_home: Path) -> None:
    """The /api/garden ``language`` query param must filter by
    ``detected_language``."""
    store = GardenStore()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    for sid, lang in (("a", "python"), ("b", "rust"), ("c", "python")):
        store.save_session(
            demo_tree(sid),
            project_path=f"/p/{sid}",
            event_log_path=cfg_journals / f"{sid}.jsonl",
            detected_language=lang,
        )
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.get("/api/garden?language=python")
        assert resp.status == 200
        rows = await resp.json()
    store.close()
    assert {r["id"] for r in rows} == {"a", "c"}


async def test_api_garden_search_q_matches_id_or_project(bonsai_home: Path) -> None:
    """The ``q`` param is a case-insensitive substring search across
    session id and project_path."""
    store = GardenStore()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    for sid, proj in (
        ("abc-001", "/work/foo"),
        ("xyz-002", "/work/foo/sub"),
        ("def-003", "/home/me/bar"),
    ):
        store.save_session(
            demo_tree(sid),
            project_path=proj,
            event_log_path=cfg_journals / f"{sid}.jsonl",
        )
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.get("/api/garden?q=FOO")
        rows = await resp.json()
    store.close()
    assert {r["id"] for r in rows} == {"abc-001", "xyz-002"}


async def test_api_session_svg_returns_image(bonsai_home: Path) -> None:
    """A saved session's final SVG is served on demand as image/svg+xml."""
    store = GardenStore()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    sid = "svg-render-001"
    store.save_session(
        demo_tree(sid),
        project_path="/p",
        event_log_path=cfg_journals / f"{sid}.jsonl",
    )
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.get(f"/api/session/{sid}/svg")
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("image/svg+xml")
        # Phase 11 commit 5: thumbnails carry an aggressive cache
        # header since they never change after save.
        assert "max-age" in resp.headers.get("Cache-Control", "")
        body = await resp.text()
    store.close()
    assert body.startswith("<svg")
    assert body.endswith("</svg>")


async def test_api_session_svg_uses_cached_thumbnail(bonsai_home: Path) -> None:
    """The endpoint must read ``thumbnail_svg`` from the DB rather
    than re-rendering. We force a sentinel value into the cache to
    prove the cache path is taken, not the live render."""
    store = GardenStore()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    sid = "cached-svg-001"
    store.save_session(
        demo_tree(sid),
        project_path="/p",
        event_log_path=cfg_journals / f"{sid}.jsonl",
    )
    sentinel = "<svg id='cached-sentinel'>cached</svg>"
    store.set_thumbnail(sid, sentinel)
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.get(f"/api/session/{sid}/svg")
        body = await resp.text()
    store.close()
    assert body == sentinel


async def test_api_session_delete_removes_row(bonsai_home: Path) -> None:
    """``DELETE /api/session/<id>`` removes the row and returns 204."""
    store = GardenStore()
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    sid = "delete-me-001"
    store.save_session(
        demo_tree(sid),
        project_path="/p",
        event_log_path=cfg_journals / f"{sid}.jsonl",
    )
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.delete(f"/api/session/{sid}")
        assert resp.status == 204
        # Second delete is 404.
        resp2 = await client.delete(f"/api/session/{sid}")
        assert resp2.status == 404
    store.close()


async def test_index_html_includes_garden_grid_markup() -> None:
    """The /  page must include the garden section markup and the
    JS loader. The actual rendering happens client-side; we just pin
    the structural skeleton so a regression that drops the section
    fails loudly."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    assert 'id="garden"' in body
    assert 'id="grid"' in body
    assert 'id="empty-garden"' in body
    assert "/api/garden" in body  # the fetch URL
    assert "loadGarden" in body  # the loader function


async def test_api_replay_streams_state_per_event(bonsai_home: Path) -> None:
    """Server-side replay walks the journal and pushes a state
    event per record. Speed override drops the delay to zero so the
    test isn't time-bound."""
    store = GardenStore()
    sid = "replay-001"
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journal_path = cfg_journals / f"{sid}.jsonl"
    # Three records: SessionStart + two Writes.
    records = [
        {"ts": 1, "idx": 0, "raw": {
            "session_id": sid, "hook_event_name": "SessionStart",
            "cwd": str(bonsai_home),
        }},
        {"ts": 2, "idx": 1, "raw": {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(bonsai_home / "a.py"), "content": "x"},
            "cwd": str(bonsai_home),
        }},
        {"ts": 3, "idx": 2, "raw": {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(bonsai_home / "b.py"), "content": "y"},
            "cwd": str(bonsai_home),
        }},
    ]
    journal_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    store.save_session(
        demo_tree(sid),
        project_path=str(bonsai_home),
        event_log_path=journal_path,
    )

    b = WebBroadcaster()
    async with (
        TestClient(TestServer(build_app(b, garden=store))) as client,
        client.get(f"/api/replay/{sid}?speed=0") as resp,
    ):
        assert resp.status == 200
        received: list[tuple[str, dict[str, Any]]] = []
        for _ in range(4):  # 3 states + 1 replay_done
            name, payload = await asyncio.wait_for(
                _read_one_sse_event(resp), timeout=3.0
            )
            received.append((name, payload))
            if name == "replay_done":
                break
    store.close()

    state_events = [(n, p) for n, p in received if n == "state"]
    assert len(state_events) == 3
    # event_count grows monotonically — apply_event is doing its work.
    counts = [p["state"]["event_count"] for _n, p in state_events]
    assert counts == [1, 2, 3]
    # Replay envelope marks itself.
    assert all(p["replay"] is True for _n, p in state_events)
    # Banner names the session prefix and event number.
    assert state_events[0][1]["banner"].startswith("replay of replay-0")


async def test_api_replay_carries_progress_fields(bonsai_home: Path) -> None:
    """Phase 11 commit 6: each replay state event must carry
    ``replay_idx`` and ``replay_total`` so the client can render a
    progress bar."""
    store = GardenStore()
    sid = "progress-001"
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journal_path = cfg_journals / f"{sid}.jsonl"
    records = [
        {"ts": 1, "idx": i, "raw": {
            "session_id": sid, "hook_event_name": "SessionStart",
            "cwd": str(bonsai_home),
        }} if i == 0 else {"ts": 2, "idx": i, "raw": {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": f"/x/{i}.py", "content": "y"},
            "cwd": str(bonsai_home),
        }} for i in range(4)
    ]
    journal_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    store.save_session(
        demo_tree(sid),
        project_path=str(bonsai_home),
        event_log_path=journal_path,
    )
    b = WebBroadcaster()
    async with (
        TestClient(TestServer(build_app(b, garden=store))) as client,
        client.get(f"/api/replay/{sid}?speed=0") as resp,
    ):
        name, payload = await asyncio.wait_for(
            _read_one_sse_event(resp), timeout=2.0
        )
    store.close()
    assert name == "state"
    assert payload["replay_idx"] == 0
    assert payload["replay_total"] == 4


async def test_index_html_emits_unconditional_debug_logs() -> None:
    """When the replay-blank-canvas bug shipped, ``?debug=1``
    produced zero console output — the gated logs were useless
    because the gate itself silently resolved wrong. Pin the page
    so an *unconditional* page-load diagnostic AND an unconditional
    first-frame log are always emitted.

    The point: even if a future refactor breaks ``debugMode``
    parsing, the user can still see "[bcc-debug] page loaded" in
    DevTools and report the actual ``debugMode`` value back. No
    more flying blind."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    # debugMode is parsed via URLSearchParams from location.search.
    assert "URLSearchParams" in body
    assert "urlParams.get(\"debug\")" in body
    # The unconditional "page loaded" log must NOT be inside the
    # ``if (debugMode)`` guard — it has to fire regardless so we can
    # tell whether the script ran at all.
    page_loaded_idx = body.index('[bcc-debug] page loaded')
    # Walk backwards: there should be no ``if (debugMode)`` between
    # the const declaration and the page-loaded log.
    preceding_500 = body[max(0, page_loaded_idx - 500):page_loaded_idx]
    assert "if (debugMode)" not in preceding_500, (
        "page-load log appears to be inside ``if (debugMode)`` guard — "
        "it must be unconditional"
    )
    # First-frame log mechanism: a one-shot flag that fires
    # regardless of debugMode.
    assert "_firstFrameLogged" in body
    assert "[bcc-debug] first SSE frame received" in body


async def test_index_html_uses_domparser_not_innerhtml_for_svg() -> None:
    """SVG insertion via ``innerHTML`` is a documented trap because
    of the HTML parser's foreign-content mode (see DESIGN.md
    "Known gotchas"). Pin the page so a regression doesn't
    silently restore the brittle pattern."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    # The robust path.
    assert "function paintSvg" in body
    assert "DOMParser" in body
    assert 'parseFromString' in body
    assert "image/svg+xml" in body
    # The fragile pattern must NOT be present on the SVG payload path.
    # (Other unrelated ``innerHTML`` uses are fine — they don't
    # touch ``<svg>`` content.)
    assert "wrap.innerHTML = p.svg" not in body
    # ``?debug=1`` mode and visible error surfacing are wired up.
    assert 'debugMode' in body
    assert "surfaceRenderError" in body


async def test_api_replay_payload_carries_tree_svg(bonsai_home: Path) -> None:
    """The replay endpoint must include a server-rendered ``svg``
    field in every state frame — same shape as the live broadcaster
    payload. Without it the /replay/<id> page paints an empty
    canvas (the bug v0.2.0 RC shipped with). Pin the contract here
    so a future refactor that drops the field fails loudly.

    Parses the first state frame and asserts the SVG contains real
    tree geometry: the pot gradient, at least one filled trunk path,
    and the foliage ellipse clusters the per-theme renderer emits.
    """
    store = GardenStore()
    sid = "replay-svg-001"
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journal_path = cfg_journals / f"{sid}.jsonl"
    # SessionStart + a few writes so apply_event produces a trunk +
    # at least one branch + a foliage cluster.
    records = [
        {"ts": i + 1, "idx": i, "raw": raw}
        for i, raw in enumerate([
            {
                "session_id": sid,
                "hook_event_name": "SessionStart",
                "cwd": str(bonsai_home),
            },
            *[
                {
                    "session_id": sid,
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Write",
                    "tool_input": {
                        "file_path": str(bonsai_home / f"f{n}.py"),
                        "content": "y" * (10 + n * 3),
                    },
                    "cwd": str(bonsai_home),
                }
                for n in range(4)
            ],
        ])
    ]
    journal_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    store.save_session(
        demo_tree(sid),
        project_path=str(bonsai_home),
        event_log_path=journal_path,
    )

    b = WebBroadcaster()
    async with (
        TestClient(TestServer(build_app(b, garden=store))) as client,
        client.get(f"/api/replay/{sid}?speed=0") as resp,
    ):
        # Pull frames until we get one with branches in the state
        # (the trunk-only first frame is too sparse for the
        # foliage-cluster assertion).
        last_svg = ""
        for _ in range(6):
            name, payload = await asyncio.wait_for(
                _read_one_sse_event(resp), timeout=2.0
            )
            if name == "replay_done":
                break
            assert name == "state"
            # The svg field is the load-bearing one.
            assert isinstance(payload["svg"], str), (
                "replay state payload must include an ``svg`` field — "
                "without it the page paints an empty canvas"
            )
            last_svg = payload["svg"]
            if "<ellipse" in last_svg:
                break
    store.close()
    # Tree elements: a complete SVG envelope, pot markup, trunk
    # path, and foliage ellipse clusters.
    assert last_svg.startswith("<svg")
    assert last_svg.endswith("</svg>")
    assert 'id="bcc-pot"' in last_svg, "pot gradient missing"
    assert "<path" in last_svg, "no path elements (trunk + branches)"
    assert "<ellipse" in last_svg, "no ellipse elements (foliage clusters)"


async def test_api_replay_payload_carries_tool_counts(bonsai_home: Path) -> None:
    """The replay payload must also carry ``tool_counts`` so the
    sidebar updates during playback, not just the live view."""
    store = GardenStore()
    sid = "replay-tools-001"
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journal_path = cfg_journals / f"{sid}.jsonl"
    records = [
        {"ts": 1, "idx": 0, "raw": {
            "session_id": sid, "hook_event_name": "SessionStart",
            "cwd": str(bonsai_home),
        }},
        {"ts": 2, "idx": 1, "raw": {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "cwd": str(bonsai_home),
        }},
        {"ts": 3, "idx": 2, "raw": {
            "session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
            "cwd": str(bonsai_home),
        }},
    ]
    journal_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    store.save_session(
        demo_tree(sid),
        project_path=str(bonsai_home),
        event_log_path=journal_path,
    )
    b = WebBroadcaster()
    payloads = []
    async with (
        TestClient(TestServer(build_app(b, garden=store))) as client,
        client.get(f"/api/replay/{sid}?speed=0") as resp,
    ):
        for _ in range(4):
            name, payload = await asyncio.wait_for(
                _read_one_sse_event(resp), timeout=2.0
            )
            if name == "replay_done":
                break
            payloads.append(payload)
    store.close()
    # Final frame's tool_counts must reflect both Bash calls.
    assert payloads[-1]["tool_counts"] == {"Bash": 2}
    # Idle must be False so the body.live class kicks in.
    assert payloads[-1]["idle"] is False


async def test_api_replay_supports_from_resume(bonsai_home: Path) -> None:
    """``?from=N`` lets the client resume after a pause. Events
    before N are silently absorbed (so state matches a full play to
    N); events from N onward are emitted."""
    store = GardenStore()
    sid = "resume-001"
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journal_path = cfg_journals / f"{sid}.jsonl"
    records = [
        {"ts": i + 1, "idx": i, "raw": {
            "session_id": sid,
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": f"/x/{i}.py", "content": "y"},
            "cwd": str(bonsai_home),
        }} for i in range(6)
    ]
    journal_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    store.save_session(
        demo_tree(sid),
        project_path=str(bonsai_home),
        event_log_path=journal_path,
    )
    b = WebBroadcaster()
    async with (
        TestClient(TestServer(build_app(b, garden=store))) as client,
        client.get(f"/api/replay/{sid}?speed=0&from=3") as resp,
    ):
        first_name, first_payload = await asyncio.wait_for(
            _read_one_sse_event(resp), timeout=2.0
        )
    store.close()
    # First emitted record after resume must be idx 3 (not 0).
    assert first_name == "state"
    assert first_payload["replay_idx"] == 3
    # State carries the accumulated effect of events 0-3, not just 3.
    assert first_payload["state"]["event_count"] == 4


async def test_index_html_includes_tool_stats_sidebar() -> None:
    """The page must include the tool-stats sidebar markup, the
    Tabler icon sprite (self-hosted — no CDN), and the JS that
    consumes ``tool_counts`` from the SSE payload."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    # Sidebar shell.
    assert 'id="tool-stats"' in body
    assert "renderToolStats" in body
    # All ten tool icons + the alert-triangle for errors.
    for sym in (
        "ti-terminal-2", "ti-edit", "ti-file-plus", "ti-file-text",
        "ti-asterisk", "ti-search", "ti-world-www", "ti-network",
        "ti-notebook", "ti-tool", "ti-alert-triangle",
    ):
        assert f'id="{sym}"' in body, f"missing icon sprite: {sym}"
    # No CDN references — icons are bundled inline.
    assert "tabler.io" not in body
    assert "unpkg" not in body
    assert "cdnjs" not in body


async def test_index_html_has_live_vs_idle_visual_prominence() -> None:
    """The page layout shifts when a session is active vs idle.
    Pin the CSS class hooks (``body.live``) and the live-pulse
    strip so a regression that drops the prominence behaviour
    fails. The current design swaps two siblings (``#idle-hero``
    and ``#live-area``) on body.live rather than resizing one;
    the test pins the swap rule directly so a future refactor can
    change the mechanism but still get caught if the prominence
    behaviour disappears."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    assert "body.live #live-area" in body
    assert "body.live #idle-hero" in body
    assert 'id="live-pulse"' in body
    assert "classList.toggle(\"idle\"" in body
    assert "classList.toggle(\"live\"" in body


async def test_index_html_includes_replay_controls() -> None:
    """The page bundle must include the replay control surface
    (toggle / speed selector / progress / share). Lifts a regression
    that drops the playback UI."""
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b))) as client:
        resp = await client.get("/")
        body = await resp.text()
    assert 'id="replay-controls"' in body
    assert 'id="rc-toggle"' in body
    assert 'id="rc-speed"' in body
    assert 'id="rc-bar"' in body
    assert "togglePlayback" in body


async def test_api_replay_404_for_missing_session(bonsai_home: Path) -> None:
    store = GardenStore()
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.get("/api/replay/never-existed")
        assert resp.status == 404
    store.close()


async def test_api_session_events_returns_journal(bonsai_home: Path) -> None:
    """Replay mode pulls the journal via this endpoint."""
    store = GardenStore()
    sid = "events-001"
    cfg_journals = bonsai_home / "journals"
    cfg_journals.mkdir(parents=True, exist_ok=True)
    journal_path = cfg_journals / f"{sid}.jsonl"
    journal_path.write_text(
        json.dumps({
            "ts": 1, "idx": 0, "raw": {
                "session_id": sid, "hook_event_name": "SessionStart",
                "cwd": str(bonsai_home),
            },
        }) + "\n",
        encoding="utf-8",
    )
    store.save_session(
        demo_tree(sid),
        project_path=str(bonsai_home),
        event_log_path=journal_path,
    )
    b = WebBroadcaster()
    async with TestClient(TestServer(build_app(b, garden=store))) as client:
        resp = await client.get(f"/api/session/{sid}/events")
        assert resp.status == 200
        records = await resp.json()
    store.close()
    assert len(records) == 1
    assert records[0]["raw"]["hook_event_name"] == "SessionStart"


# Silence the unused-import warning if pytest plugins are dropped.
_ = pytest

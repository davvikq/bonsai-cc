"""``run_web_pipeline`` -- the top-level entry point.

Brings up everything needed for the web renderer:

* IPC server listening for Claude Code hook events (existing).
* Growth runner consuming the bus and pushing state into a
  :class:`WebBroadcaster`.
* aiohttp HTTP server on a random loopback port.
* Optional browser auto-open via :mod:`webbrowser`.

Stops on ``q`` typed at stdin or SIGINT. Performs orphan-journal
recovery before mounting the runner.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import webbrowser
from pathlib import Path

from aiohttp import web

from bonsai_cc.config import Config, get_config
from bonsai_cc.events.bus import get_event_bus
from bonsai_cc.events.journal import JournalRegistry
from bonsai_cc.events.watcher import JournalWatcher
from bonsai_cc.garden.store import GardenStore
from bonsai_cc.log import get_logger
from bonsai_cc.runner import (
    GrowthRunner,
    recover_orphan_sessions,
    replay_journal_into_bus,
)
from bonsai_cc.web.broadcaster import WebBroadcaster
from bonsai_cc.web.server import PORT_FILE_NAME, build_app, write_port_file

__all__ = ["run_web_pipeline"]


_log = get_logger("bonsai_cc.web.pipeline")


async def run_web_pipeline(
    *,
    config: Config | None = None,
    theme: str = "default",
    banner: str | None = None,
    port: int = 0,
    open_browser: bool = True,
    browser_path: str = "/",
    replay_path: Path | None = None,
    replay_speed: float = 0.0,
) -> None:
    """Run the web renderer until the user types ``q`` or Ctrl-C.

    ``port=0`` (default) asks the OS for a random free port; pass
    a specific number to bind there. ``open_browser=False`` skips
    the auto-open, useful on SSH boxes.

    ``replay_path`` lets a recorded JSONL journal drive the same
    pipeline a live Claude Code hook would. Used by
    ``bonsai-cc watch --replay <file>`` for smoke-testing without
    starting a real session. ``replay_speed=0`` (default) fires
    every record as fast as possible; ``replay_speed=1.0`` mimics
    the original wall-clock cadence.
    """
    cfg = config or get_config()
    cfg.ensure_dirs()

    # --- Event production + growth pipeline ---
    # Events arrive via the journal watcher; the hook writes journal
    # files directly with no IPC layer in between.
    bus = get_event_bus()
    journals = JournalRegistry(cfg.journals_dir)

    garden = GardenStore(config=cfg)
    # One-shot migration: pre-fix versions of `install-hook` wrote
    # a smoke marker into journals/ where the watcher treated it
    # as a real session. Sweep it up on every daemon start so users
    # who don't re-run install-hook still benefit.
    from bonsai_cc.cli import cleanup_legacy_smoke_artifacts
    cleanup_legacy_smoke_artifacts()
    recovered = recover_orphan_sessions(garden, journals)
    if recovered:
        _log.info("web_pipeline_recovered_orphans", count=recovered)

    # Sessions a previous run already finalised don't need their journal
    # backlog replayed through the live runner on startup. The runner
    # would discard those events anyway (GrowthRunner skips completed
    # sessions when binding), but the watcher would still read + parse +
    # publish every event first -- with a large accumulated journals dir
    # that starves the single-threaded event loop long enough that the
    # HTTP server binds late and the browser sees ERR_CONNECTION_REFUSED.
    # ``recover_orphan_sessions`` above has already persisted every
    # on-disk journal, so this set is complete.
    from bonsai_cc.garden.store import SessionFilter, SessionStatus

    completed_session_ids = {
        row.id
        for row in garden.list_sessions(SessionFilter(limit=100_000))
        if row.status in (SessionStatus.COMPLETE, SessionStatus.RECOVERED)
    }
    watcher = JournalWatcher(
        cfg.journals_dir, bus, skip_session_ids=completed_session_ids
    )

    broadcaster = WebBroadcaster(
        banner=banner,
        project_root=str(Path.cwd()),
    )
    # An idle broadcaster still seeds an ``event: snapshot`` frame
    # to every new SSE subscriber, so the browser flips out of
    # "connecting…" immediately. The ``idle: true`` flag in the
    # payload tells the client to render an empty pot + "waiting"
    # banner until the first real Claude Code event lands.

    runner = GrowthRunner(
        broadcaster,
        bus,
        theme=theme,
        garden=garden,
        journals=journals,
    )
    # Late-attach so payload's `live_session_id` field mirrors the
    # runner's actual binding. The runner couldn't be passed in
    # WebBroadcaster.__init__ because the runner construction
    # requires the broadcaster.
    broadcaster.attach_runner(runner)
    await runner.start()

    # --- HTTP server: bind BEFORE wiring up event production ---
    # The journal watcher's startup catch-up scan (and the optional
    # replay task) can publish a large backlog onto the bus. Binding
    # the listening socket first means the browser connects instantly
    # and streams that backlog over SSE, rather than getting
    # ERR_CONNECTION_REFUSED while the single-threaded event loop is
    # still busy replaying history.
    app = build_app(
        broadcaster,
        garden=garden,
        journals_dir=cfg.journals_dir,
        runner=runner,
    )
    runner_app = web.AppRunner(app)
    await runner_app.setup()
    bound_port = port or _pick_free_port()
    site = web.TCPSite(runner_app, host="127.0.0.1", port=bound_port)
    await site.start()
    port_path = cfg.home / PORT_FILE_NAME
    write_port_file(port_path, bound_port)
    url = f"http://127.0.0.1:{bound_port}"
    print(  # noqa: T201
        f"bonsai-cc - {url} - Ctrl+C to stop",
        file=sys.stderr,
        flush=True,
    )
    _log.info("web_pipeline_serving", url=url, port=bound_port)

    if open_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(url + browser_path)

    # --- Event production: live watcher + optional replay ---
    watcher_task = asyncio.create_task(watcher.run(), name="bonsai-watcher")

    # Optional smoke-test path: feed a recorded journal through the
    # bus alongside the live watcher. Lets users prove the renderer
    # works ("bonsai-cc watch --replay tests/fixtures/.../...jsonl")
    # without starting a Claude Code session. The watcher keeps
    # listening for real events too, so this composes cleanly.
    replay_task: asyncio.Task[None] | None = None
    if replay_path is not None:
        replay_task = asyncio.create_task(
            replay_journal_into_bus(replay_path, bus, speed=replay_speed),
            name="bonsai-replay",
        )
        _log.info(
            "web_pipeline_replay_started",
            path=str(replay_path),
            speed=replay_speed,
        )

    # --- Wait for stdin 'q' or process termination ---
    stop_event = asyncio.Event()
    keyboard_task = asyncio.create_task(
        _wait_for_q(stop_event), name="bonsai-stdin-q"
    )
    try:
        await stop_event.wait()
    finally:
        keyboard_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keyboard_task
        await site.stop()
        await runner_app.cleanup()
        watcher.request_stop()
        watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher_task
        if replay_task is not None:
            replay_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await replay_task
        await runner.stop()
        garden.close()
        with contextlib.suppress(OSError):
            port_path.unlink(missing_ok=True)


def _pick_free_port() -> int:
    """Bind a TCP socket to port 0 just to learn the OS-assigned
    port, then release it. There's a one-second window where another
    process could steal the port, but on a sane single-user box
    that's not a concern."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


async def _wait_for_q(stop_event: asyncio.Event) -> None:
    """Block until the user types 'q' on stdin or stdin closes.

    Run in a thread because stdin reads are sync. The thread sets
    ``stop_event`` from the event loop via ``call_soon_threadsafe``.
    """
    loop = asyncio.get_running_loop()

    def _reader() -> None:
        try:
            for line in sys.stdin:
                if line.strip().lower() in {"q", "quit", "exit"}:
                    loop.call_soon_threadsafe(stop_event.set)
                    return
        except Exception:  # noqa: BLE001 - signal stop on stdin failure
            pass
        finally:
            loop.call_soon_threadsafe(stop_event.set)

    await asyncio.to_thread(_reader)

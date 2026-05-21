"""Tail-read the journals directory and forward new lines to the bus.

Uses ``watchfiles`` (inotify / FSEvents / ReadDirectoryChangesW).
Per-file byte offsets are tracked so a restart never re-publishes
old lines; the initial scan catches up from byte 0 of each file.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from watchfiles import Change, awatch

from bonsai_cc.events.bus import EventBus, IngestedEvent
from bonsai_cc.events.journal import _safe_session_filename
from bonsai_cc.events.models import parse_event
from bonsai_cc.log import get_logger

__all__ = ["JournalWatcher"]


_log = get_logger("bonsai_cc.events.watcher")

# Per the design contract -- defensive against malformed payloads. Same
# limit the old socket-based ingest used.
MAX_LINE_BYTES = 1 * 1024 * 1024


class JournalWatcher:
    """Watch ``journals_dir`` for new lines and publish them to the bus.

    Lifecycle:

    * :meth:`run` is an awaitable coroutine that blocks until the
      task is cancelled. Daemon pipelines spawn it as
      ``asyncio.create_task(watcher.run())``.
    * On entry, every existing journal is scanned from byte 0 so the
      daemon catches up on whatever the hook wrote while it was
      down.
    * After the initial scan, ``watchfiles.awatch`` drives further
      updates: a file modification (or create) triggers a re-scan
      from the last known offset to EOF.

    The watcher uses a per-file ``(byte_offset, line_idx)`` cache so
    the line counter that becomes ``IngestedEvent.idx`` matches what
    a fresh ``Journal.read()`` would produce -- the deterministic
    replay contract.
    """

    def __init__(
        self,
        journals_dir: Path,
        bus: EventBus,
        *,
        poll_min_interval_ms: int = 50,
        skip_session_ids: set[str] | None = None,
    ) -> None:
        self._journals_dir = journals_dir
        self._bus = bus
        # Per-session: how many bytes we've consumed AND the next
        # idx we'll publish (= number of valid records produced so
        # far). Both advance together.
        self._offsets: dict[Path, tuple[int, int]] = {}
        # Sanitised filename stems of sessions a previous daemon run
        # already finalised. The startup catch-up scan seeds these at
        # EOF instead of replaying their backlog: the runner would
        # discard the events anyway (it skips completed sessions when
        # binding), but reading + parsing + publishing a large backlog
        # first starves the event loop before the HTTP server binds.
        self._skip_stems: set[str] = {
            _safe_session_filename(sid) for sid in (skip_session_ids or set())
        }
        # ``watchfiles`` polling cadence -- milliseconds between
        # change-set yields when nothing is happening. The default
        # (50 ms) is plenty responsive for human-scale event rates.
        self._poll_min_interval_ms = poll_min_interval_ms
        # Set externally to stop the watcher's main loop.
        self._stop = asyncio.Event()

    @property
    def journals_dir(self) -> Path:
        return self._journals_dir

    async def run(self) -> None:
        """Block until cancelled, forwarding new journal lines to the bus."""
        self._journals_dir.mkdir(parents=True, exist_ok=True)
        # Catch up on whatever's already on disk. The bus has a single
        # consumer (the runner) which is started before us, so anything
        # we publish here is processed in order.
        await self._scan_all()
        # Live tail.
        _log.info("journal_watcher_started", path=str(self._journals_dir))
        try:
            async for changes in awatch(
                str(self._journals_dir),
                stop_event=self._stop,
                step=self._poll_min_interval_ms,
                recursive=False,
            ):
                await self._handle_changes(changes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("journal_watcher_crashed", error=str(exc))
            raise

    def request_stop(self) -> None:
        """Signal the watcher to exit on the next iteration."""
        self._stop.set()

    async def _scan_all(self) -> None:
        """Initial pass: process every .jsonl file from byte 0.

        Used both on daemon startup (catch-up after downtime) and as
        the inner loop of :meth:`_handle_changes` when a single file
        changes. Ensures the journals directory exists so subsequent
        watch calls have a target.
        """
        self._journals_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self._journals_dir.glob("*.jsonl")):
            if path.stem in self._skip_stems:
                # Already-finalised session: don't replay its backlog.
                # Seed the offset at EOF so any later append is still
                # tailed live (completed sessions normally never grow).
                with contextlib.suppress(OSError):
                    self._offsets[path] = (path.stat().st_size, 0)
                continue
            await self._scan_one(path)

    async def _handle_changes(self, changes: set[tuple[Change, str]]) -> None:
        """Process the change set yielded by ``watchfiles.awatch``."""
        # We only care about the *paths* that changed; the precise
        # kind (added / modified / deleted) is irrelevant because
        # we always read from the last known offset to EOF. A
        # deletion just means the offset cache becomes stale, which
        # is harmless until a future create reuses the name (and at
        # that point we re-scan from offset 0 because the file
        # mtime/size will diverge).
        for _kind, str_path in changes:
            path = Path(str_path)
            if path.suffix != ".jsonl":
                continue
            if path.parent.resolve() != self._journals_dir.resolve():
                # The watchfiles non-recursive flag should prevent
                # this, but cheap belt-and-braces.
                continue
            await self._scan_one(path)

    async def _scan_one(self, path: Path) -> None:
        """Read ``path`` from the cached offset to EOF, publish new records.

        If the file has shrunk since we last looked (deletion + reuse
        of the same name, or a manual truncate), we reset to byte 0
        and the next-idx counter to 0. That's the only way to
        preserve the "line position is idx" contract across a
        recreate.
        """
        if not path.is_file():
            self._offsets.pop(path, None)
            return
        try:
            stat = path.stat()
        except OSError:
            return
        offset, next_idx = self._offsets.get(path, (0, 0))
        if stat.st_size < offset:
            # File was truncated or recreated -- start over.
            offset, next_idx = 0, 0
        if stat.st_size == offset:
            return
        try:
            with path.open("rb") as fp:
                fp.seek(offset)
                # Read the full remaining tail. The journal records
                # are typically <2 KB each; even a busy session is
                # a few MB at most.
                tail = fp.read(stat.st_size - offset)
        except OSError:
            return
        # We may have caught a partial last record (the hook is
        # mid-write). The contract is "one JSON object per line
        # ending in \n," so we split on \n and keep any unterminated
        # remainder for the next pass.
        complete_block, _, remainder = tail.rpartition(b"\n")
        if not complete_block and remainder:
            # No newline yet -- wait for more.
            return
        consumed_bytes = offset + len(complete_block) + 1  # +1 for the trailing \n
        for raw_line in complete_block.split(b"\n"):
            if not raw_line.strip():
                continue
            if len(raw_line) > MAX_LINE_BYTES:
                _log.warning(
                    "journal_watcher_oversize",
                    path=str(path),
                    bytes=len(raw_line),
                )
                next_idx += 1  # advance idx even on skip -- line position stays consistent
                continue
            published = await self._publish_line(raw_line, idx=next_idx)
            if published:
                next_idx += 1
            else:
                # Record was malformed JSON or non-object. Still
                # bump the idx so the line-position contract holds.
                next_idx += 1
        self._offsets[path] = (consumed_bytes, next_idx)

    async def _publish_line(self, line: bytes, *, idx: int) -> bool:
        """Parse one journal line and publish the inner event.

        Returns True on success (an :class:`IngestedEvent` was
        published), False on parse / validation failure. The
        caller advances ``next_idx`` either way to keep idx aligned
        with line position.
        """
        try:
            rec: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            _log.warning(
                "journal_watcher_bad_json",
                error=str(exc),
                head=line[:120].decode("utf-8", errors="replace"),
            )
            return False
        if not isinstance(rec, dict):
            return False
        raw = rec.get("raw")
        if not isinstance(raw, dict):
            return False
        try:
            event = parse_event(raw)
        except Exception as exc:  # noqa: BLE001 - pydantic raises many shapes
            _log.warning(
                "journal_watcher_validation_failed",
                idx=idx,
                session_id=raw.get("session_id"),
                event_name=raw.get("hook_event_name"),
                error=str(exc),
            )
            return False
        # The journal's ts is the wall-clock the hook wrote -- the
        # runner uses it for started_at / ended_at so orphan-journal
        # replay produces non-zero session durations.
        ts_raw = rec.get("ts")
        ts = ts_raw if isinstance(ts_raw, int) else 0
        await self._bus.publish(IngestedEvent(idx=idx, event=event, ts=ts))
        return True

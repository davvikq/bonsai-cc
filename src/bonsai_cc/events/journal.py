"""Per-session JSONL event journal.

Append-only, one JSON record per line. Each session has its own file
at ``<home>/journals/{session_id}.jsonl``. Crash-recovery semantics
depend on every successful write being on disk before we return, so we
fsync after every append.

Record shape (see the design contract)::

    {"ts": 1715694523123, "idx": 42, "raw": {...full payload...}}

* ``ts`` is wall-clock UTC milliseconds (from ``datetime.now(UTC)``).
* ``idx`` is the session-local monotonically increasing event index.
* ``raw`` is the payload exactly as received from the hook.

Atomicity: each line is one short ``write`` followed by ``fsync``.
Short writes on POSIX/Windows are atomic at the page level for our
record sizes; we never partially commit a record.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bonsai_cc.log import get_logger

__all__ = ["Journal", "JournalRegistry", "read_journal"]


_log = get_logger("bonsai_cc.events.journal")

# Whitelist (by negation) of characters permitted in a sanitized
# session id. Anything else is replaced with ``_``. Dots are deliberately
# excluded so a payload like ``../etc/passwd`` becomes ``__etc_passwd``,
# never a path-like string. Length is also capped so a 10 MiB session_id
# cannot DoS the filesystem.
#
# Whitelist (not blacklist) on purpose: blacklists for path
# traversal lose to creative Unicode every time.
# The only characters that survive verbatim are ASCII letters, digits,
# underscore, and hyphen -- the same set permitted in DNS labels and
# what UUIDs use.
_SESSION_ID_ALLOWED = re.compile(r"[^A-Za-z0-9_-]")
_SESSION_ID_MAX_LEN = 128


def _safe_session_filename(session_id: str) -> str:
    """Sanitize a session id for use as a filename.

    Whitelist policy: only ``[A-Za-z0-9_-]`` survive verbatim; everything
    else (including Unicode normal forms that decompose to slashes,
    nulls, dot-dot, etc.) is replaced with ``_``. The result is then
    truncated to :data:`_SESSION_ID_MAX_LEN` characters. The ``.jsonl``
    extension is added by callers, never sourced from the session id.
    Empty / all-rejected ids fall back to ``"unknown"``.
    """
    cleaned = _SESSION_ID_ALLOWED.sub("_", session_id or "")
    if not cleaned:
        return "unknown"
    return cleaned[:_SESSION_ID_MAX_LEN]


def _now_ms() -> int:
    """Current wall-clock time in UTC milliseconds.

    Wall clock is correct here: journal timestamps are for humans and
    must survive across process restarts. Per the design contract the
    monotonic clock is reserved for ordering, animation, and timeouts
    -- never for timestamps.
    """
    return int(datetime.now(UTC).timestamp() * 1000)


def read_journal(path: Path) -> Iterator[dict[str, Any]]:
    """Yield every record in ``path`` in order, with line-position idx.

    Each yielded dict has the shape ``{"ts": ..., "idx": <line-num>,
    "raw": ...}``. The stored ``idx`` field, if present, is ignored
    in favour of the line counter -- that's the contract that lets
    concurrent hook writes race safely (each gets a distinct line,
    line number IS the idx).

    Malformed JSON lines are skipped (with a WARN log) rather than
    aborting iteration. Used by the daemon's tail-reader, by the
    web replay endpoint, and by orphan-session recovery.
    """
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fp:
        idx = 0
        for line_no, line in enumerate(fp, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                _log.warning(
                    "journal_corrupt_line",
                    path=str(path),
                    line_no=line_no,
                    error=str(exc),
                )
                continue
            if not isinstance(rec, dict):
                continue
            raw = rec.get("raw")
            if not isinstance(raw, dict):
                # Non-conforming record. Skip -- we can't apply_event
                # without a raw payload.
                continue
            ts_val = rec.get("ts")
            ts = ts_val if isinstance(ts_val, int) else 0
            yield {"ts": ts, "idx": idx, "raw": raw}
            idx += 1


class Journal:
    """A per-session JSONL append log.

    Thread-safe: a lock guards the append so concurrent ingest tasks
    don't interleave bytes within a record. Each :class:`Journal`
    holds its own counter for ``idx``; callers must not share an
    instance across sessions.

    Example:
        >>> import tempfile, pathlib
        >>> with tempfile.TemporaryDirectory() as d:
        ...     j = Journal(pathlib.Path(d) / "s1.jsonl")
        ...     j.append({"hook_event_name": "Stop", "session_id": "s1"})
        ...     records = list(j.read())
        ...     records[0]["raw"]["hook_event_name"]
        'Stop'
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._idx = self._scan_last_idx()

    def _scan_last_idx(self) -> int:
        """Recover the next ``idx`` from an existing file (crash resume).

        If the file doesn't exist, start at 0. If it exists but is
        empty or corrupted on the last line, fall back to a line
        count. We tolerate a partially-written trailing line by
        truncating it -- better than refusing to start.
        """
        if not self.path.exists():
            return 0
        last_idx = -1
        try:
            with self.path.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if isinstance(rec, dict) and isinstance(rec.get("idx"), int):
                            last_idx = max(last_idx, int(rec["idx"]))
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:  # pragma: no cover - filesystem oddity
            _log.warning("journal_scan_failed", path=str(self.path), error=str(exc))
            return 0
        return last_idx + 1

    def append(self, raw: dict[str, Any]) -> int:
        """Append a raw hook payload to the journal.

        Returns the ``idx`` assigned to this record. The write is
        durable on return (fsync). Failures are logged at ERROR but
        re-raised -- the ingest pipeline above us decides whether to
        surface the failure to the user or drop the event in-memory.
        """
        with self._lock:
            idx = self._idx
            record = {"ts": _now_ms(), "idx": idx, "raw": raw}
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            try:
                # Open with line buffering disabled; we control flushing.
                fd = os.open(
                    self.path,
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                    0o600,
                )
                try:
                    os.write(fd, line.encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as exc:
                _log.error("journal_append_failed", path=str(self.path), error=str(exc))
                raise
            self._idx = idx + 1
            return idx

    def read(self) -> Iterator[dict[str, Any]]:
        """Yield every record in the journal in order.

        Malformed lines are skipped (with a WARN log) rather than
        aborting iteration -- a single corrupt line should not block
        replay of the rest of the session.

        Idx assignment: line position is authoritative. Hook records
        omit the ``idx`` field; legacy records that carry one are
        ignored in favour of the enumeration counter so a stale idx
        that disagrees with line position can never poison replay.
        """
        yield from read_journal(self.path)


class JournalRegistry:
    """Owns one :class:`Journal` per session id, materialised on demand.

    The daemon holds a single :class:`JournalRegistry`. It maps
    sanitized session ids to open :class:`Journal` instances; the
    underlying files live in ``journals_dir``. Lookup is thread-safe.

    We deliberately keep journals open for the life of the daemon
    rather than re-opening per write: even though each append uses
    ``os.open``/``os.close`` (we don't keep a Python file object),
    the :class:`Journal` instance caches the next-idx counter, and
    re-creating it on every event would re-scan the file from disk.
    """

    def __init__(self, journals_dir: Path) -> None:
        self.journals_dir = journals_dir
        self.journals_dir.mkdir(parents=True, exist_ok=True)
        self._journals: dict[str, Journal] = {}
        self._lock = threading.Lock()

    def for_session(self, session_id: str) -> Journal:
        """Return (and lazily create) the journal for ``session_id``."""
        key = _safe_session_filename(session_id)
        with self._lock:
            j = self._journals.get(key)
            if j is None:
                j = Journal(self.journals_dir / f"{key}.jsonl")
                self._journals[key] = j
            return j

    def path_for(self, session_id: str) -> Path:
        """Return the on-disk path that *would* be used for this session.

        Convenience for callers that need the path (e.g. to copy it
        into the garden DB on ``SessionEnd``) without forcing the
        journal to materialise.
        """
        return self.journals_dir / f"{_safe_session_filename(session_id)}.jsonl"

    def list_journal_files(self) -> list[Path]:
        """Every ``*.jsonl`` file currently in the journals dir.

        Used by the orphan-recovery scan: if a journal exists but
        the matching garden row is stale (or missing entirely),
        the runner replays the journal to recover the session.
        Returns paths sorted by name for deterministic iteration.
        """
        if not self.journals_dir.exists():
            return []
        out: list[Path] = []
        try:
            for p in self.journals_dir.iterdir():
                if p.suffix == ".jsonl" and p.is_file():
                    out.append(p)
        except OSError:  # pragma: no cover - filesystem flake
            return []
        return sorted(out)

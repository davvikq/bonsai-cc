"""bonsai-cc hook client -- STABLE INTERFACE.

============================================================================
DO NOT add features to this file.

This file is shipped verbatim into ``<home>/hook_client.py`` at install
time and baked into the user's ``~/.claude/settings.json``. It stays
there across upgrades of the rest of the package. Anything in here is
an API contract: changing it without bumping a stability version means
silently breaking sessions for users who installed an earlier version.

If you find yourself wanting to add logic here, the answer is almost
always to push the logic into the daemon and keep this client dumb.
============================================================================

WHAT THIS DOES
--------------
Reads a JSON hook payload on stdin, appends one record to the
per-session journal at ``<home>/journals/<session_id>.jsonl``, fsyncs,
exits 0. That's it. The daemon is OPTIONAL: it watches the journals
directory for new lines and forwards them to the live web view. With
no daemon running, events still land durably on disk and the next
``bonsai-cc`` launch absorbs them via orphan recovery.

RECORD FORMAT
-------------
One JSON object per line::

    {"ts": 1715694523123, "raw": {...full payload as received...}}

* ``ts`` is wall-clock UTC milliseconds.
* ``raw`` is the payload exactly as Claude Code sent it.
* No ``idx`` field -- line position IS the idx. Two hooks racing on the
  same session each get a distinct line thanks to ``O_APPEND`` atomic
  semantics; readers number lines from zero.

FAIL-SILENT CONTRACT
--------------------
Every one of these scenarios exits 0 with NO output on stdout or
stderr (Claude Code parses stdout as context; any stderr surfaces in
the user's transcript):

* stdin is empty                                             → exit 0
* stdin contains malformed JSON                              → exit 0
* payload is not a JSON object                               → exit 0
* payload has no ``session_id``                              → exit 0
* journals directory cannot be created                       → exit 0
* disk full / permission denied on append                    → exit 0
* ANY other unexpected exception                             → exit 0
* Total wall-clock exceeds 500ms                             → exit 0 (deadline)

When ``BONSAI_CC_DEBUG=1``, exceptions are appended to
``<home>/logs/hook-client.log``. Otherwise they vanish silently.
The exit code is always 0 -- Claude Code must never be slowed or
distracted by a bonsai-cc problem.

STDLIB ONLY
-----------
This file imports only from the Python standard library: ``json``,
``os``, ``re``, ``sys``, ``time``, ``pathlib``, and ``msvcrt`` on
Windows. CI asserts this via AST walk. Adding any third-party
import here (including ``bonsai_cc.*``) will fail the test suite --
and would burn 50-100ms of cold-start budget for no real win.

CROSS-PLATFORM ATOMIC APPEND
----------------------------
POSIX ``O_APPEND`` guarantees "seek-to-end + write" is one atomic
operation, so concurrent hooks for the same session don't clobber
each other. **Windows does NOT make this guarantee.** Each opener
seeks to end-of-file independently, so 20 racing hooks can land on
the same byte offset and the file ends up with ~50 % loss.

The fix on Windows: take an advisory byte-range lock via
``msvcrt.locking(fd, LK_NBLCK, n)`` for the duration of the write.
We retry with a short backoff because the lock window is microseconds
-- a few hundred retries fit comfortably inside the 500 ms budget.

BUDGET
------
500ms hard wall-clock budget, measured with ``time.monotonic()`` so
it survives system clock jumps. CI benchmark asserts p99 < 150ms
over 50 runs on no-op events.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    # Imported only on Windows so the POSIX AST walk doesn't list a
    # platform-specific module as a runtime import. ``msvcrt`` is
    # stdlib; the cost is < 1 ms.
    import msvcrt

# ---------------------------------------------------------------------------
# Tunables -- these are the only knobs. Do not introduce more.

TOTAL_BUDGET_S = 0.500

# Session id sanitization -- must match
# ``bonsai_cc.events.journal._safe_session_filename`` exactly.
# Whitelist (not blacklist): only ``[A-Za-z0-9_-]`` survive verbatim.
# Anything else (including Unicode that decomposes to slash, dot-dot,
# null, etc.) is replaced with ``_``. Empty / all-rejected ids fall
# back to ``"unknown"``. The result is truncated to 128 chars.
_SID_RE = re.compile(r"[^A-Za-z0-9_-]")
_SID_MAX_LEN = 128

# Fields the redaction pass blanks out when ``BONSAI_CC_REDACT=1`` is
# set. These are the high-signal-of-content names from the Claude Code
# hook schema: ``prompt`` lives on ``UserPromptSubmit``, the rest live
# under ``tool_input`` for Edit/Write/NotebookEdit. The growth engine
# doesn't read any of them -- it only cares about ``hook_event_name``,
# ``tool_name``, ``file_path``, and ``cwd`` -- so redaction is lossless
# for the rendered tree but defeats the "anyone with read access to
# my home dir can see my prompts" failure mode.
_REDACT_KEYS = frozenset({
    "prompt",
    "old_string",
    "new_string",
    "content",
})
_REDACT_MARKER = "[redacted by BONSAI_CC_REDACT]"

# ---------------------------------------------------------------------------


def _home() -> Path:
    """Locate the bonsai-cc state directory.

    Mirrors the logic in ``bonsai_cc.config`` but is duplicated here
    intentionally: importing config would pull pydantic, typer, and
    structlog into a hot path that must not see them.
    """
    override = os.environ.get("BONSAI_CC_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "bonsai-cc"
    return Path.home() / ".bonsai-cc"


def _safe_sid(sid: str) -> str:
    """Sanitize a session id for use as a filename."""
    cleaned = _SID_RE.sub("_", sid or "")
    if not cleaned:
        return "unknown"
    return cleaned[:_SID_MAX_LEN]


def _redact(node):  # type: ignore[no-untyped-def]
    """Walk ``node`` and replace any key in :data:`_REDACT_KEYS`.

    Recursive but bounded by the payload's own depth -- Claude Code's
    hook schema is shallow (event → tool_input → primitives), so this
    is microseconds. Mutates dict/list in place to avoid duplicating
    the payload, which keeps the 500 ms budget comfortable even on
    a 50 KB Edit payload.
    """
    if isinstance(node, dict):
        for key in list(node.keys()):
            if key in _REDACT_KEYS and isinstance(node[key], str):
                node[key] = _REDACT_MARKER
            else:
                _redact(node[key])
    elif isinstance(node, list):
        for item in node:
            _redact(item)


def _redact_enabled() -> bool:
    """Whether ``BONSAI_CC_REDACT`` opts the user into redaction.

    Anything truthy except ``"0"`` / ``"false"`` / ``""`` flips it
    on, so ``BONSAI_CC_REDACT=1`` and ``BONSAI_CC_REDACT=yes`` both
    work the way an env-var-set user expects.
    """
    raw = os.environ.get("BONSAI_CC_REDACT", "").strip().lower()
    if not raw:
        return False
    return raw not in ("0", "false", "no", "off")


def _append_locked(journal_path: Path, blob: bytes, deadline: float) -> bool:
    """Append ``blob`` to ``journal_path``, atomic across processes.

    On POSIX: a single ``O_APPEND`` write is atomic by spec, so we
    just open + write + fsync.

    On Windows: ``O_APPEND`` is *not* atomic across writers, so we
    take an advisory byte-range lock on the first byte of the file
    via ``msvcrt.locking`` for the duration of the write. The lock
    is non-blocking; we retry with a short sleep until the deadline.

    Returns True on success, False if the write was abandoned (lock
    contention past the deadline, or disk error). Failures are
    swallowed silently by the caller -- the hook never raises.
    """
    try:
        fd = os.open(
            journal_path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
    except OSError:
        return False
    try:
        if sys.platform == "win32":
            # Acquire an exclusive byte-range lock on byte 0 of the
            # file. We don't actually care about byte 0 -- it's just a
            # convenient stable address all writers can contend over.
            # ``LK_NBLCK`` is non-blocking; ``LK_LOCK`` blocks ~10 s
            # which would blow our 500 ms budget on the first retry.
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        return False
                    time.sleep(0.002)
            try:
                os.write(fd, blob)
                os.fsync(fd)
            finally:
                try:
                    # Seek back so we unlock the same byte we locked,
                    # then release. ``LK_UNLCK`` releases at the
                    # current file position; the seek matters.
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            # POSIX: O_APPEND atomic write semantics handle the race.
            os.write(fd, blob)
            os.fsync(fd)
        return True
    except OSError:
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _maybe_log_exception(home: Path) -> None:
    """Append the current exception to ``hook-client.log`` if debug is on.

    Best-effort: a logging failure must never propagate. Only invoked
    from the top-level handler in :func:`main`.
    """
    if not os.environ.get("BONSAI_CC_DEBUG"):
        return
    try:
        logs = home / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / "hook-client.log").open("a", encoding="utf-8") as fp:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            fp.write(f"{ts} {sys.exc_info()[1]!r}\n")
    except OSError:
        pass


def main() -> int:
    """Read stdin, append to journal, exit 0 -- see module docstring."""
    deadline = time.monotonic() + TOTAL_BUDGET_S
    home = _home()
    try:
        # 1. Read the payload.
        try:
            payload = sys.stdin.buffer.read()
        except OSError:
            return 0
        if not payload.strip():
            return 0

        # 2. Validate JSON. Garbage in → silent drop.
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0
        if not isinstance(data, dict):
            return 0

        # 3. Resolve the session id (required to pick a journal file).
        sid_raw = data.get("session_id")
        if not isinstance(sid_raw, str) or not sid_raw:
            # No session id -> silent drop. A payload without a
            # session id has nowhere to go in the per-session journal
            # model; loss is the intended outcome. The daemon-side
            # recovery path can still absorb anything that DOES have
            # a session id.
            return 0
        sid = _safe_sid(sid_raw)

        # 4. Make sure the journals directory exists.
        journals_dir = home / "journals"
        try:
            journals_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return 0

        if time.monotonic() > deadline:
            return 0

        # 4b. Optional content redaction. ``BONSAI_CC_REDACT=1``
        #     blanks out ``prompt``, ``old_string``, ``new_string``,
        #     ``content`` -- the high-signal-of-content fields -- so
        #     the journal records WHAT happened but not the literal
        #     text of the prompt / edit. The growth engine doesn't
        #     read these fields, so the rendered tree is unchanged.
        if _redact_enabled():
            _redact(data)

        # 5. Build the record. No ``idx`` -- line position is the idx
        #    (the contract that lets concurrent hooks race safely on a
        #    single journal file).
        try:
            record = json.dumps(
                {"ts": int(time.time() * 1000), "raw": data},
                ensure_ascii=False,
                separators=(",", ":"),
            ) + "\n"
            blob = record.encode("utf-8")
        except (TypeError, ValueError):
            return 0

        # 6. Append + fsync, cross-platform atomic. POSIX uses the
        #    ``O_APPEND`` atomic-seek-and-write guarantee; Windows
        #    takes a byte-range lock for the brief window. Either
        #    way two hooks racing on the same session land on
        #    distinct lines, in arrival order, with no interleaving.
        journal_path = journals_dir / f"{sid}.jsonl"
        _append_locked(journal_path, blob, deadline)
        return 0
    except Exception:  # noqa: BLE001 - top-level "never crash" guard
        _maybe_log_exception(home)
        return 0


if __name__ == "__main__":
    sys.exit(main())

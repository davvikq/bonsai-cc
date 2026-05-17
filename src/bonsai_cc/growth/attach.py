"""Semantic attachment: events → which part of the tree they grow.

This module owns two responsibilities that are too small to split:

1. **Path identity** -- :func:`normalize_path` collapses any number of
   string forms of the same file into one canonical key. Without
   this, ``./auth.py`` and ``src/auth.py`` from different cwds would
   spawn two branches. The rules are pinned in the design contract

2. **Attachment intent** -- :func:`attach_intent` looks at a validated
   :class:`Event` and decides what *kind* of growth it causes, plus
   the identity key that growth is bound to (file path, cwd, host,
   etc.). It never mutates state; it just classifies. The
   transformation logic lives in :mod:`bonsai_cc.growth.apply`.

Adding a new tool means adding one entry to the dispatch in
:func:`_tool_intent`. The unknown-tool fallback (generic leaf on
the most-recent branch) keeps the tree growing on future Claude
Code releases
"""

from __future__ import annotations

from dataclasses import dataclass

from bonsai_cc.events.models import (
    BaseHookEvent,
    PostToolUseEvent,
    PostToolUseFailureEvent,
    PreToolUseEvent,
    SessionStartEvent,
    SubagentStartEvent,
    SubagentStopEvent,
)

__all__ = [
    "AttachmentIntent",
    "AttachmentKind",
    "attach_intent",
    "normalize_path",
]


class AttachmentKind:
    """String enum of growth effects. Lowercase, snake_case for safe
    journalling and equality comparisons in tests."""

    SEED = "seed"
    GROW_BRANCH = "grow_branch"
    ADD_LEAF = "add_leaf"
    ADD_LEAF_CLUSTER = "add_leaf_cluster"
    GROW_ROOT = "grow_root"
    ADD_FLOWER = "add_flower"
    SPAWN_OFFSHOOT = "spawn_offshoot"
    CAP_OFFSHOOT = "cap_offshoot"
    WITHER = "wither"  # PostToolUseFailure
    NO_OP = "no_op"


@dataclass(frozen=True, slots=True)
class AttachmentIntent:
    """The growth engine's decision about one event.

    All fields besides ``kind`` are optional; only the relevant ones
    are populated. ``apply`` reads the matching fields to do the
    transformation.
    """

    kind: str
    file_path_key: str | None = None
    cwd_key: str | None = None
    host_or_query: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    raw_tool_name: str | None = None


# Shell-family tools whose invocation grows a root. Claude Code on
# Windows uses ``PowerShell`` for almost every command (a live
# session in May 2026 produced 8 PowerShell + 1 Bash out of 9 shell
# events). Older corpora and POSIX Claude Code use ``Bash``. ``Cmd``
# also appears occasionally. We treat them as one semantic kind so
# the roots fan out regardless of which shell the agent picked.
_SHELL_TOOL_NAMES: frozenset[str] = frozenset({"Bash", "PowerShell", "Cmd"})


# ---------------------------------------------------------------------------
# Path identity
# ---------------------------------------------------------------------------


def normalize_path(raw_path: str, cwd: str | None) -> str:
    """Return the canonical identity key for ``raw_path``.

    OS-independent on purpose: the same fixture journal must produce
    the same key on Windows, Linux, and macOS so the determinism
    snapshots match across CI matrices. Earlier this used
    ``os.path.realpath`` + ``os.path.normpath`` which prepended a
    drive letter on Windows (``/work/x.py`` -> ``C:\\work\\x.py``),
    diverging from the POSIX-style fixture paths.

    Steps:

    1. Normalise separators to ``/``.
    2. Drop any leading drive prefix (``C:``).
    3. If still relative, resolve against ``cwd`` (when known) using
       the same forward-slash POSIX convention.
    4. Collapse ``.`` / ``..`` segments by walking the parts list.

    Failure falls back to the raw string verbatim -- a duplicated
    branch is preferable to a dropped event.

    Example:
        >>> normalize_path("auth.py", "/work/proj")
        '/work/proj/auth.py'
    """
    if not raw_path:
        return ""
    try:
        cleaned = _to_posix(raw_path)
        if not cleaned.startswith("/"):
            base = _to_posix(cwd) if cwd else ""
            if base:
                cleaned = base.rstrip("/") + "/" + cleaned
        # Collapse '.' / '..' segments manually so the result is
        # identical on every OS regardless of the local filesystem.
        parts: list[str] = []
        for part in cleaned.split("/"):
            if part in ("", "."):
                continue
            if part == ".." and parts:
                parts.pop()
                continue
            if part == "..":
                continue
            parts.append(part)
        prefix = "/" if cleaned.startswith("/") else ""
        return prefix + "/".join(parts)
    except (OSError, ValueError):
        return raw_path


def _to_posix(p: str) -> str:
    """Convert separators to ``/`` and drop a leading drive prefix.

    ``C:\\work\\x.py`` -> ``/work/x.py``;
    ``/work/x.py`` -> ``/work/x.py``;
    ``work\\x.py``  -> ``work/x.py``.
    """
    s = p.replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        # Drive prefix like ``C:`` or ``c:/`` -- strip and keep the
        # remainder (which may or may not start with ``/``).
        rest = s[2:]
        return rest if rest.startswith("/") else "/" + rest
    return s


def normalize_cwd(raw_cwd: str | None) -> str:
    """Normalize a cwd for use as a root-cluster identity key.

    Empty / missing falls back to ``""`` so roots from "unknown cwd"
    cluster together rather than spawning a root per event.
    """
    if not raw_cwd:
        return ""
    return normalize_path(raw_cwd, cwd=None)


# ---------------------------------------------------------------------------
# Event → intent dispatch
# ---------------------------------------------------------------------------


def attach_intent(event: BaseHookEvent) -> AttachmentIntent:
    """Classify ``event`` into an :class:`AttachmentIntent`.

    Routes on the event type first, then on ``tool_name`` for the
    PostToolUse family. Unknown tool / event types still produce a
    safe intent (``NO_OP`` or ``ADD_LEAF`` for unknown tools) so the
    pipeline never crashes on Claude Code schema drift.
    """
    if isinstance(event, SessionStartEvent):
        return AttachmentIntent(kind=AttachmentKind.SEED)
    if isinstance(event, PostToolUseEvent):
        return _tool_intent(event)
    if isinstance(event, PostToolUseFailureEvent):
        return _wither_intent(event)
    if isinstance(event, SubagentStartEvent):
        return AttachmentIntent(
            kind=AttachmentKind.SPAWN_OFFSHOOT,
            agent_id=event.agent_id or "",
            agent_type=event.agent_type or "",
        )
    if isinstance(event, SubagentStopEvent):
        return AttachmentIntent(
            kind=AttachmentKind.CAP_OFFSHOOT,
            agent_id=event.agent_id or "",
            agent_type=event.agent_type or "",
        )
    if isinstance(event, PreToolUseEvent):
        # Pre-events bump the event count but don't grow geometry;
        # the matching Post event does the growth.
        return AttachmentIntent(kind=AttachmentKind.NO_OP)
    # Unknown events (PostCompact, ConfigChange, Notification, ...) are
    # logged only; apply() bumps the event counter but makes no
    # structural change.
    return AttachmentIntent(kind=AttachmentKind.NO_OP)


def _tool_intent(event: PostToolUseEvent) -> AttachmentIntent:
    """Map a PostToolUse event to growth based on its tool name."""
    tool = event.tool_name
    cwd = event.cwd
    tool_input = event.tool_input

    if tool in ("Edit", "Write", "NotebookEdit"):
        raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return AttachmentIntent(
            kind=AttachmentKind.GROW_BRANCH,
            file_path_key=normalize_path(str(raw), cwd) if raw else None,
            raw_tool_name=tool,
        )
    if tool == "Read":
        raw = tool_input.get("file_path") or ""
        return AttachmentIntent(
            kind=AttachmentKind.ADD_LEAF,
            file_path_key=normalize_path(str(raw), cwd) if raw else None,
            raw_tool_name=tool,
        )
    if tool in ("Glob", "Grep"):
        return AttachmentIntent(
            kind=AttachmentKind.ADD_LEAF_CLUSTER,
            raw_tool_name=tool,
        )
    if tool in _SHELL_TOOL_NAMES:
        return AttachmentIntent(
            kind=AttachmentKind.GROW_ROOT,
            cwd_key=normalize_cwd(cwd),
            raw_tool_name=tool,
        )
    if tool == "WebFetch":
        url = str(tool_input.get("url", ""))
        host = _host_from_url(url)
        return AttachmentIntent(
            kind=AttachmentKind.ADD_FLOWER,
            host_or_query=host,
            raw_tool_name=tool,
        )
    if tool == "WebSearch":
        query = str(tool_input.get("query", ""))
        return AttachmentIntent(
            kind=AttachmentKind.ADD_FLOWER,
            host_or_query=query[:64],
            raw_tool_name=tool,
        )
    if tool in ("Agent", "Task"):
        agent = tool_input.get("subagent_type") or tool_input.get("agent") or ""
        return AttachmentIntent(
            kind=AttachmentKind.SPAWN_OFFSHOOT,
            agent_id=event.tool_use_id or "",
            agent_type=str(agent),
            raw_tool_name=tool,
        )
    # Unknown tool: generic leaf on the most-recent branch.
    return AttachmentIntent(
        kind=AttachmentKind.ADD_LEAF,
        file_path_key=None,
        raw_tool_name=tool,
    )


def _wither_intent(event: PostToolUseFailureEvent) -> AttachmentIntent:
    """Map a tool failure to its visual effect.

    We try to bind the wilt to the file the failed tool touched
    (so an Edit failure yellows the right branch); otherwise we
    wilt the most-recently-active branch (resolved in ``apply``).
    """
    raw = ""
    tool_input = event.tool_input
    if isinstance(tool_input, dict):
        raw = (
            tool_input.get("file_path")
            or tool_input.get("notebook_path")
            or ""
        )
    return AttachmentIntent(
        kind=AttachmentKind.WITHER,
        file_path_key=normalize_path(str(raw), event.cwd) if raw else None,
        raw_tool_name=event.tool_name,
    )


def _host_from_url(url: str) -> str:
    """Pluck the host out of a URL without importing urllib.

    Stdlib ``urllib.parse`` is fine to use elsewhere; here we keep
    the surface small and tolerant of garbage (a malformed URL must
    not crash growth -- it just becomes a flower with an empty host).
    """
    if not url:
        return ""
    s = url.split("://", 1)[-1]
    s = s.split("/", 1)[0]
    s = s.split("?", 1)[0]
    s = s.split("#", 1)[0]
    return s[:64]

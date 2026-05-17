"""File-path normalisation and event → attachment-intent dispatch."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from bonsai_cc.events.models import (
    BashToolInput,
    Event,
    PostToolUseEvent,
    PostToolUseFailureEvent,
    SessionStartEvent,
    SubagentStartEvent,
    parse_event,
)
from bonsai_cc.growth.attach import (
    AttachmentKind,
    attach_intent,
    normalize_cwd,
    normalize_path,
)

# ---------------------------------------------------------------------------
# normalize_path
# ---------------------------------------------------------------------------


def test_relative_path_resolves_against_cwd(tmp_path: Path) -> None:
    """Two paths that *mean the same file* must hash to the same key."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "auth.py").write_text("x", encoding="utf-8")
    src = project / "src"
    src.mkdir()
    (src / "auth.py").write_text("y", encoding="utf-8")

    # Same file, two surface forms.
    a = normalize_path("auth.py", str(project))
    b = normalize_path("./auth.py", str(project))
    assert a == b


def test_absolute_path_is_canonical(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x", encoding="utf-8")
    a = normalize_path(str(target), cwd=None)
    b = normalize_path(str(target), cwd=str(tmp_path))
    assert a == b
    # The canonical form matches ``normpath(realpath(...))`` modulo the
    # Windows drive-letter rule (DESIGN.md §2.1 step 4 lowercases it).
    expected = os.path.normpath(str(target.resolve()))
    if sys.platform == "win32" and len(expected) >= 2 and expected[1] == ":":
        expected = expected[0].lower() + expected[1:]
    assert a == expected


def test_missing_file_still_normalizes(tmp_path: Path) -> None:
    """``realpath`` returns its best guess for a non-existent file —
    we keep the result so a typo doesn't drop the event entirely."""
    out = normalize_path("does-not-exist.py", str(tmp_path))
    # Must include the cwd prefix and the original filename. The
    # comparison is case-insensitive on Windows because the drive
    # letter is normalised to lowercase.
    assert "does-not-exist.py" in out
    if sys.platform == "win32":
        assert str(tmp_path).lower() in out.lower()
    else:
        assert str(tmp_path) in out


def test_empty_path_collapses_to_empty() -> None:
    assert normalize_path("", "/tmp") == ""


@pytest.mark.skipif(sys.platform != "win32", reason="drive-letter rule is win32-only")
def test_drive_letter_is_lowercased(tmp_path: Path) -> None:
    """On Windows, ``C:\\foo`` and ``c:\\foo`` must hash to the same key."""
    target = tmp_path / "z.py"
    target.write_text("x", encoding="utf-8")
    # Force-uppercase the drive letter on a parallel form.
    abs_str = str(target.resolve())
    if len(abs_str) >= 2 and abs_str[1] == ":":
        upper = abs_str[0].upper() + abs_str[1:]
        lower = abs_str[0].lower() + abs_str[1:]
        assert normalize_path(upper, cwd=None) == normalize_path(lower, cwd=None)


def test_fallback_returns_raw_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If normalisation explodes, the raw string is preserved."""
    from bonsai_cc.growth import attach as attach_mod

    def boom(_: str) -> str:
        raise OSError("filesystem on fire")

    monkeypatch.setattr(attach_mod.os.path, "realpath", boom)
    out = normalize_path("weird.py", "/tmp")
    assert out == "weird.py"


def test_normalize_cwd_empty_returns_empty() -> None:
    assert normalize_cwd("") == ""
    assert normalize_cwd(None) == ""


# ---------------------------------------------------------------------------
# attach_intent — per-event-type dispatch
# ---------------------------------------------------------------------------


def _event(name: str, **extra: object) -> Event:
    payload: dict[str, object] = {
        "session_id": "s",
        "hook_event_name": name,
        **extra,
    }
    return parse_event(payload)


def test_session_start_intends_seed() -> None:
    intent = attach_intent(_event("SessionStart"))
    assert intent.kind == AttachmentKind.SEED


def test_pre_tool_use_is_a_noop() -> None:
    """Pre events bump counters but don't grow geometry."""
    intent = attach_intent(
        _event(
            "PreToolUse",
            tool_name="Bash",
            tool_input={"command": "ls"},
        )
    )
    assert intent.kind == AttachmentKind.NO_OP


@pytest.mark.parametrize("tool", ["Edit", "Write", "NotebookEdit"])
def test_edit_family_grows_a_branch(tmp_path: Path, tool: str) -> None:
    target = tmp_path / "a.py"
    target.write_text("x", encoding="utf-8")
    tool_input = {"file_path": str(target)}
    if tool == "NotebookEdit":
        tool_input = {"notebook_path": str(target)}
    elif tool == "Edit":
        tool_input |= {"old_string": "x", "new_string": "y"}
    elif tool == "Write":
        tool_input |= {"content": "y"}
    intent = attach_intent(
        _event(
            "PostToolUse",
            tool_name=tool,
            tool_input=tool_input,
            cwd=str(tmp_path),
        )
    )
    assert intent.kind == AttachmentKind.GROW_BRANCH
    assert intent.file_path_key
    assert "a.py" in intent.file_path_key


def test_read_adds_a_leaf(tmp_path: Path) -> None:
    target = tmp_path / "r.py"
    target.write_text("x", encoding="utf-8")
    intent = attach_intent(
        _event(
            "PostToolUse",
            tool_name="Read",
            tool_input={"file_path": str(target)},
            cwd=str(tmp_path),
        )
    )
    assert intent.kind == AttachmentKind.ADD_LEAF


@pytest.mark.parametrize("tool", ["Grep", "Glob"])
def test_grep_glob_adds_a_cluster(tool: str) -> None:
    intent = attach_intent(
        _event(
            "PostToolUse",
            tool_name=tool,
            tool_input={"pattern": "foo"},
        )
    )
    assert intent.kind == AttachmentKind.ADD_LEAF_CLUSTER


def test_bash_grows_a_root() -> None:
    intent = attach_intent(
        _event(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": "ls"},
            cwd="/project",
        )
    )
    assert intent.kind == AttachmentKind.GROW_ROOT
    assert intent.cwd_key != ""


@pytest.mark.parametrize("shell", ["PowerShell", "Cmd", "Bash"])
def test_every_shell_family_tool_grows_a_root(shell: str) -> None:
    """Windows Claude Code uses ``PowerShell``, not ``Bash``. We
    must recognise the whole shell family so the roots actually
    appear — the original release missed PowerShell and produced
    rootless trees on every Windows session.
    """
    intent = attach_intent(
        _event(
            "PostToolUse",
            tool_name=shell,
            tool_input={"command": "echo hi"},
            cwd="C:\\proj",
        )
    )
    assert intent.kind == AttachmentKind.GROW_ROOT
    assert intent.raw_tool_name == shell


def test_webfetch_extracts_host() -> None:
    intent = attach_intent(
        _event(
            "PostToolUse",
            tool_name="WebFetch",
            tool_input={"url": "https://docs.example.com/foo/bar?q=1"},
        )
    )
    assert intent.kind == AttachmentKind.ADD_FLOWER
    assert intent.host_or_query == "docs.example.com"


def test_websearch_uses_query() -> None:
    intent = attach_intent(
        _event(
            "PostToolUse",
            tool_name="WebSearch",
            tool_input={"query": "python lsystem deterministic"},
        )
    )
    assert intent.kind == AttachmentKind.ADD_FLOWER
    assert "python" in (intent.host_or_query or "")


def test_agent_spawns_offshoot() -> None:
    intent = attach_intent(
        _event(
            "PostToolUse",
            tool_name="Agent",
            tool_input={"subagent_type": "Explore", "task": "find x"},
            tool_use_id="tu-1",
        )
    )
    assert intent.kind == AttachmentKind.SPAWN_OFFSHOOT
    assert intent.agent_type == "Explore"


def test_unknown_tool_falls_back_to_leaf() -> None:
    intent = attach_intent(
        _event(
            "PostToolUse",
            tool_name="BrandNewTool",
            tool_input={"whatever": 1},
        )
    )
    assert intent.kind == AttachmentKind.ADD_LEAF
    assert intent.file_path_key is None


def test_failure_targets_the_file(tmp_path: Path) -> None:
    target = tmp_path / "f.py"
    target.write_text("x", encoding="utf-8")
    intent = attach_intent(
        _event(
            "PostToolUseFailure",
            tool_name="Edit",
            tool_input={"file_path": str(target)},
            cwd=str(tmp_path),
            error="boom",
        )
    )
    assert intent.kind == AttachmentKind.WITHER
    assert intent.file_path_key and "f.py" in intent.file_path_key


def test_subagent_start_and_stop_pair() -> None:
    start = attach_intent(
        _event(
            "SubagentStart",
            agent_id="a1",
            agent_type="Explore",
        )
    )
    stop = attach_intent(
        _event(
            "SubagentStop",
            agent_id="a1",
            agent_type="Explore",
        )
    )
    assert start.kind == AttachmentKind.SPAWN_OFFSHOOT
    assert stop.kind == AttachmentKind.CAP_OFFSHOOT
    assert start.agent_id == stop.agent_id == "a1"


# We don't have to keep these references — they're for type-checking
# the imports above.
_ = (BashToolInput, PostToolUseEvent, PostToolUseFailureEvent, SessionStartEvent, SubagentStartEvent)

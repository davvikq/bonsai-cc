"""Pydantic model coverage: one assertion per hook event we model."""

from __future__ import annotations

from typing import Any

import pytest

from bonsai_cc.events.models import (
    BashToolInput,
    EditToolInput,
    Event,
    PostToolUseEvent,
    PostToolUseFailureEvent,
    PreToolUseEvent,
    SessionEndEvent,
    SessionStartEvent,
    StopEvent,
    SubagentStartEvent,
    SubagentStopEvent,
    UnknownEvent,
    parse_event,
    parse_tool_input,
)


@pytest.mark.parametrize(
    "name,extra,expected",
    [
        ("SessionStart", {"source": "startup"}, SessionStartEvent),
        ("SessionEnd", {"end_reason": "clear"}, SessionEndEvent),
        ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, PreToolUseEvent),
        (
            "PostToolUse",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_result": {"type": "text", "text": "ok"},
            },
            PostToolUseEvent,
        ),
        (
            "PostToolUseFailure",
            {"tool_name": "Bash", "tool_input": {"command": "ls"}, "error": "nope"},
            PostToolUseFailureEvent,
        ),
        ("Stop", {"stop_reason": "end_turn"}, StopEvent),
        ("SubagentStart", {"agent_id": "a1", "agent_type": "Explore"}, SubagentStartEvent),
        ("SubagentStop", {"agent_id": "a1", "result": "ok"}, SubagentStopEvent),
    ],
)
def test_dispatches_to_correct_model(name: str, extra: dict[str, Any], expected: type) -> None:
    payload = {"session_id": "s1", "hook_event_name": name, **extra}
    event = parse_event(payload)
    assert isinstance(event, expected)
    assert event.hook_event_name == name
    assert event.session_id == "s1"


def test_unknown_event_falls_through() -> None:
    payload = {
        "session_id": "s1",
        "hook_event_name": "BrandNewFutureEvent",
        "some_new_field": 42,
    }
    event = parse_event(payload)
    assert isinstance(event, UnknownEvent)
    assert event.hook_event_name == "BrandNewFutureEvent"
    # extra="allow" must preserve the unknown field for downstream code.
    assert event.model_dump().get("some_new_field") == 42


def test_unknown_field_on_known_event_is_preserved() -> None:
    payload = {
        "session_id": "s1",
        "hook_event_name": "Stop",
        "future_field_we_dont_know_about": "carry me through",
    }
    event = parse_event(payload)
    assert isinstance(event, StopEvent)
    dumped = event.model_dump()
    assert dumped["future_field_we_dont_know_about"] == "carry me through"


def test_pre_tool_use_typed_tool_input() -> None:
    event = parse_event(
        {
            "session_id": "s1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la", "description": "list"},
        }
    )
    assert isinstance(event, PreToolUseEvent)
    parsed = event.parsed_tool_input()
    assert isinstance(parsed, BashToolInput)
    assert parsed.command == "ls -la"


def test_edit_tool_input_required_fields() -> None:
    ti = parse_tool_input(
        "Edit",
        {"file_path": "/tmp/a.py", "old_string": "x", "new_string": "y"},
    )
    assert isinstance(ti, EditToolInput)
    assert ti.file_path == "/tmp/a.py"
    assert ti.replace_all is None


def test_unknown_tool_returns_none() -> None:
    assert parse_tool_input("BrandNewTool", {"whatever": 1}) is None


def test_typed_event_alias_is_a_union() -> None:
    # Smoke: the public ``Event`` symbol must include every known model.
    event: Event = parse_event({"session_id": "s1", "hook_event_name": "Stop"})
    assert isinstance(event, StopEvent)

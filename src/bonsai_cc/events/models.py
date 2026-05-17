"""Pydantic v2 models for every Claude Code hook event.

Schema reference: https://code.claude.com/docs/en/hooks (snapshotted in
the design contract). All models use ``extra="allow"`` so unknown fields from
future Claude Code releases never crash parsing -- they are passed
through to the journal verbatim and ignored by the growth pipeline
until we add typed support.

Dispatch happens in :func:`parse_event`, which routes on
``hook_event_name``. Unknown event names land on
:class:`UnknownEvent`, which is intentionally permissive so the
session is never lost.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BaseHookEvent",
    "BashToolInput",
    "EditToolInput",
    "Event",
    "GlobToolInput",
    "GrepToolInput",
    "NotebookEditToolInput",
    "PostToolUseEvent",
    "PostToolUseFailureEvent",
    "PreToolUseEvent",
    "ReadToolInput",
    "SessionEndEvent",
    "SessionStartEvent",
    "StopEvent",
    "SubagentStartEvent",
    "SubagentStopEvent",
    "UnknownEvent",
    "UserPromptSubmitEvent",
    "WebFetchToolInput",
    "WebSearchToolInput",
    "WriteToolInput",
    "parse_event",
]


# ---------------------------------------------------------------------------
# Tool input models
# ---------------------------------------------------------------------------
#
# These describe ``tool_input`` for each tool we recognise. Anything we
# don't recognise stays as the raw dict on the parent event and grows
# the tree generically. See ``the design contract`` §3.2.


class _ToolInputBase(BaseModel):
    """Tool-input common config: tolerant to extra fields."""

    model_config = ConfigDict(extra="allow")


class BashToolInput(_ToolInputBase):
    command: str
    description: str | None = None
    timeout: int | None = None
    run_in_background: bool | None = None


class EditToolInput(_ToolInputBase):
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool | None = None


class WriteToolInput(_ToolInputBase):
    file_path: str
    content: str


class ReadToolInput(_ToolInputBase):
    file_path: str
    offset: int | None = None
    limit: int | None = None


class GlobToolInput(_ToolInputBase):
    pattern: str
    path: str | None = None


class GrepToolInput(_ToolInputBase):
    pattern: str
    path: str | None = None
    glob: str | None = None
    output_mode: Literal["content", "files_with_matches", "count"] | None = None


class WebFetchToolInput(_ToolInputBase):
    url: str
    prompt: str | None = None


class WebSearchToolInput(_ToolInputBase):
    query: str


class NotebookEditToolInput(_ToolInputBase):
    notebook_path: str
    new_source: str | None = None
    cell_id: str | None = None
    cell_type: str | None = None
    edit_mode: str | None = None


class AgentToolInput(_ToolInputBase):
    """Sub-agent invocation (the ``Task`` / ``Agent`` tool)."""

    agent: str | None = None
    task: str | None = None
    description: str | None = None
    prompt: str | None = None
    subagent_type: str | None = None


TOOL_INPUT_MODELS: dict[str, type[_ToolInputBase]] = {
    "Bash": BashToolInput,
    "Edit": EditToolInput,
    "Write": WriteToolInput,
    "Read": ReadToolInput,
    "Glob": GlobToolInput,
    "Grep": GrepToolInput,
    "WebFetch": WebFetchToolInput,
    "WebSearch": WebSearchToolInput,
    "NotebookEdit": NotebookEditToolInput,
    "Agent": AgentToolInput,
    "Task": AgentToolInput,
}


def parse_tool_input(tool_name: str, raw: dict[str, Any]) -> _ToolInputBase | None:
    """Return a typed model for the given tool, or ``None`` if unknown.

    Unknown tools still grow the tree generically (see ``growth/attach``
    in later phases). We return ``None`` rather than raising so the
    ingest pipeline never drops events on a new tool name.

    Example:
        >>> ti = parse_tool_input("Bash", {"command": "ls"})
        >>> assert isinstance(ti, BashToolInput) and ti.command == "ls"
    """
    cls = TOOL_INPUT_MODELS.get(tool_name)
    if cls is None:
        return None
    return cls.model_validate(raw)


# ---------------------------------------------------------------------------
# Hook event models
# ---------------------------------------------------------------------------


class BaseHookEvent(BaseModel):
    """Fields present on every hook event we care about."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    transcript_path: str | None = None
    cwd: str | None = None
    hook_event_name: str


class SessionStartEvent(BaseHookEvent):
    hook_event_name: Literal["SessionStart"] = "SessionStart"
    source: str | None = None  # "startup" | "resume" | "clear" | "compact"
    model: str | None = None
    agent_type: str | None = None


class SessionEndEvent(BaseHookEvent):
    hook_event_name: Literal["SessionEnd"] = "SessionEnd"
    end_reason: str | None = None


class UserPromptSubmitEvent(BaseHookEvent):
    hook_event_name: Literal["UserPromptSubmit"] = "UserPromptSubmit"
    prompt: str | None = None
    permission_mode: str | None = None


class UserPromptExpansionEvent(BaseHookEvent):
    hook_event_name: Literal["UserPromptExpansion"] = "UserPromptExpansion"
    prompt: str | None = None
    expansion_type: str | None = None
    command_name: str | None = None
    command_args: str | None = None
    command_source: str | None = None
    permission_mode: str | None = None


class _ToolEventBase(BaseHookEvent):
    """Common fields shared by ``Pre/PostToolUse(Failure)``."""

    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_use_id: str | None = None
    permission_mode: str | None = None
    effort: dict[str, Any] | None = None
    agent_id: str | None = None
    agent_type: str | None = None

    def parsed_tool_input(self) -> _ToolInputBase | None:
        """Return a typed tool-input model, or ``None`` for unknown tools."""
        return parse_tool_input(self.tool_name, self.tool_input)


class PreToolUseEvent(_ToolEventBase):
    hook_event_name: Literal["PreToolUse"] = "PreToolUse"


class PostToolUseEvent(_ToolEventBase):
    hook_event_name: Literal["PostToolUse"] = "PostToolUse"
    tool_result: dict[str, Any] | None = None


class PostToolUseFailureEvent(_ToolEventBase):
    hook_event_name: Literal["PostToolUseFailure"] = "PostToolUseFailure"
    error: str | None = None


class PostToolBatchEvent(BaseHookEvent):
    hook_event_name: Literal["PostToolBatch"] = "PostToolBatch"
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    permission_mode: str | None = None


class PermissionRequestEvent(_ToolEventBase):
    hook_event_name: Literal["PermissionRequest"] = "PermissionRequest"
    permission_rule: dict[str, Any] | None = None


class PermissionDeniedEvent(_ToolEventBase):
    hook_event_name: Literal["PermissionDenied"] = "PermissionDenied"


class NotificationEvent(BaseHookEvent):
    hook_event_name: Literal["Notification"] = "Notification"
    message: str | None = None
    notification_type: str | None = None


class SubagentStartEvent(BaseHookEvent):
    hook_event_name: Literal["SubagentStart"] = "SubagentStart"
    agent_id: str | None = None
    agent_type: str | None = None
    task: str | None = None


class SubagentStopEvent(BaseHookEvent):
    hook_event_name: Literal["SubagentStop"] = "SubagentStop"
    agent_id: str | None = None
    agent_type: str | None = None
    result: str | None = None


class StopEvent(BaseHookEvent):
    hook_event_name: Literal["Stop"] = "Stop"
    response: str | None = None
    stop_reason: str | None = None


class StopFailureEvent(BaseHookEvent):
    hook_event_name: Literal["StopFailure"] = "StopFailure"
    error_type: str | None = None
    error_message: str | None = None


class PreCompactEvent(BaseHookEvent):
    hook_event_name: Literal["PreCompact"] = "PreCompact"
    trigger: str | None = None  # "manual" | "auto"


class PostCompactEvent(BaseHookEvent):
    hook_event_name: Literal["PostCompact"] = "PostCompact"
    trigger: str | None = None


class CwdChangedEvent(BaseHookEvent):
    hook_event_name: Literal["CwdChanged"] = "CwdChanged"
    previous_cwd: str | None = None
    new_cwd: str | None = None


class FileChangedEvent(BaseHookEvent):
    hook_event_name: Literal["FileChanged"] = "FileChanged"
    file_path: str | None = None
    change_type: str | None = None  # "modified" | "created" | "deleted"


class TaskCreatedEvent(BaseHookEvent):
    hook_event_name: Literal["TaskCreated"] = "TaskCreated"
    task_id: str | None = None
    task_name: str | None = None
    task_description: str | None = None


class TaskCompletedEvent(BaseHookEvent):
    hook_event_name: Literal["TaskCompleted"] = "TaskCompleted"
    task_id: str | None = None
    task_name: str | None = None


class InstructionsLoadedEvent(BaseHookEvent):
    hook_event_name: Literal["InstructionsLoaded"] = "InstructionsLoaded"
    file_path: str | None = None
    memory_type: str | None = None
    load_reason: str | None = None


class ConfigChangeEvent(BaseHookEvent):
    hook_event_name: Literal["ConfigChange"] = "ConfigChange"
    config_source: str | None = None
    config_path: str | None = None


class WorktreeCreateEvent(BaseHookEvent):
    hook_event_name: Literal["WorktreeCreate"] = "WorktreeCreate"
    base_path: str | None = None
    worktree_name: str | None = None


class WorktreeRemoveEvent(BaseHookEvent):
    hook_event_name: Literal["WorktreeRemove"] = "WorktreeRemove"
    worktree_path: str | None = None


class ElicitationEvent(BaseHookEvent):
    hook_event_name: Literal["Elicitation"] = "Elicitation"
    server_name: str | None = None
    request: dict[str, Any] | None = None


class ElicitationResultEvent(BaseHookEvent):
    hook_event_name: Literal["ElicitationResult"] = "ElicitationResult"
    server_name: str | None = None
    result: dict[str, Any] | None = None


class TeammateIdleEvent(BaseHookEvent):
    hook_event_name: Literal["TeammateIdle"] = "TeammateIdle"
    agent_id: str | None = None
    agent_type: str | None = None


class SetupEvent(BaseHookEvent):
    hook_event_name: Literal["Setup"] = "Setup"
    trigger: str | None = None


class UnknownEvent(BaseHookEvent):
    """Fallback for hook event names we have no typed model for yet.

    We still capture everything: the raw payload is journaled before
    pydantic ever runs, and this model preserves all fields via
    ``extra="allow"``. The growth pipeline ignores unknown events but
    logs them at WARN so we notice schema drift.
    """

    hook_event_name: str  # not Literal -- anything goes


# Discriminated dispatch table. Keys are ``hook_event_name`` values.
EVENT_MODELS: dict[str, type[BaseHookEvent]] = {
    "SessionStart": SessionStartEvent,
    "SessionEnd": SessionEndEvent,
    "UserPromptSubmit": UserPromptSubmitEvent,
    "UserPromptExpansion": UserPromptExpansionEvent,
    "PreToolUse": PreToolUseEvent,
    "PostToolUse": PostToolUseEvent,
    "PostToolUseFailure": PostToolUseFailureEvent,
    "PostToolBatch": PostToolBatchEvent,
    "PermissionRequest": PermissionRequestEvent,
    "PermissionDenied": PermissionDeniedEvent,
    "Notification": NotificationEvent,
    "SubagentStart": SubagentStartEvent,
    "SubagentStop": SubagentStopEvent,
    "Stop": StopEvent,
    "StopFailure": StopFailureEvent,
    "PreCompact": PreCompactEvent,
    "PostCompact": PostCompactEvent,
    "CwdChanged": CwdChangedEvent,
    "FileChanged": FileChangedEvent,
    "TaskCreated": TaskCreatedEvent,
    "TaskCompleted": TaskCompletedEvent,
    "InstructionsLoaded": InstructionsLoadedEvent,
    "ConfigChange": ConfigChangeEvent,
    "WorktreeCreate": WorktreeCreateEvent,
    "WorktreeRemove": WorktreeRemoveEvent,
    "Elicitation": ElicitationEvent,
    "ElicitationResult": ElicitationResultEvent,
    "TeammateIdle": TeammateIdleEvent,
    "Setup": SetupEvent,
}


# Public union: every concrete subclass plus the fallback.
Event = (
    SessionStartEvent
    | SessionEndEvent
    | UserPromptSubmitEvent
    | UserPromptExpansionEvent
    | PreToolUseEvent
    | PostToolUseEvent
    | PostToolUseFailureEvent
    | PostToolBatchEvent
    | PermissionRequestEvent
    | PermissionDeniedEvent
    | NotificationEvent
    | SubagentStartEvent
    | SubagentStopEvent
    | StopEvent
    | StopFailureEvent
    | PreCompactEvent
    | PostCompactEvent
    | CwdChangedEvent
    | FileChangedEvent
    | TaskCreatedEvent
    | TaskCompletedEvent
    | InstructionsLoadedEvent
    | ConfigChangeEvent
    | WorktreeCreateEvent
    | WorktreeRemoveEvent
    | ElicitationEvent
    | ElicitationResultEvent
    | TeammateIdleEvent
    | SetupEvent
    | UnknownEvent
)


def parse_event(raw: dict[str, Any]) -> Event:
    """Dispatch a raw hook payload to the right typed model.

    Falls back to :class:`UnknownEvent` for any ``hook_event_name`` we
    don't recognise. This function never raises on unknown event names;
    it only raises on truly malformed payloads (missing
    ``session_id`` and ``hook_event_name``, etc.).

    Example:
        >>> ev = parse_event({"session_id": "s1", "hook_event_name": "Stop"})
        >>> isinstance(ev, StopEvent)
        True
    """
    name = raw.get("hook_event_name", "")
    cls = EVENT_MODELS.get(name, UnknownEvent)
    return cls.model_validate(raw)  # type: ignore[return-value]

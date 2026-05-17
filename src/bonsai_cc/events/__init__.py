"""Hook event ingestion: payload models, journaling, dispatch.

The pipeline (see ``DESIGN.md`` §1):

1. The Claude Code hook client appends one
   ``{"ts": ..., "raw": {...}}`` line to
   ``<home>/journals/<sid>.jsonl`` (fsync'd). This is the
   durability gate; the daemon is not in the path.
2. When the daemon is running, :class:`bonsai_cc.events.watcher.JournalWatcher`
   tails the directory and re-publishes each new line.
3. The raw dict is parsed via :func:`bonsai_cc.events.models.parse_event`.
4. The validated :class:`Event` is pushed onto
   :data:`bonsai_cc.events.bus.event_bus`. The growth engine
   consumes from there.

When the daemon is NOT running, events still land on disk via
step 1. On the next ``bonsai-cc`` launch, orphan-session
recovery picks them up.
"""

from bonsai_cc.events.bus import IngestedEvent, event_bus, get_event_bus
from bonsai_cc.events.journal import Journal, JournalRegistry, read_journal
from bonsai_cc.events.models import (
    Event,
    UnknownEvent,
    parse_event,
)
from bonsai_cc.events.watcher import JournalWatcher

__all__ = [
    "Event",
    "IngestedEvent",
    "Journal",
    "JournalRegistry",
    "JournalWatcher",
    "UnknownEvent",
    "event_bus",
    "get_event_bus",
    "parse_event",
    "read_journal",
]

"""The architectural seam: an asyncio.Queue of validated events.

Producers (ingest) push :class:`IngestedEvent` envelopes -- the
validated event plus its session-local index from the journal -- and
consumers (the growth engine) pull them off. Neither side knows
about the other; both know only about this queue. If the daemon ever splits out of the renderer process, the
in-process queue gets swapped for a queue-shaped IPC transport. The
producers and consumers don't change.

The seam is enforced two ways:

1. Module dependency: ``growth/`` imports only this module (and its
   own internals). It does **not** import from :mod:`bonsai_cc.ipc`
   or :mod:`bonsai_cc.events.ingest`. A test asserts this.
2. Type contract: the queue carries :class:`IngestedEvent`. The
   ``idx`` field is load-bearing for determinism (it seeds the
   per-event RNG in :func:`bonsai_cc.growth.apply.apply_event`).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bonsai_cc.events.models import Event

__all__ = [
    "EventBus",
    "IngestedEvent",
    "event_bus",
    "get_event_bus",
    "reset_event_bus_for_tests",
]


@dataclass(frozen=True, slots=True)
class IngestedEvent:
    """A validated event paired with its journal index and timestamp.

    * ``idx`` is the per-session counter the journal assigned at
      write time. Same idx -> same RNG -> same growth (determinism).
    * ``ts`` is the wall-clock UTC ms the hook wrote to the journal
      line. Consumers use it for session-duration accounting. Without
      it, the runner stamps state.started_at_ms with the *processing*
      time, which produces zero-duration garden rows for any orphan
      journal the watcher picks up later. Defaults to 0 so existing
      test stubs that build ``IngestedEvent(idx, event)`` keep
      working unchanged.
    """

    idx: int
    event: Event
    ts: int = 0


class EventBus:
    """A thin wrapper around :class:`asyncio.Queue` for ingested events.

    The wrapper exists so we can:

    * carry a stable type (no plain ``asyncio.Queue[Any]``);
    * add metrics later (queue depth, drop counters) without touching
      every call site;
    * swap the underlying transport in v2 by changing this class only.
    """

    def __init__(self, *, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[IngestedEvent] = asyncio.Queue(maxsize=maxsize)

    async def publish(self, ingested: IngestedEvent) -> None:
        """Place ``ingested`` on the queue. Awaits if the queue is full."""
        await self._queue.put(ingested)

    async def consume(self) -> IngestedEvent:
        """Pull the next envelope off the queue. Awaits if empty."""
        return await self._queue.get()

    def qsize(self) -> int:
        """Return the current queue depth (approximate; for metrics)."""
        return self._queue.qsize()


# Module-level singleton. Tests can swap it via :func:`reset_event_bus_for_tests`.
event_bus = EventBus()


def get_event_bus() -> EventBus:
    """Return the process-wide :class:`EventBus`.

    Indirect access keeps tests honest: nothing imports the singleton
    by reference in a way that would survive a reset.
    """
    return event_bus


def reset_event_bus_for_tests(*, maxsize: int = 0) -> EventBus:
    """Replace the singleton with a fresh bus. Test-only."""
    global event_bus
    event_bus = EventBus(maxsize=maxsize)
    return event_bus

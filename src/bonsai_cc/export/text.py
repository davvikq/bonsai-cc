"""Plain ASCII export.

Writes ``row.final_ascii`` verbatim to a path. If the row never
captured a snapshot (e.g. an old save), we re-project from the
serialised state instead. Either path produces the same bytes -- the
ASCII is regenerated from the same projection function the daemon
uses, so a missing snapshot is recoverable as long as the state JSON
or event log survives.
"""

from __future__ import annotations

from pathlib import Path

from bonsai_cc.garden.store import (
    DEFAULT_ASCII_HEIGHT,
    DEFAULT_ASCII_WIDTH,
    SessionRow,
    load_state_from_row,
    render_final_ascii,
)

__all__ = ["export_text"]


def export_text(
    row: SessionRow,
    out_path: Path,
    *,
    width: int = DEFAULT_ASCII_WIDTH,
    height: int = DEFAULT_ASCII_HEIGHT,
) -> Path:
    """Write the session's final ASCII to ``out_path``.

    Returns the resolved path written. Re-renders from the state
    JSON if ``row.final_ascii`` is missing; raises only if both are
    unrecoverable (and even then the caller can replay from
    ``event_log_path``).
    """
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = row.final_ascii
    if text is None:
        state = load_state_from_row(row)
        if state is None:
            from bonsai_cc.export import ExportError

            msg = (
                f"session {row.id} has neither cached ASCII nor a "
                "stored state — replay from the event log instead."
            )
            raise ExportError(msg)
        text = render_final_ascii(state, width=width, height=height)
    out_path.write_text(text, encoding="utf-8")
    return out_path

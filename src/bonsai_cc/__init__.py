"""bonsai-cc -- grow a bonsai during Claude Code sessions.

The full architecture is documented in ``DESIGN.md`` at the repo root.
Key entry points:

* :mod:`bonsai_cc.cli` -- Typer app exposing the ``bonsai-cc`` command.
* :mod:`bonsai_cc.events` -- hook payload models, journals, tail-watcher.
* :mod:`bonsai_cc.web` -- aiohttp web server + SSE + per-theme renderers.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]

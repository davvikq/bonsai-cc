"""Structured logging setup.

We use ``structlog`` rendered as JSONL into a daily file at
``<home>/logs/bonsai-cc-YYYY-MM-DD.log``. Production code paths never
call ``print``. Stderr is silent unless the caller passes
``verbose=True`` (mirrors WARN+) or ``debug=True`` (mirrors all).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from bonsai_cc.config import get_config

__all__ = ["get_logger", "setup_logging"]


_configured = False


def _log_file(logs_dir: Path) -> Path:
    """Return today's log file path. Date is UTC for consistency in
    multi-day sessions and across timezones."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return logs_dir / f"bonsai-cc-{stamp}.log"


def setup_logging(*, verbose: bool = False, debug: bool = False) -> None:
    """Initialize structlog + stdlib logging.

    Idempotent: subsequent calls reconfigure handlers (useful when
    ``--verbose`` is passed on a re-entry). Tests that want a clean
    slate can call :func:`reset_logging_for_tests`.
    """
    global _configured

    cfg = get_config()
    cfg.ensure_dirs()

    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    file_handler = logging.FileHandler(_log_file(cfg.logs_dir), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    handlers: list[logging.Handler] = [file_handler]
    if verbose or debug:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(level)
        stream.setFormatter(logging.Formatter("%(message)s"))
        handlers.append(stream)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(logging.DEBUG)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str | None = None, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger.

    On first use we lazily initialize a minimal config so library
    imports don't crash when the daemon hasn't called
    :func:`setup_logging` yet. The CLI entry point always calls
    :func:`setup_logging` explicitly.

    Example:
        >>> log = get_logger("bonsai_cc.events")
        >>> log.info("event_ingested", session_id="abc123")  # doctest: +SKIP
    """
    if not _configured:
        setup_logging()
    # structlog returns ``Any`` from get_logger; the BoundLogger return
    # type is for our callers' benefit (autocomplete, mypy).
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name, **initial_values)
    return logger


def reset_logging_for_tests() -> None:
    """Test-only: tear down handlers so the next ``setup_logging`` is
    a clean re-init."""
    global _configured
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    _configured = False

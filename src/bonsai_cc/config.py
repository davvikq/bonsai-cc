"""Runtime configuration: paths, environment overrides, defaults.

The single source of truth is :func:`get_config`, which returns a
frozen :class:`Config` dataclass. All other modules ask the config for
paths rather than building them by hand -- this keeps tests sandboxable
via ``BONSAI_CC_HOME``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

__all__ = ["Config", "get_config", "reset_config_cache"]


_ENV_HOME = "BONSAI_CC_HOME"
_ENV_DEBUG = "BONSAI_CC_DEBUG"


def _default_home() -> Path:
    """Return the default state directory for bonsai-cc.

    * If ``BONSAI_CC_HOME`` is set, use it verbatim. This is the test
      sandbox knob and also the user override.
    * On Windows, honor ``%LOCALAPPDATA%/bonsai-cc`` when set; otherwise
      fall back to ``%USERPROFILE%/.bonsai-cc``.
    * Elsewhere (Linux, macOS), use ``$HOME/.bonsai-cc``.
    """
    override = os.environ.get(_ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "bonsai-cc"
    return Path.home() / ".bonsai-cc"


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved paths and runtime flags.

    All ``Path`` attributes are *absolute* and pre-resolved. The
    directories they live in are *not* created here; callers must
    ``mkdir(parents=True, exist_ok=True)`` as needed. We keep the
    config side-effect-free so that simply importing it never touches
    the filesystem.
    """

    home: Path
    journals_dir: Path
    logs_dir: Path
    exports_dir: Path
    garden_db: Path
    pid_file: Path
    debug: bool

    def ensure_dirs(self) -> None:
        """Create the state directories if they do not exist.

        Idempotent. Called once at daemon startup; tests that need
        these paths to exist call it directly.
        """
        for d in (self.home, self.journals_dir, self.logs_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return the process-wide :class:`Config`.

    Cached: the first call materializes paths from the environment and
    every subsequent call returns the same object. Tests should call
    :func:`reset_config_cache` after mutating environment variables.

    Example:
        >>> cfg = get_config()
        >>> cfg.home.name
        '.bonsai-cc'
    """
    home = _default_home()
    return Config(
        home=home,
        journals_dir=home / "journals",
        logs_dir=home / "logs",
        exports_dir=home / "exports",
        garden_db=home / "garden.db",
        pid_file=home / "daemon.pid",
        debug=os.environ.get(_ENV_DEBUG, "") not in ("", "0", "false", "False"),
    )


def reset_config_cache() -> None:
    """Clear the :func:`get_config` LRU cache. Test-only."""
    get_config.cache_clear()

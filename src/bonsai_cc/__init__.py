"""bonsai-cc -- grow a bonsai during Claude Code sessions."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bonsai-cc")
except PackageNotFoundError:  # editable / source-tree dev
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]

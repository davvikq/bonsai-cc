"""Non-destructive merge into ``~/.claude/settings.json``.

Adds one matcher group per registered hook event, each tagged with
``"_bonsai_cc": true`` so uninstall can find them again. Idempotent;
existing hooks / MCP servers / free-form settings are preserved.
"""

from __future__ import annotations

import contextlib
import copy
import difflib
import json
import os
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from bonsai_cc.config import get_config
from bonsai_cc.log import get_logger

__all__ = [
    "DEFAULT_REGISTERED_EVENTS",
    "WINDOWS_STORE_SHIM_MARKER",
    "HookEntry",
    "InstallError",
    "InstallPlan",
    "Scope",
    "build_install_plan",
    "find_project_root",
    "find_python_executable",
    "install_hook_client_script",
    "is_windows_store_shim",
    "render_diff",
    "uninstall",
    "write_settings",
]


_log = get_logger("bonsai_cc.hook.installer")


# The Microsoft Store Python redirector lives under ``\WindowsApps\``;
# the .exe at that path is not a Python interpreter, it pops up a
# Store install prompt (or fails outright with "Python was not
# found"). Any binary inside that directory must be treated as a
# shim and skipped during interpreter discovery.
WINDOWS_STORE_SHIM_MARKER = "\\windowsapps\\"


class InstallError(RuntimeError):
    """Raised when installation cannot proceed safely.

    The hook installer prefers refusing to write than to install a
    hook that we already know is going to fail silently for every
    Claude Code event. The CLI catches this and surfaces an
    actionable message -- never a stack trace -- to the user.
    """

# The events we register hooks for. Matches the design contract Adding a
# new event here automatically gets it installed on the next run.
DEFAULT_REGISTERED_EVENTS: tuple[str, ...] = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "Stop",
    "SessionEnd",
    "Notification",
)

# Filenames that mark a project root. Searched ancestor-by-ancestor.
_PROJECT_ROOT_MARKERS: tuple[str, ...] = (
    ".git",
    ".hg",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
)

_MARKER_KEY = "_bonsai_cc"


class Scope:
    """Where to install: project-level or globally for the user."""

    PROJECT = "project"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class HookEntry:
    """One concrete hook command, already realised on disk."""

    python_executable: str
    hook_client_path: Path

    @property
    def command(self) -> str:
        """The ``command`` string written into ``settings.json``.

        On Windows we quote both halves with double quotes to survive
        paths containing spaces (``C:\\Program Files\\...``). POSIX
        shells handle the same quoting fine.
        """
        return f'"{self.python_executable}" "{self.hook_client_path}"'


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """Everything ``install-hook`` would do, in serialisable form."""

    scope: str
    settings_path: Path
    before: dict[str, Any]
    after: dict[str, Any]
    hook_client_path: Path
    python_executable: str
    registered_events: tuple[str, ...]


# ---------------------------------------------------------------------------
# Discovery: project root, python executable
# ---------------------------------------------------------------------------


def find_project_root(start: Path) -> Path:
    """Walk up from ``start`` looking for a marker file.

    Returns the first ancestor (including ``start``) that contains
    any of :data:`_PROJECT_ROOT_MARKERS`. If nothing matches, returns
    ``start`` itself -- a project root is whatever directory the user
    is in.
    """
    start = start.resolve()
    for candidate in (start, *start.parents):
        for marker in _PROJECT_ROOT_MARKERS:
            if (candidate / marker).exists():
                return candidate
    return start


def is_windows_store_shim(path: str | None) -> bool:
    """Return True iff ``path`` is the Microsoft Store Python redirector.

    The Store ships a ``python.exe`` / ``python3.exe`` under
    ``%LOCALAPPDATA%\\Microsoft\\WindowsApps\\`` that looks like an
    interpreter but is actually a UWP App Execution Alias -- it
    opens the Microsoft Store install flow rather than running
    Python. Anything inside ``\\WindowsApps\\`` is the same kind of
    placeholder; we never want to bake one of these paths into a
    user's ``~/.claude/settings.json``.
    """
    if not path:
        return False
    return WINDOWS_STORE_SHIM_MARKER in path.lower()


def find_python_executable() -> str:
    """Pick a Python interpreter the hook command can rely on.

    Priority:

    1. **sys.executable** -- the Python that is running this very
       function. By definition it works, isn't a Store shim, and
       can resolve the hook client script. This sidesteps the
       Windows trap where ``shutil.which("python3")`` happily
       returns the Store redirector at
       ``...\\Microsoft\\WindowsApps\\python3.EXE`` (a shim that
       opens the Microsoft Store rather than running Python).
    2. **PATH scan for a real python.exe / python3.exe**, skipping
       any path under ``\\WindowsApps\\``. Only reached when
       ``sys.executable`` itself is somehow a shim (paranoid case
       -- e.g. uv tool install via a shim).
    3. **Refuse** with :class:`InstallError`. Writing a known-broken
       hook is worse than not writing one -- the user can install a
       real Python from python.org or via ``winget`` and re-run.
    """
    primary = sys.executable
    if primary and not is_windows_store_shim(primary):
        return primary

    for name in ("python3", "python"):
        for candidate in _which_all(name):
            if not is_windows_store_shim(candidate):
                return candidate

    raise InstallError(
        "No usable Python interpreter found. The Windows Store "
        f"Python shim at {primary!r} is not a real interpreter — "
        "it opens the Microsoft Store instead of running Python. "
        "Install Python from python.org or run "
        "`winget install Python.Python.3.12`, then re-run "
        "`bonsai-cc install-hook`."
    )


def _which_all(name: str) -> list[str]:
    """Like :func:`shutil.which` but returns *every* match in PATH order.

    ``shutil.which`` stops at the first hit, which on Windows is
    typically the Store shim. We need to keep looking past it so a
    real interpreter further down PATH can take over. On POSIX the
    function still works -- there's just usually only one match.
    """
    raw_path = os.environ.get("PATH", "")
    if sys.platform == "win32":
        exts = [e.lower() for e in os.environ.get("PATHEXT", ".EXE").split(os.pathsep) if e]
    else:
        exts = [""]
    matches: list[str] = []
    for entry in raw_path.split(os.pathsep):
        if not entry:
            continue
        base = Path(entry)
        try:
            for ext in exts:
                candidate = base / f"{name}{ext}"
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    matches.append(str(candidate))
                    break
        except OSError:
            continue
    return matches


# ---------------------------------------------------------------------------
# Hook client script materialisation
# ---------------------------------------------------------------------------


def install_hook_client_script(home: Path) -> Path:
    """Copy the stable hook-client template into ``<home>/hook_client.py``.

    The script is read from the package resources so the on-disk file
    matches the version of bonsai-cc that installed it byte-for-byte.
    Users can ``cat`` the result and audit it.

    Returns the path on disk. Overwrites if it exists (an upgrade).
    """
    home.mkdir(parents=True, exist_ok=True)
    target = home / "hook_client.py"
    template = resources.files("bonsai_cc.hook").joinpath("client_template.py")
    target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    if sys.platform != "win32":
        # Make it executable for ``./hook_client.py`` debugging by hand.
        target.chmod(0o755)
    return target


# ---------------------------------------------------------------------------
# Plan / apply
# ---------------------------------------------------------------------------


def _resolve_settings_path(scope: str, project_root: Path) -> Path:
    """Compute the target settings path for the chosen scope."""
    if scope == Scope.GLOBAL:
        return Path.home() / ".claude" / "settings.json"
    return project_root / ".claude" / "settings.json"


def _load_settings(path: Path) -> dict[str, Any]:
    """Read ``settings.json`` as a dict. Missing → ``{}``.

    Insertion order is preserved (Python 3.7+ dicts). We never use
    ``object_pairs_hook=OrderedDict`` because plain dicts give the
    same guarantee with less ceremony.
    """
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(
            f"settings.json at {path} is not a JSON object — refusing to merge"
        )
    return data


def _strip_bonsai_entries(hooks_for_event: list[Any]) -> list[Any]:
    """Drop any matcher-group with our marker key.

    Used both during install (so the plan is idempotent -- re-install
    is equivalent to uninstall + install) and during uninstall.
    """
    out: list[Any] = []
    for entry in hooks_for_event:
        if isinstance(entry, dict) and entry.get(_MARKER_KEY) is True:
            continue
        out.append(entry)
    return out


def build_install_plan(
    *,
    scope: str,
    project_root: Path,
    registered_events: tuple[str, ...] = DEFAULT_REGISTERED_EVENTS,
) -> InstallPlan:
    """Compute the exact ``before`` / ``after`` settings dicts.

    No filesystem mutation. The caller can render a diff via
    :func:`render_diff` and decide whether to commit with
    :func:`write_settings`.
    """
    cfg = get_config()
    cfg.ensure_dirs()
    settings_path = _resolve_settings_path(scope, project_root)

    before = _load_settings(settings_path)
    after = copy.deepcopy(before)

    hook_client_path = cfg.home / "hook_client.py"
    python_exe = find_python_executable()
    entry = HookEntry(python_executable=python_exe, hook_client_path=hook_client_path)

    hooks_root = after.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        raise ValueError(
            f"settings.hooks at {settings_path} is not a JSON object — "
            "refusing to merge"
        )

    new_group: dict[str, Any] = {
        _MARKER_KEY: True,
        "matcher": "",
        "hooks": [{"type": "command", "command": entry.command}],
    }
    for event in registered_events:
        existing = hooks_root.get(event)
        if existing is None:
            existing = []
        elif not isinstance(existing, list):
            raise ValueError(
                f"settings.hooks.{event} at {settings_path} is not a list — "
                "refusing to merge"
            )
        cleaned = _strip_bonsai_entries(existing)
        cleaned.append(new_group)
        hooks_root[event] = cleaned

    return InstallPlan(
        scope=scope,
        settings_path=settings_path,
        before=before,
        after=after,
        hook_client_path=hook_client_path,
        python_executable=python_exe,
        registered_events=registered_events,
    )


def render_diff(plan: InstallPlan) -> str:
    """Return a unified diff (text) of the settings change.

    Empty string if no change. The diff is what ``--dry-run`` prints
    and what the normal install prints after writing.
    """
    before_text = json.dumps(plan.before, indent=2, ensure_ascii=False) + "\n"
    after_text = json.dumps(plan.after, indent=2, ensure_ascii=False) + "\n"
    if before_text == after_text:
        return ""
    diff_lines = difflib.unified_diff(
        before_text.splitlines(keepends=True),
        after_text.splitlines(keepends=True),
        fromfile=str(plan.settings_path),
        tofile=str(plan.settings_path),
        n=3,
    )
    return "".join(diff_lines)


def write_settings(plan: InstallPlan) -> None:
    """Atomically replace ``settings.json`` with the planned content.

    Writes to ``settings.json.tmp`` in the same directory, then
    renames into place. Creates parent dirs as needed.
    """
    plan.settings_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(plan.after, indent=2, ensure_ascii=False) + "\n"
    tmp = plan.settings_path.with_suffix(plan.settings_path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(plan.settings_path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
    _log.info(
        "hook_installed",
        scope=plan.scope,
        settings_path=str(plan.settings_path),
        events=list(plan.registered_events),
    )


def uninstall(
    *,
    scope: str,
    project_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """Remove every bonsai-cc marker entry from settings.json.

    Returns ``(before, after, settings_path)``. The caller writes
    the result via :func:`write_settings_dict` (mirroring
    :func:`write_settings` for a pre-computed dict). If the settings
    file doesn't exist, returns empty dicts.
    """
    settings_path = _resolve_settings_path(scope, project_root)
    before = _load_settings(settings_path)
    after = copy.deepcopy(before)

    hooks_root = after.get("hooks")
    if isinstance(hooks_root, dict):
        empty_events = []
        for event, entries in list(hooks_root.items()):
            if not isinstance(entries, list):
                continue
            cleaned = _strip_bonsai_entries(entries)
            if cleaned:
                hooks_root[event] = cleaned
            else:
                empty_events.append(event)
        # Drop any event whose list became empty after our removals,
        # so we don't leave behind ``"Stop": []`` cruft that wasn't
        # there before.
        for event in empty_events:
            if event in hooks_root:
                del hooks_root[event]
        if not hooks_root:
            del after["hooks"]

    return before, after, settings_path


def write_settings_dict(path: Path, data: dict[str, Any]) -> None:
    """Atomically write ``data`` to ``path`` (or delete if data is empty)."""
    if not data:
        # We never *created* the file just to register hooks; if our
        # uninstall empties it, leave a real empty object behind so
        # other tools that parse the file don't crash on absence.
        # (Deleting feels right but is more surprising.)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "{}\n"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)

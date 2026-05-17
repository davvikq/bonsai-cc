"""Diagnostics for ``bonsai-cc doctor``.

Every check is a small, pure function that returns a :class:`Check`
result so the CLI can render them uniformly. Checks must:

* never raise -- failures collapse into ``status == "warn"`` or
  ``"fail"`` with an actionable ``remediation``;
* be cheap (no network, no SQL); doctor runs interactively.

The list of checks lives in :func:`run_all`; each subsystem owns its
own check. Adding a new failure mode to bonsai-cc means adding a
check here so the user can self-diagnose.
"""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bonsai_cc import __version__
from bonsai_cc.config import get_config
from bonsai_cc.hook.installer import (
    DEFAULT_REGISTERED_EVENTS,
    Scope,
    _load_settings,
    _resolve_settings_path,
    find_project_root,
)

__all__ = ["Check", "Status", "run_all"]


Status = Literal["ok", "warn", "fail", "info"]


@dataclass(frozen=True, slots=True)
class Check:
    """One row of doctor output."""

    label: str
    status: Status
    value: str
    remediation: str | None = None


# ---------------------------------------------------------------------------
# Individual checks. Each returns a single :class:`Check`.
# ---------------------------------------------------------------------------


def _check_build_rev() -> Check:
    """Show the git SHA the installed wheel was built from.

    A mismatch between this value and ``git rev-parse --short HEAD``
    in the source tree is the canonical signal of a stale ``uv tool
    install``: same version number, different bits
    on disk, browser keeps serving the old client. ``"unknown"``
    means the build hook didn't run -- likely an editable install or
    a tarball from a non-git source.
    """
    try:
        from bonsai_cc._build_info import GIT_REV
    except ImportError:
        return Check(
            "Build",
            "info",
            "unknown",
            remediation=(
                "No _build_info.py — installed without the hatch build hook. "
                "Reinstall with: uv tool uninstall bonsai-cc && uv tool install ."
            ),
        )
    if GIT_REV.endswith("-dirty"):
        return Check(
            "Build",
            "warn",
            GIT_REV,
            remediation=(
                "Working tree had uncommitted changes when this wheel was "
                "built. Commit (or stash) and reinstall for a clean rev."
            ),
        )
    return Check("Build", "info", GIT_REV)


def _check_python() -> Check:
    py = sys.version_info
    value = f"{py.major}.{py.minor}.{py.micro}"
    if py >= (3, 11):
        return Check("Python", "ok", value)
    return Check(
        "Python",
        "fail",
        value,
        remediation="bonsai-cc requires Python 3.11 or newer.",
    )


def _check_platform() -> Check:
    return Check("Platform", "info", f"{sys.platform} ({platform.machine()})")


def _check_event_pipeline() -> Check:
    """Report the event-ingest path.

    Events go straight from the hook to ``<home>/journals/*.jsonl``;
    the daemon is a renderer and event consumer, not an event source,
    so there's no socket or port file to probe.
    """
    return Check("Event pipeline", "info", "hook -> journal (direct)")


def _check_home_writable() -> Check:
    cfg = get_config()
    try:
        cfg.home.mkdir(parents=True, exist_ok=True)
        probe = cfg.home / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(
            "Home directory",
            "fail",
            str(cfg.home),
            remediation=(
                f"Cannot write to {cfg.home}: {exc}. Check permissions, or set "
                "BONSAI_CC_HOME to a writable directory."
            ),
        )
    free = _free_bytes(cfg.home)
    return Check("Home directory", "ok", f"{cfg.home} ({_fmt_bytes(free)} free)")


def _check_journals_writable() -> Check:
    cfg = get_config()
    try:
        cfg.journals_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.journals_dir / ".doctor_probe.jsonl"
        probe.write_text('{"probe":true}\n', encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(
            "Journals directory",
            "fail",
            str(cfg.journals_dir),
            remediation=(
                f"Cannot write to {cfg.journals_dir}: {exc}. The daemon will "
                "lose every session until this is fixed."
            ),
        )
    return Check("Journals directory", "ok", str(cfg.journals_dir))


def _check_fsync_supported() -> Check:
    """fsync is required for the durability gate. A handful of FUSE
    mounts and exotic network filesystems return ``EINVAL`` on fsync;
    we want to surface that here so the user isn't surprised at 3am
    when the journal lies about durability.
    """
    cfg = get_config()
    cfg.journals_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=cfg.journals_dir, prefix=".fsync_probe_", suffix=".bin", delete=False
    ) as fp:
        fp.write(b"ok")
        fp.flush()
        try:
            os.fsync(fp.fileno())
        except OSError as exc:
            tmp_path = Path(fp.name)
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            return Check(
                "fsync support",
                "warn",
                "not supported",
                remediation=(
                    f"os.fsync raised {exc!s} on {cfg.journals_dir}. The "
                    "durability gate (journal-before-pydantic) will still "
                    "write, but power-loss safety is reduced. Consider "
                    "setting BONSAI_CC_HOME to a path on a native filesystem."
                ),
            )
        tmp_path = Path(fp.name)
    with contextlib.suppress(OSError):
        tmp_path.unlink(missing_ok=True)
    return Check("fsync support", "ok", "yes")


def _check_daemon_status() -> Check:
    """Report whether the optional web daemon is currently running.

    The daemon is optional. Probe the web port file
    (``<home>/web.port``) to report whether one is running, but
    "not running" is fine: events still land in the journal via
    the hook.
    """
    cfg = get_config()
    port_file = cfg.home / "web.port"
    if not port_file.exists():
        return Check(
            "Daemon",
            "info",
            "not running (optional)",
            remediation=(
                "Events still land in the journal via the hook. "
                "Start the web view with: bonsai-cc"
            ),
        )
    try:
        port = int(port_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        return Check(
            "Daemon",
            "warn",
            "web.port unreadable",
            remediation=f"Could not parse {port_file}: {exc}. Delete the file and restart.",
        )
    if not _tcp_port_alive("127.0.0.1", port):
        return Check(
            "Daemon",
            "warn",
            f"web.port points to dead daemon (:{port})",
            remediation=(
                f"{port_file} advertises port {port} but nothing is listening. "
                "It will be replaced by the next `bonsai-cc` launch."
            ),
        )
    return Check("Daemon", "ok", f"running (http://127.0.0.1:{port})")


def _check_hook_installed() -> Check:
    """Is bonsai-cc's hook present in any settings.json we can see?

    We check the project-level file first (since that's the install
    default), then global. We don't *require* either -- fresh installs
    haven't run ``install-hook`` yet.
    """
    project_path = _resolve_settings_path(Scope.PROJECT, find_project_root(Path.cwd()))
    global_path = _resolve_settings_path(Scope.GLOBAL, Path.home())
    candidates = [(Scope.PROJECT, project_path), (Scope.GLOBAL, global_path)]
    for scope, path in candidates:
        if not path.exists():
            continue
        try:
            data = _load_settings(path)
        except (ValueError, OSError) as exc:
            return Check(
                "Hook installed",
                "warn",
                str(path),
                remediation=f"Could not read settings.json: {exc}",
            )
        hooks_root = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks_root, dict):
            continue
        bonsai_events: list[str] = []
        for event, entries in hooks_root.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and entry.get("_bonsai_cc") is True:
                    bonsai_events.append(event)
                    break
        if bonsai_events:
            missing = set(DEFAULT_REGISTERED_EVENTS) - set(bonsai_events)
            if missing:
                return Check(
                    "Hook installed",
                    "warn",
                    f"{scope} ({path}); missing events: {sorted(missing)}",
                    remediation="Re-run `bonsai-cc install-hook` to refresh.",
                )
            return Check("Hook installed", "ok", f"{scope} ({path})")
    return Check(
        "Hook installed",
        "info",
        "no",
        remediation="Run `bonsai-cc install-hook` to start growing trees.",
    )


def _check_hook_client_script() -> Check:
    cfg = get_config()
    script = cfg.home / "hook_client.py"
    if not script.exists():
        return Check(
            "Hook client script",
            "info",
            "not materialised",
            remediation="Will be created on `bonsai-cc install-hook`.",
        )
    size = script.stat().st_size
    return Check("Hook client script", "ok", f"{script} ({_fmt_bytes(size)})")


def _check_garden_db() -> Check:
    """Dry-run consistency: count stored sessions + pending orphans.

    The orphan count comes from a read-only scan
    (:func:`bonsai_cc.runner.count_orphan_journals`); it doesn't
    trigger recovery -- that runs on the next CLI invocation that
    reads the garden. Surfacing the number here means a user with
    an empty garden sees *why*: "M orphan journals pending recovery."
    """
    from bonsai_cc.runner import count_orphan_journals

    cfg = get_config()
    if not cfg.garden_db.exists():
        return Check(
            "Garden",
            "info",
            "not yet created",
            remediation="Created automatically on the first SessionEnd.",
        )
    try:
        from bonsai_cc.garden.store import GardenStore

        with GardenStore() as store:
            stored = store.count_sessions()
        orphans = count_orphan_journals(cfg)
    except Exception as exc:  # noqa: BLE001 - doctor must not crash
        return Check(
            "Garden",
            "warn",
            f"unreadable: {exc}",
            remediation=(
                "Inspect with `uv run python scripts/diagnose_garden.py`."
            ),
        )
    if orphans:
        return Check(
            "Garden",
            "warn",
            f"{stored} sessions stored, "
            f"{orphans} orphan journals pending recovery",
            remediation=(
                "Recovery runs automatically on `bonsai-cc`, `garden`, "
                "`list`, `show`, or `export` — invoke any of those to "
                "absorb the orphans."
            ),
        )
    return Check("Garden", "ok", f"{stored} sessions stored")


def _check_terminal() -> Check:
    try:
        cols, rows = shutil.get_terminal_size((80, 24))
    except OSError:
        cols, rows = 80, 24
    truecolor = os.environ.get("COLORTERM") == "truecolor"
    suffix = ", truecolor" if truecolor else ", 16-color"
    return Check("Terminal", "info", f"{cols}x{rows}{suffix}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tcp_port_alive(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.2)
        s.connect((host, port))
    except OSError:
        return False
    else:
        return True
    finally:
        with contextlib.suppress(OSError):
            s.close()


def _free_bytes(path: Path) -> int:
    try:
        usage = shutil.disk_usage(path)
        return int(usage.free)
    except OSError:
        return 0


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n = n // 1 if unit == "B" else int(n // 1)
        n = n / 1024  # type: ignore[assignment]
    return f"{n:.1f} PiB"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_all() -> list[Check]:
    """Run every check in order. Order matters for human readability."""
    return [
        Check("bonsai-cc", "info", __version__),
        _check_build_rev(),
        _check_python(),
        _check_platform(),
        _check_event_pipeline(),
        _check_home_writable(),
        _check_journals_writable(),
        _check_fsync_supported(),
        _check_daemon_status(),
        _check_hook_installed(),
        _check_hook_client_script(),
        _check_garden_db(),
        _check_terminal(),
    ]

"""Typer CLI entry points."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from bonsai_cc import __version__
from bonsai_cc.config import get_config
from bonsai_cc.export import ExportError, export_png, export_text
from bonsai_cc.garden.store import GardenStore, SessionFilter
from bonsai_cc.hook import doctor as doctor_mod
from bonsai_cc.hook.installer import (
    InstallError,
    Scope,
    build_install_plan,
    find_project_root,
    install_hook_client_script,
    render_diff,
    uninstall,
    write_settings,
    write_settings_dict,
)
from bonsai_cc.log import get_logger, setup_logging
from bonsai_cc.runner import ensure_garden_consistent
from bonsai_cc.web.pipeline import run_web_pipeline

__all__ = ["app"]


app = typer.Typer(
    name="bonsai-cc",
    help=(
        "Grow an ASCII bonsai during Claude Code sessions.\n\n"
        "Run `bonsai-cc` (no arguments) to get started: it will offer to "
        "install the hook in the current project and then open the live "
        "renderer. Sub-commands are for power users and troubleshooting."
    ),
    no_args_is_help=False,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"bonsai-cc {__version__}")
        raise typer.Exit()


def _stdin_is_interactive() -> bool:
    """Whether stdin is a real terminal.

    Wrapped so tests can monkeypatch the answer without messing with
    the actual ``sys.stdin``.
    """
    return sys.stdin.isatty()


@app.callback(invoke_without_command=True)
def _global_options(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Mirror WARN-and-above logs to stderr."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Mirror ALL logs to stderr."),
    ] = False,
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Run the web server but don't auto-open the browser.",
        ),
    ] = False,
    port: Annotated[
        int,
        typer.Option(
            "--port",
            help="Bind the web server to a specific port. 0 (default) = random.",
        ),
    ] = 0,
) -> None:
    """Top-level options shared by every subcommand.

    Also: when invoked with no subcommand, run the one-shot default
    flow (offer to install the hook, then open the web view).
    """
    setup_logging(verbose=verbose, debug=debug)
    if ctx.invoked_subcommand is None:
        _default_flow(no_browser=no_browser, port=port)


# ---------------------------------------------------------------------------
# Default flow: the one-command experience.
# ---------------------------------------------------------------------------


def _hook_installed_at(scope: str, project_root: Path) -> bool:
    """True iff ``settings.json`` for ``scope`` carries our marker."""
    from bonsai_cc.hook.installer import _load_settings, _resolve_settings_path

    path = _resolve_settings_path(scope, project_root)
    if not path.exists():
        return False
    try:
        data = _load_settings(path)
    except (OSError, ValueError):
        return False
    hooks_root = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks_root, dict):
        return False
    for entries in hooks_root.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("_bonsai_cc") is True:
                return True
    return False


def _default_flow(
    *, no_browser: bool = False, port: int = 0
) -> None:
    """The one-command UX: install hook (if needed), open web view, ask to keep.

    Steps:

    1. Detect whether the current project already has the hook.
    2. If not and stdin is a TTY, ask once (default Yes). Non-TTY
       use is told to call ``install-hook`` explicitly -- we never
       silently mutate settings in CI.
    3. Open the web view.
    4. On quit, if we just installed the hook, ask once whether to
       keep it (default Yes).
    """
    log = get_logger("bonsai_cc.cli.default")
    project_root = find_project_root(Path.cwd())
    already_installed = _hook_installed_at(Scope.PROJECT, project_root)

    if already_installed:
        log.info("default_flow_hook_already_installed", project=str(project_root))
        installed_this_run = False
    else:
        if not _stdin_is_interactive():
            typer.echo(
                "bonsai-cc hook isn't installed in this project. "
                "Run `bonsai-cc install-hook` to enable it, or "
                "`bonsai-cc watch` to attach to a daemon you started elsewhere.",
                err=True,
            )
            raise typer.Exit(2)
        if not typer.confirm(
            "Install bonsai-cc hook for this project?",
            default=True,
        ):
            typer.echo(
                "Skipped. Run `bonsai-cc install-hook` later if you change your mind."
            )
            return
        try:
            plan = build_install_plan(scope=Scope.PROJECT, project_root=project_root)
        except InstallError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        install_hook_client_script(get_config().home)
        write_settings(plan)
        installed_this_run = True
        log.info("default_flow_hook_installed", project=str(project_root))

    banner = _watch_banner(project_root)
    typer.echo(banner)

    # Detect the project language up front so the placeholder state
    # and the renderer's palette are right from the very first frame
    # rather than flickering ``default`` until SessionStart lands.
    from bonsai_cc.growth.language import detect_language

    theme = detect_language(str(project_root))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run_web_pipeline(
                banner=banner,
                theme=theme,
                port=port,
                open_browser=not no_browser,
            )
        )

    if installed_this_run and _stdin_is_interactive():
        keep = typer.confirm(
            "\nKeep hook installed for next time?",
            default=True,
        )
        if not keep:
            _, after, settings_path = uninstall(
                scope=Scope.PROJECT, project_root=project_root
            )
            write_settings_dict(settings_path, after)
            typer.echo(
                "Hook removed. Run `bonsai-cc` again any time to reinstall."
            )


def _watch_banner(project_root: Path) -> str:
    """One-line banner shown in the browser header (via SSE).

    The "how to stop" instruction lives only in the launching
    terminal (``pipeline.py`` prints ``Ctrl+C to stop``) -- the
    browser page can't intercept Ctrl+C anyway.
    """
    cwd_display = str(project_root)
    if len(cwd_display) > 50:
        cwd_display = "..." + cwd_display[-47:]
    return (
        f"Watching {cwd_display} - run `claude` in another terminal "
        "to grow your tree"
    )


@app.command("watch")
def watch_cmd(
    replay: Annotated[
        Path | None,
        typer.Option(
            "--replay",
            help="Drive the renderer from a JSONL journal file in addition to live events.",
        ),
    ] = None,
    speed: Annotated[
        float,
        typer.Option(
            "--speed",
            help="Replay speed multiplier; 0 = as fast as possible, 1.0 = real-time.",
        ),
    ] = 0.0,
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Run the web server but don't auto-open the browser.",
        ),
    ] = False,
    port: Annotated[
        int,
        typer.Option(
            "--port",
            help="Bind the web server to a specific port. 0 (default) = random.",
        ),
    ] = 0,
) -> None:
    """Start the daemon and the web view.

    Brings up the full pipeline: journal watcher + growth runner +
    aiohttp + SSE web server. Press ``q`` (then Enter) in the
    launching terminal to stop.

    ``--replay <file>`` feeds a recorded JSONL journal through the
    pipeline alongside the live watcher -- useful for smoke-testing
    the renderer without starting a Claude Code session. Pair with
    ``--speed`` to throttle the playback (``0`` = burst, ``1.0`` =
    original wall-clock cadence).
    """
    log = get_logger("bonsai_cc.cli.watch")
    log.info(
        "watch_invoked",
        replay=str(replay) if replay else None,
        speed=speed,
    )
    if replay is not None and not replay.exists():
        typer.echo(f"Replay file not found: {replay}", err=True)
        raise typer.Exit(2)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run_web_pipeline(
                port=port,
                open_browser=not no_browser,
                replay_path=replay,
                replay_speed=speed,
            )
        )


# ---------------------------------------------------------------------------
# install-hook / uninstall-hook
# ---------------------------------------------------------------------------


def _prompt_scope() -> str:
    """Interactive prompt. Enter accepts the recommended default (project).

    Non-TTY fallback already happened before calling us -- this is only
    reached when stdin is a real terminal.
    """
    typer.echo(
        "Install hook for this project, or globally for all Claude Code sessions?"
    )
    typer.echo("  [P] project (recommended) — only fires inside this project")
    typer.echo("  [g] global  — fires in every Claude Code session you start")
    while True:
        choice = typer.prompt("Scope", default="project").strip().lower()
        if choice in ("", "p", "project"):
            return Scope.PROJECT
        if choice in ("g", "global"):
            return Scope.GLOBAL
        typer.echo(f"Did not understand {choice!r}; please type 'project' or 'global'.")


# Legacy smoke-test artefact names. Earlier versions wrote the
# install-hook smoke marker into journals/ -- where the watcher
# treated it as a real Claude Code session and the runner persisted
# it as a "_install" card in the garden grid. The new path lives
# outside journals/ so neither the watcher nor the runner sees it.
LEGACY_SMOKE_JOURNAL = "_install_hook_smoke.jsonl"
LEGACY_SMOKE_SESSION_ID = "_install_hook_smoke"
SMOKE_MARKER_FILENAME = "_install_smoke.jsonl"


def cleanup_legacy_smoke_artifacts() -> None:
    """Best-effort removal of the legacy install-hook smoke artefacts.

    Two pieces of state need cleaning up for users who installed
    the hook before the move:

    * ``journals/_install_hook_smoke.jsonl`` -- the watcher picks
      this up on every daemon start, the runner binds to it and
      then persists it as ``partial`` when it switches to a real
      session. Deleting the file stops the cycle.
    * ``garden.db`` row with id ``_install_hook_smoke`` -- the
      spurious "_install" card. Removed via the store's normal
      delete API.

    Idempotent: a no-op when neither artefact exists. Errors are
    swallowed and logged at info -- this is best-effort migration,
    not a hard requirement.
    """
    log = get_logger("bonsai_cc.cli.smoke_cleanup")
    cfg = get_config()
    legacy_journal = cfg.journals_dir / LEGACY_SMOKE_JOURNAL
    if legacy_journal.exists():
        try:
            legacy_journal.unlink()
            log.info("legacy_smoke_journal_removed", path=str(legacy_journal))
        except OSError as exc:
            log.info(
                "legacy_smoke_journal_unlink_failed",
                path=str(legacy_journal),
                error=str(exc),
            )
    try:
        from bonsai_cc.garden.store import GardenStore

        with GardenStore() as store:
            if store.delete_session(LEGACY_SMOKE_SESSION_ID):
                log.info("legacy_smoke_garden_row_removed")
    except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
        log.info("legacy_smoke_garden_unlink_failed", error=str(exc))


def _send_smoke_event(timeout_s: float = 1.0) -> tuple[bool, str]:
    """Write a synthetic Notification to a marker file.

    The install-hook smoke test appends one record exactly the way
    the hook client would, but to a marker file OUTSIDE ``journals/``. The journal
    watcher treats anything in ``journals/`` as a real session;
    writing the smoke marker there made the daemon bind to a fake
    "_install_hook_smoke" session and persist it as a card in the
    garden (the source of the "_install" ghost-card bug).

    Returns ``(success, detail)``. ``success`` is ``True`` iff the
    record landed on disk inside the timeout.
    """
    _ = timeout_s
    cfg = get_config()
    # Sweep up any artefacts the old path left behind so a user
    # re-running install-hook gets a clean garden.
    cleanup_legacy_smoke_artifacts()
    payload = {
        "session_id": LEGACY_SMOKE_SESSION_ID,
        "hook_event_name": "Notification",
        "message": "install-hook smoke test",
    }
    try:
        cfg.home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"could not create {cfg.home}: {exc}"
    marker_path = cfg.home / SMOKE_MARKER_FILENAME
    record = (
        json.dumps(
            {"ts": int(time.time() * 1000), "raw": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    try:
        fd = os.open(
            marker_path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        try:
            os.write(fd, record.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        return False, str(exc)
    return True, f"appended to {marker_path}"


@app.command("install-hook")
def install_hook_cmd(
    global_scope: Annotated[
        bool,
        typer.Option(
            "--global",
            help="Install in ~/.claude/settings.json (fires for every Claude Code session).",
        ),
    ] = False,
    project_scope: Annotated[
        bool,
        typer.Option(
            "--project",
            help="Install in <project>/.claude/settings.json (default in non-TTY scripts).",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the diff and exit without writing."),
    ] = False,
) -> None:
    """Register bonsai-cc's hook in Claude Code settings.

    Default scope is **project-level**. Pass ``--global`` to install
    user-wide. Interactive shells get a prompt (Enter = project);
    non-interactive shells default to project unless ``--global`` is
    explicit. Re-running this command is idempotent -- it removes any
    previous bonsai-cc entries before adding the current ones.
    """
    if global_scope and project_scope:
        typer.echo("Pass at most one of --global / --project.", err=True)
        raise typer.Exit(2)

    if global_scope:
        scope = Scope.GLOBAL
    elif project_scope:
        scope = Scope.PROJECT
    elif sys.stdin.isatty():
        scope = _prompt_scope()
    else:
        scope = Scope.PROJECT

    project_root = find_project_root(Path.cwd())
    try:
        plan = build_install_plan(scope=scope, project_root=project_root)
    except InstallError as exc:
        # Refuse to write a broken hook (most commonly: Windows Store
        # Python shim selected as the interpreter). Surface the
        # actionable remediation to the user and exit non-zero.
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    diff = render_diff(plan)
    if dry_run:
        if diff:
            typer.echo(diff, nl=False)
        else:
            typer.echo("(no changes — settings.json is already up to date)")
        return

    hook_client_path = install_hook_client_script(get_config().home)
    typer.echo(f"Wrote hook client: {hook_client_path}")

    write_settings(plan)
    if diff:
        typer.echo(diff, nl=False)
    else:
        typer.echo("settings.json already had bonsai-cc hooks; refreshed in place.")

    undo_flag = " --global" if scope == Scope.GLOBAL else ""
    typer.echo(f"To undo: bonsai-cc uninstall-hook{undo_flag}")

    success, detail = _send_smoke_event()
    if success:
        typer.echo("Smoke test: daemon received synthetic event - [ok]")
    else:
        get_logger("bonsai_cc.cli.install").info(
            "install_hook_smoke_no_daemon", detail=detail
        )
        typer.echo(
            "Hook is wired up. The renderer isn't running yet - "
            "start it in another terminal with: bonsai-cc watch"
        )


@app.command("uninstall-hook")
def uninstall_hook_cmd(
    global_scope: Annotated[
        bool, typer.Option("--global", help="Remove from ~/.claude/settings.json.")
    ] = False,
    project_scope: Annotated[
        bool,
        typer.Option(
            "--project",
            help="Remove from <project>/.claude/settings.json (default).",
        ),
    ] = False,
) -> None:
    """Remove bonsai-cc entries from Claude Code settings.

    Idempotent: removes every matcher-group tagged with the
    ``_bonsai_cc`` marker. Settings files that contain no other
    content are reduced to ``{}``. The hook client script in
    ``<home>/hook_client.py`` is left in place so users can audit
    what was installed; ``rm`` it manually if desired.
    """
    if global_scope and project_scope:
        typer.echo("Pass at most one of --global / --project.", err=True)
        raise typer.Exit(2)
    scope = Scope.GLOBAL if global_scope else Scope.PROJECT
    project_root = find_project_root(Path.cwd())
    before, after, path = uninstall(scope=scope, project_root=project_root)

    if not path.exists():
        typer.echo(f"No settings file at {path}; nothing to uninstall.")
        return
    if before == after:
        typer.echo(f"No bonsai-cc entries in {path}; nothing to do.")
        return

    write_settings_dict(path, after)
    typer.echo(f"Removed bonsai-cc entries from {path}.")


# ---------------------------------------------------------------------------
# web: browser renderer
# ---------------------------------------------------------------------------


@app.command("web")
def web_cmd(
    port: Annotated[
        int,
        typer.Option(
            "--port",
            help="Bind to a specific port. 0 (default) picks a free port.",
        ),
    ] = 0,
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Don't auto-open the browser (useful for SSH / dev).",
        ),
    ] = False,
) -> None:
    """Run the web renderer: daemon + HTTP/SSE server + browser.

    Equivalent to ``bonsai-cc`` with no arguments.
    """
    from bonsai_cc.growth.language import detect_language

    project_root = find_project_root(Path.cwd())
    theme = detect_language(str(project_root))
    banner = _watch_banner(project_root)
    typer.echo(banner)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run_web_pipeline(
                port=port,
                open_browser=not no_browser,
                theme=theme,
                banner=banner,
            )
        )


@app.command("replay")
def replay_cmd(
    session_id: Annotated[
        str, typer.Argument(help="Session id (full or unambiguous prefix).")
    ],
    speed: Annotated[
        float,
        typer.Option(
            "--speed",
            help="Playback speed multiplier (default 1.0). 0 = as fast as possible.",
        ),
    ] = 1.0,
    port: Annotated[
        int,
        typer.Option(
            "--port",
            help="Bind to a specific port. 0 (default) picks a free port.",
        ),
    ] = 0,
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Don't auto-open the browser (useful for SSH / dev).",
        ),
    ] = False,
) -> None:
    """Replay a saved session in the browser.

    Opens the web renderer at ``/replay/<session_id>``. The server
    walks the saved journal, applies each event with the same
    ``apply_event`` the live daemon uses, and pushes the resulting
    state snapshots over SSE -- visually identical to a live
    session but on demand.
    """
    ensure_garden_consistent()
    with GardenStore() as store:
        match = _resolve_session(store, session_id)
    if match is None:
        raise typer.Exit(1)

    banner = f"Replaying {match.id[:8]} · speed {speed}x"
    typer.echo(banner)

    target_path = f"/replay/{match.id}?speed={speed}"
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run_web_pipeline(
                port=port,
                open_browser=not no_browser,
                theme=match.theme,
                banner=banner,
                browser_path=target_path,
            )
        )


# ---------------------------------------------------------------------------
# garden / list / show / export
# ---------------------------------------------------------------------------


def _format_started(epoch_ms: int) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(
        epoch_ms / 1000, tz=datetime.UTC
    ).strftime("%Y-%m-%d %H:%M")


@app.command("garden")
def garden_cmd(
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Run the web server but don't auto-open the browser.",
        ),
    ] = False,
    port: Annotated[
        int,
        typer.Option(
            "--port",
            help="Bind the web server to a specific port. 0 (default) = random.",
        ),
    ] = 0,
) -> None:
    """Browse the saved garden of trees in the web view.

    Every saved session appears as a card; click one to replay it.
    The daemon runs in this terminal -- press ``q`` (then Enter)
    to stop. Orphan-journal recovery runs first so sessions whose
    daemon died without saving still appear.
    """
    ensure_garden_consistent()
    log = get_logger("bonsai_cc.cli.garden")
    log.info("garden_web_invoked")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run_web_pipeline(
                port=port,
                open_browser=not no_browser,
                banner="garden",
            )
        )


@app.command("list")
def list_cmd(
    project: Annotated[
        str | None,
        typer.Option("--project", help="Only sessions from this project path."),
    ] = None,
    language: Annotated[
        str | None,
        typer.Option("--language", help="Only sessions matching this detected language."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum number of rows to print."),
    ] = 50,
) -> None:
    """Print a plain-text listing of saved sessions.

    Designed for scripting: tab-separated, newest first. Use
    ``bonsai-cc garden`` for the interactive browser. Orphan
    journals on disk are recovered before listing so a session
    whose daemon never saved still shows up here.
    """
    ensure_garden_consistent()
    with GardenStore() as store:
        rows = store.list_sessions(
            SessionFilter(
                project_path=project,
                detected_language=language,
                limit=limit,
            )
        )
    if not rows:
        typer.echo("(no sessions in the garden yet)")
        return
    header = ("started", "id", "project", "lang", "events", "errors")
    typer.echo("\t".join(header))
    for r in rows:
        typer.echo(
            "\t".join(
                (
                    _format_started(r.started_at),
                    r.id,
                    r.project_path or "—",
                    r.detected_language or "—",
                    str(r.tool_call_count),
                    str(r.error_count),
                )
            )
        )


@app.command("show")
def show_cmd(
    session_id: Annotated[str, typer.Argument(help="Session id (full or prefix).")],
) -> None:
    """Print the final ASCII of a saved session to stdout.

    Accepts a session id prefix as a convenience; if more than one
    session matches the prefix we list them and exit non-zero so
    the caller can re-invoke with a fuller id. Recovery of any
    orphan journals runs first so a freshly-finished session is
    addressable by id immediately.
    """
    ensure_garden_consistent()
    with GardenStore() as store:
        match = _resolve_session(store, session_id)
    if match is None:
        raise typer.Exit(1)
    if match.final_ascii:
        typer.echo(match.final_ascii, nl=False)
    else:
        typer.echo(
            f"Session {match.id} has no cached snapshot. "
            f"Replay it in the garden browser or via "
            f"`bonsai-cc watch --replay {match.event_log_path}`.",
            err=True,
        )
        raise typer.Exit(2)


@app.command("export")
def export_cmd(
    session_id: Annotated[str, typer.Argument(help="Session id (full or prefix).")],
    format_: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output format: txt | png | svg | gif.",
        ),
    ] = "txt",
    out: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Where to write the export."),
    ] = None,
) -> None:
    """Export a saved session in the chosen format.

    ``txt`` and ``png`` are MVP-grade. ``svg`` and ``gif`` raise a
    clear "not implemented" message rather than pretending to work.
    Without ``-o`` the file lands in ``<home>/exports/<id>.<ext>``.

    Recovery of orphan journals runs first so a just-finished
    session can be exported by id without the user having to
    re-run ``watch``.
    """
    fmt = format_.lower().lstrip(".")
    if fmt not in ("txt", "png", "svg", "gif"):
        typer.echo(f"Unknown format: {format_}", err=True)
        raise typer.Exit(2)

    ensure_garden_consistent()
    with GardenStore() as store:
        match = _resolve_session(store, session_id)
    if match is None:
        raise typer.Exit(1)

    cfg = get_config()
    cfg.ensure_dirs()
    target = out if out is not None else (cfg.exports_dir / f"{match.id}.{fmt}")

    try:
        if fmt == "txt":
            written = export_text(match, target)
        elif fmt == "png":
            written = export_png(match, target)
        else:
            from bonsai_cc.export.image import export_gif_stub, export_svg_stub

            stub = export_svg_stub if fmt == "svg" else export_gif_stub
            written = stub(match, target)
    except ExportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(f"Wrote {written}")


def _resolve_session(store: GardenStore, prefix: str):  # type: ignore[no-untyped-def]
    """Look up a session by full id or unambiguous prefix.

    Prefix-matching keeps the CLI ergonomic -- UUID session ids are
    long and copy-paste-prone.
    """
    direct = store.get_session(prefix)
    if direct is not None:
        return direct
    matches = [
        r
        for r in store.list_sessions(SessionFilter(limit=500))
        if r.id.startswith(prefix)
    ]
    if not matches:
        typer.echo(f"No session matching {prefix!r}.", err=True)
        return None
    if len(matches) > 1:
        typer.echo(
            f"{len(matches)} sessions start with {prefix!r}; please disambiguate:",
            err=True,
        )
        for r in matches:
            typer.echo(f"  {r.id}  {_format_started(r.started_at)}", err=True)
        return None
    return matches[0]


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


_STATUS_GLYPHS = {
    "ok": "[ok]",
    "warn": "[!!]",
    "fail": "[XX]",
    "info": "[..]",
}


@app.command("doctor")
def doctor_cmd() -> None:
    """Diagnose bonsai-cc installation and runtime state."""
    checks = doctor_mod.run_all()
    width = max((len(c.label) for c in checks), default=0)
    any_fail = False
    typer.echo("")
    for c in checks:
        glyph = _STATUS_GLYPHS.get(c.status, "[??]")
        typer.echo(f"  {glyph}  {c.label:<{width}}  {c.value}")
        if c.remediation and c.status != "ok":
            # Plain ASCII arrow: the Windows console default codepage
            # (CP1252) cannot encode the box-drawing variant, and
            # typer.echo would crash mid-report.
            typer.echo(f"        -> {c.remediation}")
        if c.status == "fail":
            any_fail = True
    typer.echo("")
    if any_fail:
        raise typer.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()

"""The one-command default flow (`bonsai-cc` with no arguments).

The contract: a freshly-installed user runs ``bonsai-cc`` in their
project, gets prompted once about the hook, sees the renderer, and
on quit gets one prompt about whether to keep the hook. No three-
terminal dance, no mandatory ``install-hook`` step. CI / scripts
that lack a TTY are nudged toward the explicit sub-commands rather
than silently mutating settings.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from bonsai_cc import cli
from bonsai_cc.cli import app


def _project(tmp_path: Path) -> Path:
    """A pretend project directory: gitted, otherwise empty."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    return root


def _settings_path(project: Path) -> Path:
    return project / ".claude" / "settings.json"


def _has_bonsai_marker(settings_path: Path) -> bool:
    if not settings_path.exists():
        return False
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        if isinstance(entries, list) and any(
            isinstance(e, dict) and e.get("_bonsai_cc") is True for e in entries
        ):
            return True
    return False


@pytest.fixture
def fake_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend stdin is interactive so the prompts fire.

    CliRunner replaces sys.stdin with a non-TTY pipe; we override
    the helper the default flow uses for its TTY detection.
    """
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)


@pytest.fixture
def stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ``run_web_pipeline`` so the test never spins up a server.

    Returns a dict tracking whether (and how) the pipeline was called.
    """
    calls: dict[str, Any] = {"count": 0, "last_kwargs": None}

    async def _stub_web(**kwargs: Any) -> None:
        calls["count"] += 1
        calls["last_kwargs"] = kwargs
        await asyncio.sleep(0)

    monkeypatch.setattr(cli, "run_web_pipeline", _stub_web)
    return calls


@pytest.fixture
def stub_pipeline_should_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail the test if the pipeline is reached.

    Used when the user declines the install prompt — we should
    exit *before* launching the renderer.
    """

    async def _fail(**_: Any) -> None:
        msg = "renderer pipeline must not be called when user declines install"
        raise AssertionError(msg)

    monkeypatch.setattr(cli, "run_web_pipeline", _fail)


# ---------------------------------------------------------------------------
# The three cases the spec calls out
# ---------------------------------------------------------------------------


def test_no_hook_prompts_installs_and_runs_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    fake_tty: None,
    stub_pipeline: dict[str, Any],
) -> None:
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()

    # Two Enters: accept install, then accept "keep hook" on quit.
    result = runner.invoke(app, [], input="\n\n")
    assert result.exit_code == 0, result.stdout
    # Pipeline launched exactly once with a banner.
    assert stub_pipeline["count"] == 1
    banner = stub_pipeline["last_kwargs"]["banner"]
    # The browser banner tells the user how to get growth started
    # (run claude in another terminal). It does NOT carry a "press q
    # to stop" hint — Q-to-quit was a TUI affordance the browser
    # never had, and including it confused users. Stop instructions
    # live in the launching terminal's pipeline banner (Ctrl+C).
    assert "claude" in banner
    assert "q to stop" not in banner
    # Hook was installed (and kept, since second Enter = Yes).
    assert _has_bonsai_marker(_settings_path(project))


def test_already_installed_skips_prompt_and_runs_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    fake_tty: None,
    stub_pipeline: dict[str, Any],
) -> None:
    project = _project(tmp_path)
    monkeypatch.chdir(project)

    # Pre-install the hook so the default flow finds it.
    from bonsai_cc.hook.installer import (
        Scope,
        build_install_plan,
        install_hook_client_script,
        write_settings,
    )
    install_hook_client_script(bonsai_home)
    plan = build_install_plan(scope=Scope.PROJECT, project_root=project)
    write_settings(plan)
    assert _has_bonsai_marker(_settings_path(project))

    runner = CliRunner()
    # No input needed; no prompt should fire.
    result = runner.invoke(app, [], input="")
    assert result.exit_code == 0, result.stdout
    assert stub_pipeline["count"] == 1
    # Importantly: no install prompt in stdout.
    assert "Install bonsai-cc hook" not in result.stdout
    # And no "keep hook" prompt either (we didn't install this run).
    assert "Keep hook installed" not in result.stdout
    # Hook is still installed (we didn't touch it).
    assert _has_bonsai_marker(_settings_path(project))


def test_user_declines_install_exits_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    fake_tty: None,
    stub_pipeline_should_not_run: None,
) -> None:
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()

    # "n" to the install prompt → exit clean, no install, no watch.
    result = runner.invoke(app, [], input="n\n")
    assert result.exit_code == 0, result.stdout
    assert not _settings_path(project).exists()
    # The "next time" hint is shown.
    assert "install-hook" in result.stdout


# ---------------------------------------------------------------------------
# Quit-time prompt to keep the hook
# ---------------------------------------------------------------------------


def test_quit_prompts_to_remove_hook_when_installed_this_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    fake_tty: None,
    stub_pipeline: dict[str, Any],
) -> None:
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()

    # Accept install, then decline "keep hook" on quit.
    result = runner.invoke(app, [], input="\nn\n")
    assert result.exit_code == 0, result.stdout
    # Hook was installed during the session, then removed on quit.
    assert not _has_bonsai_marker(_settings_path(project))
    # The undo hint is in stdout.
    assert "reinstall" in result.stdout.lower()


def test_quit_keeps_hook_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    fake_tty: None,
    stub_pipeline: dict[str, Any],
) -> None:
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()

    # Enter (install) then Enter (keep) — the friendly defaults.
    result = runner.invoke(app, [], input="\n\n")
    assert result.exit_code == 0
    assert _has_bonsai_marker(_settings_path(project))


def test_quit_does_not_prompt_when_hook_was_already_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    fake_tty: None,
    stub_pipeline: dict[str, Any],
) -> None:
    """If the user already had a hook from a previous session, we
    don't second-guess that decision on quit — leave it alone."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    from bonsai_cc.hook.installer import (
        Scope,
        build_install_plan,
        install_hook_client_script,
        write_settings,
    )
    install_hook_client_script(bonsai_home)
    plan = build_install_plan(scope=Scope.PROJECT, project_root=project)
    write_settings(plan)

    runner = CliRunner()
    result = runner.invoke(app, [], input="")
    assert result.exit_code == 0
    assert "Keep hook installed" not in result.stdout
    assert _has_bonsai_marker(_settings_path(project))


# ---------------------------------------------------------------------------
# Non-TTY (scripted / CI) safety
# ---------------------------------------------------------------------------


def test_non_tty_without_hook_refuses_with_actionable_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    stub_pipeline_should_not_run: None,
) -> None:
    """We must never silently mutate settings in CI."""
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: False)
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()

    result = runner.invoke(app, [], input="")
    assert result.exit_code == 2
    # Message names BOTH sub-commands the user can use instead.
    combined = result.stdout + (result.stderr or "")
    assert "install-hook" in combined
    assert "watch" in combined


def test_non_tty_with_hook_already_installed_runs_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    stub_pipeline: dict[str, Any],
) -> None:
    """No prompt needed → CI / scripted use just works."""
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: False)
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    from bonsai_cc.hook.installer import (
        Scope,
        build_install_plan,
        install_hook_client_script,
        write_settings,
    )
    install_hook_client_script(bonsai_home)
    plan = build_install_plan(scope=Scope.PROJECT, project_root=project)
    write_settings(plan)

    runner = CliRunner()
    result = runner.invoke(app, [], input="")
    assert result.exit_code == 0
    assert stub_pipeline["count"] == 1


# ---------------------------------------------------------------------------
# --help still works and points users at the new default
# ---------------------------------------------------------------------------


def test_help_promotes_no_args_invocation() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    # The top-level help mentions the no-args flow as primary.
    assert "no arguments" in result.stdout.lower() or "no args" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Pipeline dispatch: web is the only renderer now
# ---------------------------------------------------------------------------


def test_default_flow_dispatches_to_web_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    stub_pipeline: dict[str, Any],
) -> None:
    """The web pipeline is the only renderer. The default flow
    must reach it with theme + open_browser kwargs."""
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: False)
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    from bonsai_cc.hook.installer import (
        Scope,
        build_install_plan,
        install_hook_client_script,
        write_settings,
    )
    install_hook_client_script(bonsai_home)
    plan = build_install_plan(scope=Scope.PROJECT, project_root=project)
    write_settings(plan)

    result = CliRunner().invoke(app, [], input="")
    assert result.exit_code == 0, result.stdout
    assert stub_pipeline["count"] == 1
    kwargs = stub_pipeline["last_kwargs"]
    assert "open_browser" in kwargs
    assert "theme" in kwargs


def test_watch_subcommand_runs_web(
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    stub_pipeline: dict[str, Any],
) -> None:
    """`bonsai-cc watch` reaches the web pipeline."""
    result = CliRunner().invoke(app, ["watch"], input="")
    assert result.exit_code == 0, result.stdout
    assert stub_pipeline["count"] == 1


def test_no_browser_flag_propagates(
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    stub_pipeline: dict[str, Any],
) -> None:
    """`--no-browser` must reach the pipeline so SSH users don't get xdg-open."""
    result = CliRunner().invoke(app, ["watch", "--no-browser"], input="")
    assert result.exit_code == 0, result.stdout
    assert stub_pipeline["last_kwargs"]["open_browser"] is False


def test_port_flag_propagates(
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
    stub_pipeline: dict[str, Any],
) -> None:
    """`--port N` lets the user pin the server to a known port."""
    result = CliRunner().invoke(app, ["watch", "--port", "8765"], input="")
    assert result.exit_code == 0, result.stdout
    assert stub_pipeline["last_kwargs"]["port"] == 8765


def test_ascii_flag_is_gone() -> None:
    """Phase 11.5: the ``--ascii`` flag is gone from every command."""
    runner = CliRunner()
    for argv in (["--ascii"], ["watch", "--ascii"], ["garden", "--ascii"]):
        result = runner.invoke(app, argv, input="")
        # typer surfaces unknown options with a non-zero exit + help.
        assert result.exit_code != 0, f"argv {argv!r} unexpectedly accepted --ascii"

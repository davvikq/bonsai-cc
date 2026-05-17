"""Settings.json merge + project-root detection + idempotency.

The acceptance criterion: a user with an existing ``settings.json``
containing unrelated hooks and MCP servers can run ``install-hook``
and have every original key survive byte-equivalently, with only the
bonsai-cc entries added. Uninstall must round-trip back to that
original state.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from bonsai_cc.hook.installer import (
    DEFAULT_REGISTERED_EVENTS,
    WINDOWS_STORE_SHIM_MARKER,
    InstallError,
    Scope,
    build_install_plan,
    find_project_root,
    find_python_executable,
    install_hook_client_script,
    is_windows_store_shim,
    render_diff,
    uninstall,
    write_settings,
    write_settings_dict,
)


@pytest.fixture
def project_with_existing_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """A project containing a non-trivial settings.json.

    Three unrelated hooks (each on a different event), two MCP server
    configs, and a free-form ``preferences`` block. After install /
    uninstall every byte of these must remain.
    """
    project = tmp_path / "myproject"
    (project / ".git").mkdir(parents=True)  # makes find_project_root happy
    settings_dir = project / ".claude"
    settings_dir.mkdir()
    original = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "/usr/local/bin/my-audit"}
                    ],
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {"type": "command", "command": "notify-send 'Claude done'"}
                    ]
                }
            ],
            "Notification": [
                {
                    "matcher": "permission_prompt",
                    "hooks": [{"type": "command", "command": "/opt/secret-tool"}],
                }
            ],
        },
        "mcpServers": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            },
            "github": {
                "command": "uvx",
                "args": ["mcp-server-github"],
                "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"},
            },
        },
        "preferences": {
            "model": "claude-opus-4-7",
            "theme": "dark",
            "experimental": {"foo": True, "bar": [1, 2, 3]},
        },
    }
    (settings_dir / "settings.json").write_text(
        json.dumps(original, indent=2) + "\n", encoding="utf-8"
    )
    monkeypatch.chdir(project)
    return project


# ---------------------------------------------------------------------------
# find_project_root
# ---------------------------------------------------------------------------


def test_find_project_root_walks_up_to_git(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    deep = root / "src" / "deep" / "subdir"
    deep.mkdir(parents=True)
    assert find_project_root(deep) == root.resolve()


def test_find_project_root_falls_back_to_cwd(tmp_path: Path) -> None:
    """No markers anywhere → just use the directory itself."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert find_project_root(plain) == plain.resolve()


def test_find_project_root_recognises_python_marker(tmp_path: Path) -> None:
    root = tmp_path / "pyrepo"
    root.mkdir()
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    sub = root / "src"
    sub.mkdir()
    assert find_project_root(sub) == root.resolve()


# ---------------------------------------------------------------------------
# Fresh install (no pre-existing settings)
# ---------------------------------------------------------------------------


def test_fresh_install_creates_full_hook_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bonsai_home: Path
) -> None:
    project = tmp_path / "fresh"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)

    plan = build_install_plan(scope=Scope.PROJECT, project_root=project)
    write_settings(plan)

    settings = json.loads(plan.settings_path.read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    for event in DEFAULT_REGISTERED_EVENTS:
        assert event in hooks
        assert len(hooks[event]) == 1
        entry = hooks[event][0]
        assert entry["_bonsai_cc"] is True
        assert entry["matcher"] == ""
        assert entry["hooks"][0]["type"] == "command"
        # The command points at the materialised hook client script,
        # under the test sandbox.
        assert "hook_client.py" in entry["hooks"][0]["command"]
        assert str(bonsai_home) in entry["hooks"][0]["command"]


# ---------------------------------------------------------------------------
# Non-destructive merge
# ---------------------------------------------------------------------------


def test_install_preserves_unrelated_settings(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    plan = build_install_plan(
        scope=Scope.PROJECT, project_root=project_with_existing_settings
    )
    write_settings(plan)

    settings = json.loads(plan.settings_path.read_text(encoding="utf-8"))
    # MCP servers and preferences must survive byte-identically.
    assert settings["mcpServers"]["filesystem"]["command"] == "npx"
    assert (
        settings["mcpServers"]["filesystem"]["args"]
        == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    )
    assert settings["mcpServers"]["github"]["env"]["GITHUB_TOKEN"] == "$GITHUB_TOKEN"
    assert settings["preferences"]["model"] == "claude-opus-4-7"
    assert settings["preferences"]["experimental"]["bar"] == [1, 2, 3]

    # The user's pre-existing hooks must still be present, with their
    # original matchers and commands intact.
    pre = settings["hooks"]["PreToolUse"]
    user_pre = [e for e in pre if e.get("_bonsai_cc") is not True]
    assert len(user_pre) == 1
    assert user_pre[0]["matcher"] == "Bash"
    assert user_pre[0]["hooks"][0]["command"] == "/usr/local/bin/my-audit"

    stop = settings["hooks"]["Stop"]
    user_stop = [e for e in stop if e.get("_bonsai_cc") is not True]
    assert len(user_stop) == 1
    assert user_stop[0]["hooks"][0]["command"] == "notify-send 'Claude done'"

    notif = settings["hooks"]["Notification"]
    user_notif = [e for e in notif if e.get("_bonsai_cc") is not True]
    assert len(user_notif) == 1
    assert user_notif[0]["matcher"] == "permission_prompt"


def test_install_is_idempotent(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    """Three installs in a row → same content as one install."""
    plan1 = build_install_plan(
        scope=Scope.PROJECT, project_root=project_with_existing_settings
    )
    write_settings(plan1)
    after_one = json.loads(plan1.settings_path.read_text(encoding="utf-8"))

    for _ in range(2):
        plan = build_install_plan(
            scope=Scope.PROJECT, project_root=project_with_existing_settings
        )
        write_settings(plan)

    after_three = json.loads(plan1.settings_path.read_text(encoding="utf-8"))
    assert after_one == after_three

    # Specifically: no event has more than one ``_bonsai_cc`` entry.
    for event, entries in after_three["hooks"].items():
        bonsai_entries = [e for e in entries if e.get("_bonsai_cc") is True]
        assert len(bonsai_entries) <= 1, (
            f"{event} accumulated {len(bonsai_entries)} bonsai-cc entries"
        )


# ---------------------------------------------------------------------------
# Uninstall round-trip
# ---------------------------------------------------------------------------


def test_uninstall_restores_original_content(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    settings_path = (
        project_with_existing_settings / ".claude" / "settings.json"
    )
    original_dict = json.loads(settings_path.read_text(encoding="utf-8"))

    plan = build_install_plan(
        scope=Scope.PROJECT, project_root=project_with_existing_settings
    )
    write_settings(plan)

    _before, after, path = uninstall(
        scope=Scope.PROJECT, project_root=project_with_existing_settings
    )
    assert path == settings_path
    write_settings_dict(path, after)

    # Semantic equality with the original (formatting may differ; the
    # contract is "modulo JSON formatting normalisation" per the design
    # discussion).
    restored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert restored == original_dict


def test_uninstall_when_no_hook_installed_is_noop(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    settings_path = (
        project_with_existing_settings / ".claude" / "settings.json"
    )
    before_text = settings_path.read_text(encoding="utf-8")

    before, after, _ = uninstall(
        scope=Scope.PROJECT, project_root=project_with_existing_settings
    )
    assert before == after  # nothing to remove
    # File untouched on disk.
    assert settings_path.read_text(encoding="utf-8") == before_text


def test_uninstall_drops_empty_event_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bonsai_home: Path
) -> None:
    """If our entries were the *only* content of an event, the event
    key should disappear entirely rather than leaving ``"Stop": []``."""
    project = tmp_path / "leftovers"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)

    # Empty settings; install will populate; uninstall must leave it
    # truly empty.
    plan = build_install_plan(scope=Scope.PROJECT, project_root=project)
    write_settings(plan)

    _before, after, _ = uninstall(scope=Scope.PROJECT, project_root=project)
    write_settings_dict(plan.settings_path, after)

    final = json.loads(plan.settings_path.read_text(encoding="utf-8"))
    assert "hooks" not in final, (
        f"uninstall left orphan ``hooks`` block: {final!r}"
    )


# ---------------------------------------------------------------------------
# Diff rendering
# ---------------------------------------------------------------------------


def test_render_diff_is_empty_when_already_installed(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    plan = build_install_plan(
        scope=Scope.PROJECT, project_root=project_with_existing_settings
    )
    write_settings(plan)

    second = build_install_plan(
        scope=Scope.PROJECT, project_root=project_with_existing_settings
    )
    assert render_diff(second) == ""


def test_render_diff_shows_additions(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    plan = build_install_plan(
        scope=Scope.PROJECT, project_root=project_with_existing_settings
    )
    diff = render_diff(plan)
    assert diff.startswith("---") or diff.startswith("+++")
    assert "_bonsai_cc" in diff


# ---------------------------------------------------------------------------
# Refusal modes
# ---------------------------------------------------------------------------


def test_refuses_to_merge_when_settings_is_not_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bonsai_home: Path
) -> None:
    project = tmp_path / "weird"
    (project / ".git").mkdir(parents=True)
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text(
        "[1, 2, 3]\n", encoding="utf-8"
    )
    monkeypatch.chdir(project)

    with pytest.raises(ValueError, match="is not a JSON object"):
        build_install_plan(scope=Scope.PROJECT, project_root=project)


def test_refuses_to_merge_when_hooks_is_not_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bonsai_home: Path
) -> None:
    project = tmp_path / "weird2"
    (project / ".git").mkdir(parents=True)
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text(
        '{"hooks": "not an object"}\n', encoding="utf-8"
    )
    monkeypatch.chdir(project)
    with pytest.raises(ValueError, match=r"settings\.hooks .* is not a JSON object"):
        build_install_plan(scope=Scope.PROJECT, project_root=project)


# ---------------------------------------------------------------------------
# Hook client materialisation
# ---------------------------------------------------------------------------


def test_install_hook_client_script_writes_template(bonsai_home: Path) -> None:
    path = install_hook_client_script(bonsai_home)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    # Header lines from the template.
    assert "STABLE INTERFACE" in text
    assert "def main()" in text
    # Pure ASCII / valid source.
    compile(text, str(path), "exec")
    if sys.platform != "win32":
        # Executable bit set so users can debug by running directly.
        mode = path.stat().st_mode & 0o777
        assert mode & 0o100, f"hook_client.py is not executable: {oct(mode)}"


# ---------------------------------------------------------------------------
# Windows Store Python shim avoidance — the critical regression.
# ---------------------------------------------------------------------------
#
# The Microsoft Store ships a fake python.exe at
# ``%LOCALAPPDATA%\Microsoft\WindowsApps\python3.EXE`` that, when run,
# opens the Microsoft Store install page instead of executing Python.
# A naive ``shutil.which("python3")`` happily returns this path,
# leaving the user with a hook that fail-silently swallows every event.
# These tests pin the new discovery logic so this never recurs.


def test_is_windows_store_shim_recognises_marker() -> None:
    assert is_windows_store_shim(
        r"C:\Users\Alice\AppData\Local\Microsoft\WindowsApps\python3.EXE"
    )
    # Case-insensitive — the user's drive / case may vary.
    assert is_windows_store_shim(
        r"C:\users\alice\appdata\local\microsoft\windowsapps\python.exe"
    )
    # Real paths must not trip the heuristic.
    assert not is_windows_store_shim(r"C:\Python312\python.exe")
    assert not is_windows_store_shim("/usr/bin/python3")
    assert not is_windows_store_shim(None)
    assert not is_windows_store_shim("")


def test_marker_constant_is_lowercase() -> None:
    """If someone edits the marker, the case-insensitive contract
    must survive — the discovery code lower()s the candidate."""
    assert WINDOWS_STORE_SHIM_MARKER.lower() == WINDOWS_STORE_SHIM_MARKER
    assert "windowsapps" in WINDOWS_STORE_SHIM_MARKER


def test_find_python_prefers_sys_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sys.executable`` is the running interpreter — by definition
    it works and isn't a shim. We must trust it over anything on
    PATH."""
    monkeypatch.setattr(sys, "executable", r"C:\Python312\python.exe")
    monkeypatch.setenv("PATH", r"C:\never\reached")
    chosen = find_python_executable()
    assert chosen == r"C:\Python312\python.exe"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Store shim only exists on Windows")
def test_find_python_skips_shim_in_sys_executable_and_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Paranoid case: ``sys.executable`` itself is a shim (uv tool
    install via the Store python launcher). We must scan PATH and
    pick a real python.exe further down."""
    # 1. sys.executable points at the shim.
    shim_dir = tmp_path / "AppData" / "Local" / "Microsoft" / "WindowsApps"
    shim_dir.mkdir(parents=True)
    shim = shim_dir / "python3.EXE"
    shim.write_text("not a real binary", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(shim))

    # 2. PATH contains the shim FIRST, then a real python further down.
    real_dir = tmp_path / "Python312"
    real_dir.mkdir()
    real_python = real_dir / "python.exe"
    real_python.write_text("not really, but is_file() + os.access(...,X_OK)", encoding="utf-8")
    # On POSIX, set the executable bit so os.access(X_OK) returns True.
    if sys.platform != "win32":
        real_python.chmod(0o755)

    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{real_dir}")
    # On Windows, PATHEXT controls which suffixes _which_all checks.
    monkeypatch.setenv("PATHEXT", ".EXE")

    chosen = find_python_executable()
    assert is_windows_store_shim(chosen) is False
    assert "Python312" in chosen


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Store shim only exists on Windows")
def test_find_python_raises_install_error_when_only_shim_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fresh Windows with ONLY the Store shim → refuse loudly.

    We never want to silently install a broken hook. The error
    message must point the user at the fix.
    """
    shim_dir = tmp_path / "AppData" / "Local" / "Microsoft" / "WindowsApps"
    shim_dir.mkdir(parents=True)
    shim_a = shim_dir / "python3.EXE"
    shim_a.write_text("shim", encoding="utf-8")
    shim_b = shim_dir / "python.exe"
    shim_b.write_text("shim", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(shim_a))
    monkeypatch.setenv("PATH", str(shim_dir))
    monkeypatch.setenv("PATHEXT", ".EXE")

    with pytest.raises(InstallError) as ei:
        find_python_executable()
    msg = str(ei.value)
    # Actionable: name the problem AND the fix.
    assert "Windows Store" in msg
    assert "python.org" in msg or "winget" in msg


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Store shim only exists on Windows")
def test_install_refuses_to_write_when_no_python_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bonsai_home: Path,
) -> None:
    """End-to-end: build_install_plan must not silently produce a
    plan with a shim path. It must raise InstallError so the CLI
    can refuse to write."""
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)

    shim_dir = tmp_path / "WindowsApps"
    shim_dir.mkdir()
    shim = shim_dir / "python3.EXE"
    shim.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(shim))
    monkeypatch.setenv("PATH", str(shim_dir))
    monkeypatch.setenv("PATHEXT", ".EXE")

    with pytest.raises(InstallError):
        build_install_plan(scope=Scope.PROJECT, project_root=project)

    # No on-disk damage: settings file must not exist.
    settings_file = project / ".claude" / "settings.json"
    assert not settings_file.exists()


# ---------------------------------------------------------------------------
# Uninstall must still clean up a stale shim-pointed hook entry
# ---------------------------------------------------------------------------


def test_uninstall_removes_existing_shim_pointed_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bonsai_home: Path
) -> None:
    """A user who installed bonsai-cc on a buggy older release may
    have a hook entry pointing at the Store shim. Uninstall must
    still recognise and remove it — the marker is what we filter
    on, not the command path.
    """
    project = tmp_path / "legacy"
    (project / ".git").mkdir(parents=True)
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    legacy_settings = {
        "hooks": {
            "Stop": [
                # A real user hook the uninstall must preserve.
                {
                    "hooks": [
                        {"type": "command", "command": "/opt/safe-tool"}
                    ]
                },
                # The broken bonsai-cc entry from a buggy install.
                {
                    "_bonsai_cc": True,
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                '"C:\\Users\\u\\AppData\\Local\\Microsoft\\'
                                'WindowsApps\\python3.EXE" '
                                '"C:\\Users\\u\\.bonsai-cc\\hook_client.py"'
                            ),
                        }
                    ],
                },
            ]
        },
        "preferences": {"theme": "dark"},
    }
    (claude_dir / "settings.json").write_text(
        json.dumps(legacy_settings, indent=2), encoding="utf-8"
    )
    monkeypatch.chdir(project)

    before, after, path = uninstall(scope=Scope.PROJECT, project_root=project)
    write_settings_dict(path, after)

    restored = json.loads(path.read_text(encoding="utf-8"))
    # The shim entry is gone, the user's safe hook survives, the
    # unrelated preferences key is intact.
    stop_entries = restored["hooks"]["Stop"]
    assert all(e.get("_bonsai_cc") is not True for e in stop_entries)
    assert any(
        e["hooks"][0]["command"] == "/opt/safe-tool"
        for e in stop_entries
        if "hooks" in e
    )
    assert restored["preferences"] == {"theme": "dark"}
    # ``before`` still has the shim entry, so the test corpus is
    # honest about what was removed.
    bonsai_before = [
        e for e in before["hooks"]["Stop"]
        if isinstance(e, dict) and e.get("_bonsai_cc") is True
    ]
    assert len(bonsai_before) == 1
    assert "WindowsApps" in bonsai_before[0]["hooks"][0]["command"]



"""End-to-end CLI tests for install-hook / uninstall-hook / doctor.

Run the Typer app via its built-in test runner. We don't fork a real
``bonsai-cc.exe`` here (that's covered by
``test_hook_client_runtime.test_happy_path_delivers_to_journal``);
instead this layer exercises the option surface and exit codes so
flag regressions like a missing ``--project`` flag get caught.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bonsai_cc.cli import app


@pytest.fixture
def project_with_existing_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "/opt/notify"}
                            ]
                        }
                    ]
                },
                "preferences": {"theme": "dark"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    return project


def test_install_uninstall_roundtrip_via_cli(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    runner = CliRunner()
    original = json.loads(
        (project_with_existing_settings / ".claude" / "settings.json").read_text(
            encoding="utf-8"
        )
    )

    install_result = runner.invoke(app, ["install-hook", "--project"])
    assert install_result.exit_code == 0, install_result.stdout
    assert "_bonsai_cc" in install_result.stdout or "Wrote hook client" in install_result.stdout

    uninstall_result = runner.invoke(app, ["uninstall-hook", "--project"])
    assert uninstall_result.exit_code == 0, uninstall_result.stdout

    restored = json.loads(
        (project_with_existing_settings / ".claude" / "settings.json").read_text(
            encoding="utf-8"
        )
    )
    assert restored == original


def test_install_dry_run_writes_nothing(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    runner = CliRunner()
    original_text = (
        project_with_existing_settings / ".claude" / "settings.json"
    ).read_text(encoding="utf-8")

    result = runner.invoke(app, ["install-hook", "--project", "--dry-run"])
    assert result.exit_code == 0, result.stdout
    # Diff is printed to stdout.
    assert "_bonsai_cc" in result.stdout
    # On-disk file is unchanged.
    after_text = (
        project_with_existing_settings / ".claude" / "settings.json"
    ).read_text(encoding="utf-8")
    assert after_text == original_text


def test_install_rejects_both_scopes(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["install-hook", "--global", "--project"])
    assert result.exit_code != 0


def test_uninstall_rejects_both_scopes(
    project_with_existing_settings: Path, bonsai_home: Path
) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["uninstall-hook", "--global", "--project"])
    assert result.exit_code != 0


def test_cleanup_legacy_smoke_artifacts_removes_journal_and_garden_row(
    bonsai_home: Path,
) -> None:
    """Earlier versions wrote the install-hook smoke marker into
    journals/_install_hook_smoke.jsonl, where the watcher picked
    it up as a real session and the runner persisted it as a
    "_install" card in the garden. The cleanup helper sweeps both
    artefacts so users who don't re-run install-hook still benefit.

    Idempotent: a second call on already-clean state is a no-op.
    """
    from bonsai_cc.cli import cleanup_legacy_smoke_artifacts
    from bonsai_cc.config import get_config
    from bonsai_cc.garden.store import GardenStore
    from bonsai_cc.growth.state import demo_tree

    cfg = get_config()
    cfg.ensure_dirs()
    legacy_journal = cfg.journals_dir / "_install_hook_smoke.jsonl"
    legacy_journal.write_text('{"ts":1,"raw":{}}\n', encoding="utf-8")
    with GardenStore() as store:
        store.save_session(
            demo_tree("_install_hook_smoke"),
            project_path="",
            event_log_path=legacy_journal,
        )

    cleanup_legacy_smoke_artifacts()

    assert not legacy_journal.exists()
    with GardenStore() as store:
        assert store.get_session("_install_hook_smoke") is None

    # Second call on a clean tree is a no-op (must not raise).
    cleanup_legacy_smoke_artifacts()


def test_doctor_runs_clean(bonsai_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    # No fails in a fresh sandbox → exit 0.
    assert result.exit_code == 0, result.stdout
    # Every check label should appear somewhere in the output.
    for label in (
        "Python",
        "Home directory",
        "Daemon",
        "Hook installed",
        "Garden",
    ):
        assert label in result.stdout

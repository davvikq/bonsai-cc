"""Smoke tests for the config layer."""

from __future__ import annotations

from pathlib import Path

from bonsai_cc.config import get_config


def test_paths_derived_from_home(bonsai_home: Path) -> None:
    cfg = get_config()
    assert cfg.home == bonsai_home
    assert cfg.journals_dir == bonsai_home / "journals"
    assert cfg.logs_dir == bonsai_home / "logs"
    assert cfg.garden_db == bonsai_home / "garden.db"
    assert cfg.pid_file == bonsai_home / "daemon.pid"


def test_ensure_dirs_idempotent(bonsai_home: Path) -> None:
    cfg = get_config()
    cfg.ensure_dirs()
    cfg.ensure_dirs()  # idempotent
    assert cfg.journals_dir.is_dir()
    assert cfg.logs_dir.is_dir()
    assert cfg.exports_dir.is_dir()

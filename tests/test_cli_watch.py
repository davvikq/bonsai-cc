"""``bonsai-cc watch`` — wiring tests.

The pipeline itself has integration coverage elsewhere; here we
just pin the CLI surface so a regression like the v0.1 ``--replay``
flag — accepted by argparse but never threaded through to the
pipeline — can't reach a release again.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from bonsai_cc import cli
from bonsai_cc.cli import app


@pytest.fixture
def stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ``run_web_pipeline`` so the test never binds a port."""
    calls: dict[str, Any] = {"count": 0, "last_kwargs": None}

    async def _stub_web(**kwargs: Any) -> None:
        calls["count"] += 1
        calls["last_kwargs"] = kwargs
        await asyncio.sleep(0)

    monkeypatch.setattr("bonsai_cc.cli.run_web_pipeline", _stub_web)
    return calls


def test_watch_threads_replay_path_and_speed_to_pipeline(
    tmp_path: Path,
    bonsai_home: Path,
    stub_pipeline: dict[str, Any],
) -> None:
    """``--replay <file> --speed 2.0`` must arrive at the pipeline
    as ``replay_path=<file>``, ``replay_speed=2.0``.

    The v0.1 bug: the flags were accepted by argparse but the
    handler called ``run_web_pipeline()`` with no replay args, so
    the README example silently rendered an empty pot. This test
    pins the wiring so the regression can't recur.
    """
    journal = tmp_path / "smoke.jsonl"
    journal.write_text(
        '{"ts":1700000000000,"raw":{"session_id":"s","hook_event_name":"SessionStart","cwd":"/tmp"}}\n',
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["watch", "--replay", str(journal), "--speed", "2.0", "--no-browser"],
    )
    assert result.exit_code == 0, result.output
    assert stub_pipeline["count"] == 1
    kwargs = stub_pipeline["last_kwargs"]
    assert kwargs["replay_path"] == journal
    assert kwargs["replay_speed"] == 2.0
    assert kwargs["open_browser"] is False


def test_watch_without_replay_passes_none(
    bonsai_home: Path,
    stub_pipeline: dict[str, Any],
) -> None:
    """Plain ``bonsai-cc watch`` runs the pipeline with no replay
    payload — the watcher alone drives growth."""
    runner = CliRunner()
    result = runner.invoke(app, ["watch", "--no-browser"])
    assert result.exit_code == 0, result.output
    kwargs = stub_pipeline["last_kwargs"]
    assert kwargs["replay_path"] is None


def test_watch_rejects_missing_replay_file(
    tmp_path: Path,
    bonsai_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad ``--replay`` path exits 2 with an actionable message
    rather than booting the daemon and then quietly doing nothing.
    """
    # Pipeline must not be called.
    async def _fail(**_: Any) -> None:
        raise AssertionError("pipeline must not start for a missing replay file")

    monkeypatch.setattr("bonsai_cc.cli.run_web_pipeline", _fail)

    missing = tmp_path / "does-not-exist.jsonl"
    runner = CliRunner()
    result = runner.invoke(app, ["watch", "--replay", str(missing)])
    assert result.exit_code == 2
    assert "Replay file not found" in result.output

"""Test fixtures.

Every test runs inside a sandboxed ``BONSAI_CC_HOME``: a temp directory
that disappears at teardown. This keeps tests from touching the real
``~/.bonsai-cc`` and lets us assert on the on-disk layout.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from bonsai_cc.config import reset_config_cache
from bonsai_cc.events.bus import reset_event_bus_for_tests
from bonsai_cc.growth.state import TreeState
from bonsai_cc.log import reset_logging_for_tests


class RecorderApp:
    """Test stub that satisfies the duck-typed ``set_state`` contract.

    The Textual ``BonsaiApp`` is gone (phase 11.5); the
    :class:`GrowthRunner` accepts any object with a
    ``set_state(state, *, last_event_name=...)`` method. Test
    suites that used to construct a real Textual app now construct
    a :class:`RecorderApp` instead — drop-in.
    """

    def __init__(self, _initial_state: TreeState | None = None) -> None:
        self.state: TreeState | None = _initial_state
        self.last_event_name: str | None = None
        self.call_count = 0
        self.tool_counts: dict[str, int] = {}

    def set_state(
        self,
        state: TreeState,
        *,
        last_event_name: str | None = None,
        tool_counts: dict[str, int] | None = None,
    ) -> None:
        self.state = state
        self.last_event_name = last_event_name
        if tool_counts is not None:
            self.tool_counts = dict(tool_counts)
        self.call_count += 1


@pytest.fixture
def bonsai_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``BONSAI_CC_HOME`` at a temp dir for this test."""
    monkeypatch.setenv("BONSAI_CC_HOME", str(tmp_path))
    reset_config_cache()
    reset_logging_for_tests()
    reset_event_bus_for_tests()
    yield tmp_path
    reset_config_cache()
    reset_logging_for_tests()


@pytest.fixture(autouse=True)
def _isolate_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Ensure no test writes to the real ``~/.bonsai-cc/logs``.

    The :func:`bonsai_home` fixture covers tests that need a sandbox;
    this autouse fixture handles tests that DON'T explicitly opt in,
    so a stray ``get_logger()`` call in a unit-under-test never
    materialises real log files.
    """
    if "BONSAI_CC_HOME" not in os.environ:
        monkeypatch.setenv("BONSAI_CC_HOME", str(tmp_path / "_default_home"))
        reset_config_cache()
        reset_logging_for_tests()
    yield

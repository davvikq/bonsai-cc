"""Doctor diagnostic checks."""

from __future__ import annotations

from pathlib import Path

from bonsai_cc.hook.doctor import Check, run_all


def _by_label(checks: list[Check], label: str) -> Check:
    for c in checks:
        if c.label == label:
            return c
    raise AssertionError(f"no check named {label!r}; have {[c.label for c in checks]}")


def test_runs_clean_in_sandbox(bonsai_home: Path) -> None:
    """A fresh sandbox: no daemon, no hook, no garden — but no fails."""
    checks = run_all()
    labels = [c.label for c in checks]
    for expected in (
        "bonsai-cc",
        "Python",
        "Platform",
        "Event pipeline",
        "Home directory",
        "Journals directory",
        "fsync support",
        "Daemon",
        "Hook installed",
        "Hook client script",
        "Garden",
        "Terminal",
    ):
        assert expected in labels, f"missing check: {expected}"

    # Nothing has been set up yet, so the "infrastructure ok" checks
    # are green and the "did you install yet?" checks are info-level.
    assert _by_label(checks, "Home directory").status == "ok"
    assert _by_label(checks, "Journals directory").status == "ok"
    assert _by_label(checks, "fsync support").status in ("ok", "warn")
    assert _by_label(checks, "Daemon").status == "info"
    assert _by_label(checks, "Hook installed").status == "info"
    assert _by_label(checks, "Garden").status == "info"

    # No check should fail in a healthy fresh sandbox.
    assert not any(c.status == "fail" for c in checks), [
        (c.label, c.value, c.remediation) for c in checks if c.status == "fail"
    ]


def test_remediation_present_for_non_ok_states(bonsai_home: Path) -> None:
    """Every non-ok check should give the user something to do."""
    checks = run_all()
    for c in checks:
        if c.status in ("warn", "fail"):
            assert c.remediation, (
                f"check {c.label!r} ({c.status}) has no remediation message"
            )

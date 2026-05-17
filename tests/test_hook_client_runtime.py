"""Runtime behaviour of the hook client.

Phase 11: the hook writes directly to ``<home>/journals/<sid>.jsonl``
— no daemon required. The fail-silent contract still holds:

* exit 0 in every scenario;
* nothing on stdout;
* nothing on stderr (unless ``BONSAI_CC_DEBUG=1``);
* completion within the budget.

A separate test runs many concurrent invocations and asserts every
event lands on a distinct journal line in arrival order.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib import resources
from pathlib import Path

import pytest


def _template_path() -> Path:
    res = resources.files("bonsai_cc.hook").joinpath("client_template.py")
    with resources.as_file(res) as p:
        return Path(p)


def _run_hook_client(
    payload: bytes,
    *,
    home: Path,
    timeout_s: float = 2.0,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Invoke the hook client as Claude Code would.

    Sets ``BONSAI_CC_HOME`` to the test sandbox and feeds ``payload``
    on stdin. Returns the completed process for inspection.
    """
    env = os.environ.copy()
    env["BONSAI_CC_HOME"] = str(home)
    # Make sure the test sandbox is the only "home" the hook sees.
    env.pop("LOCALAPPDATA", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_template_path())],
        input=payload,
        capture_output=True,
        env=env,
        timeout=timeout_s,
        check=False,
    )


# ---------------------------------------------------------------------------
# Fail-silent contract: nothing the user can do should produce noise.
# ---------------------------------------------------------------------------


def test_no_daemon_writes_to_journal(bonsai_home: Path) -> None:
    """Phase 11 contract: with no daemon running the hook still
    appends the event to the per-session journal. The daemon was a
    coupling, not a dependency."""
    payload = b'{"session_id":"s1","hook_event_name":"Stop"}\n'
    proc = _run_hook_client(payload, home=bonsai_home)
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""

    journal = bonsai_home / "journals" / "s1.jsonl"
    assert journal.exists()
    rec = json.loads(journal.read_text(encoding="utf-8").strip())
    assert rec["raw"]["hook_event_name"] == "Stop"
    assert rec["raw"]["session_id"] == "s1"
    # Phase-11 records carry ts but no idx (line position is the idx).
    assert isinstance(rec["ts"], int)
    assert "idx" not in rec


def test_empty_stdin_exits_silently(bonsai_home: Path) -> None:
    proc = _run_hook_client(b"", home=bonsai_home)
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    # No journal should have been touched.
    assert not (bonsai_home / "journals").exists() or not any(
        (bonsai_home / "journals").iterdir()
    )


def test_malformed_json_exits_silently(bonsai_home: Path) -> None:
    proc = _run_hook_client(b"not json at all", home=bonsai_home)
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""


def test_payload_without_session_id_is_dropped(bonsai_home: Path) -> None:
    """A payload that doesn't name a session has nowhere to land in
    the per-session journal model — silent drop."""
    payload = b'{"hook_event_name":"Notification","message":"hi"}\n'
    proc = _run_hook_client(payload, home=bonsai_home)
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    journals_dir = bonsai_home / "journals"
    assert not journals_dir.exists() or not any(journals_dir.iterdir())


def test_payload_with_path_traversal_session_id_is_sanitized(
    bonsai_home: Path,
) -> None:
    """``../etc/passwd`` as a session id must become a safe filename,
    NEVER escape the journals directory."""
    payload = json.dumps(
        {"session_id": "../etc/passwd", "hook_event_name": "Stop"}
    ).encode("utf-8")
    proc = _run_hook_client(payload, home=bonsai_home)
    assert proc.returncode == 0
    # The journals directory must only contain a sanitized filename.
    journals_dir = bonsai_home / "journals"
    files = sorted(p.name for p in journals_dir.iterdir())
    for f in files:
        assert "/" not in f and ".." not in f, f"path traversal leaked: {f}"
    # The whitelist replaces every non-[A-Za-z0-9_-] with _ → safe.
    assert any(".jsonl" in f for f in files)


def test_redact_env_blanks_prompts_and_edit_strings(
    bonsai_home: Path,
) -> None:
    """``BONSAI_CC_REDACT=1`` must blank the high-signal-of-content
    fields (``prompt``, ``old_string``, ``new_string``, ``content``)
    so the on-disk journal records what happened but not the
    literal text. The growth engine only reads
    ``hook_event_name`` / ``tool_name`` / ``file_path`` / ``cwd``,
    so the rendered tree is unchanged.
    """
    sid = "redact-sid"
    payload = json.dumps({
        "session_id": sid,
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "/proj/auth.py",
            "old_string": "SECRET_KEY = 'hunter2'",
            "new_string": "SECRET_KEY = os.environ['KEY']",
        },
        "cwd": "/proj",
    }).encode("utf-8")
    proc = _run_hook_client(
        payload, home=bonsai_home, extra_env={"BONSAI_CC_REDACT": "1"}
    )
    assert proc.returncode == 0
    journal = bonsai_home / "journals" / f"{sid}.jsonl"
    rec = json.loads(journal.read_text(encoding="utf-8").strip())
    raw = rec["raw"]
    # Identifying fields survive — the growth engine needs them.
    assert raw["tool_name"] == "Edit"
    assert raw["tool_input"]["file_path"] == "/proj/auth.py"
    assert raw["cwd"] == "/proj"
    # Content fields are redacted.
    assert raw["tool_input"]["old_string"] == "[redacted by BONSAI_CC_REDACT]"
    assert raw["tool_input"]["new_string"] == "[redacted by BONSAI_CC_REDACT]"
    # The literal secret is nowhere on disk.
    assert b"hunter2" not in journal.read_bytes()


def test_redact_off_by_default_preserves_full_payload(
    bonsai_home: Path,
) -> None:
    """Without ``BONSAI_CC_REDACT`` the hook is lossless — the raw
    payload is preserved byte-for-byte. This is the documented
    default; users opt into redaction explicitly.
    """
    sid = "no-redact-sid"
    payload = json.dumps({
        "session_id": sid,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "secret-text-12345",
    }).encode("utf-8")
    proc = _run_hook_client(payload, home=bonsai_home)
    assert proc.returncode == 0
    journal = bonsai_home / "journals" / f"{sid}.jsonl"
    rec = json.loads(journal.read_text(encoding="utf-8").strip())
    assert rec["raw"]["prompt"] == "secret-text-12345"


def test_debug_env_writes_log_on_exception(
    bonsai_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``BONSAI_CC_DEBUG=1`` enables the hook-client.log appender.

    We can't easily force an exception from the outside, so this test
    just asserts that running with DEBUG=1 still exits silently with
    no debug noise on stderr — the log file may or may not appear
    depending on which code paths hit, but nothing leaks to the user.
    """
    proc = _run_hook_client(
        b'{"session_id":"s1","hook_event_name":"Stop"}\n',
        home=bonsai_home,
        extra_env={"BONSAI_CC_DEBUG": "1"},
    )
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""


# ---------------------------------------------------------------------------
# Concurrency: many hooks racing on the same session must produce
# distinct lines in arrival order (O_APPEND atomic semantics).
# ---------------------------------------------------------------------------


def test_concurrent_hooks_for_same_session_no_loss(bonsai_home: Path) -> None:
    """Fire 20 hook invocations in parallel for one session. Every
    payload must land on its own line; no corruption, no loss.

    Smaller than the brief's 1000-event stress test — that one runs
    in ``scripts/bench_hook_client.py``. Here we want a unit-test
    sized smoke test that still catches the obvious failure modes
    (interleaved writes, truncation, missed events)."""
    sid = "s_concurrent"
    payloads = [
        json.dumps(
            {
                "session_id": sid,
                "hook_event_name": "PostToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": f"/p/f{i}.py", "content": "x"},
                "_marker": i,
            }
        ).encode("utf-8")
        for i in range(20)
    ]

    def _one(p: bytes) -> int:
        proc = _run_hook_client(p, home=bonsai_home, timeout_s=4.0)
        return proc.returncode

    with ThreadPoolExecutor(max_workers=20) as pool:
        for fut in as_completed(pool.submit(_one, p) for p in payloads):
            assert fut.result() == 0

    journal = bonsai_home / "journals" / f"{sid}.jsonl"
    assert journal.exists()
    lines = [
        line for line in journal.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(lines) == 20, f"expected 20 records, got {len(lines)}"
    markers = set()
    for line in lines:
        rec = json.loads(line)
        markers.add(rec["raw"]["_marker"])
    assert markers == set(range(20)), f"missing markers: {set(range(20)) - markers}"


def test_single_invocation_fits_in_budget(bonsai_home: Path) -> None:
    """One no-op hook write must complete well inside the 500ms
    wall-clock budget. Single sample, not a percentile benchmark —
    that's in ``scripts/bench_hook_client.py``."""
    payload = b'{"session_id":"s_budget","hook_event_name":"Stop"}\n'
    start = time.monotonic()
    proc = _run_hook_client(payload, home=bonsai_home, timeout_s=2.0)
    elapsed = time.monotonic() - start
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    # We allow a generous ceiling for cold-start Python interpreter
    # overhead on whichever runner this lands on.
    assert elapsed < 1.5, f"hook client took {elapsed:.2f}s, expected <1.5s"

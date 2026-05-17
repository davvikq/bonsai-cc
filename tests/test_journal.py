"""Journal append/read round-trip and crash-resume."""

from __future__ import annotations

import json
from pathlib import Path

from bonsai_cc.events.journal import Journal, JournalRegistry


def test_append_and_read_roundtrip(tmp_path: Path) -> None:
    j = Journal(tmp_path / "s1.jsonl")
    idx_a = j.append({"hook_event_name": "Stop", "session_id": "s1"})
    idx_b = j.append({"hook_event_name": "SessionEnd", "session_id": "s1"})

    assert idx_a == 0
    assert idx_b == 1
    records = list(j.read())
    assert [r["idx"] for r in records] == [0, 1]
    assert records[0]["raw"]["hook_event_name"] == "Stop"
    assert records[1]["raw"]["hook_event_name"] == "SessionEnd"
    # Wall-clock timestamps are millisecond-scale ints.
    for r in records:
        assert isinstance(r["ts"], int)
        assert r["ts"] > 1_000_000_000_000


def test_resume_idx_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "s1.jsonl"
    j1 = Journal(path)
    j1.append({"hook_event_name": "Stop", "session_id": "s1"})
    j1.append({"hook_event_name": "Stop", "session_id": "s1"})

    j2 = Journal(path)  # restart
    next_idx = j2.append({"hook_event_name": "SessionEnd", "session_id": "s1"})
    assert next_idx == 2
    records = list(j2.read())
    assert [r["idx"] for r in records] == [0, 1, 2]


def test_corrupt_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "s1.jsonl"
    path.write_text(
        '{"ts":1,"idx":0,"raw":{"hook_event_name":"Stop"}}\n'
        "not-json-at-all\n"
        '{"ts":2,"idx":1,"raw":{"hook_event_name":"SessionEnd"}}\n',
        encoding="utf-8",
    )
    j = Journal(path)
    records = list(j.read())
    assert [r["raw"]["hook_event_name"] for r in records] == ["Stop", "SessionEnd"]
    # next append continues past the highest seen idx
    next_idx = j.append({"hook_event_name": "Stop", "session_id": "s1"})
    assert next_idx == 2


def test_registry_routes_by_session(tmp_path: Path) -> None:
    reg = JournalRegistry(tmp_path)
    j1 = reg.for_session("alpha")
    j2 = reg.for_session("beta")
    j1_again = reg.for_session("alpha")
    assert j1 is j1_again
    assert j1.path != j2.path
    assert j1.path.name == "alpha.jsonl"


def test_registry_sanitizes_session_id(tmp_path: Path) -> None:
    reg = JournalRegistry(tmp_path)
    j = reg.for_session("../etc/passwd")
    # Path-traversal characters must be neutralised.
    assert j.path.parent == tmp_path
    assert ".." not in j.path.name
    assert "/" not in j.path.name


def test_registry_empty_session_id_falls_back(tmp_path: Path) -> None:
    reg = JournalRegistry(tmp_path)
    j = reg.for_session("")
    assert j.path.name == "unknown.jsonl"


def test_registry_truncates_pathologically_long_session_id(tmp_path: Path) -> None:
    """A 10 MiB session_id must not produce a 10 MiB filename.

    The sanitizer caps the length at 128 chars to prevent a malformed
    or hostile payload from DoSing the filesystem (PATH_MAX on most
    platforms is 255 or 4096; we stay well under).
    """
    reg = JournalRegistry(tmp_path)
    huge = "A" * (10 * 1024 * 1024)
    j = reg.for_session(huge)
    # Sanitized name + ".jsonl" extension must be sanely bounded.
    assert len(j.path.name) <= 128 + len(".jsonl")


def test_registry_rejects_non_ascii_via_whitelist(tmp_path: Path) -> None:
    """The sanitizer is a whitelist: only ``[A-Za-z0-9_-]`` survive.

    Per the verification ask, this is asserted explicitly: a payload
    using Unicode lookalikes for ``/`` or ``..`` (which a naive
    blacklist would miss) must be replaced character by character.
    """
    reg = JournalRegistry(tmp_path)
    # U+2215 DIVISION SLASH looks like '/' but isn't ASCII. We bypass
    # the visible character entirely so the source file stays pure
    # ASCII and ruff RUF001 has nothing to flag.
    division_slash = chr(0x2215)
    fancy = f"abc{division_slash}def"
    j = reg.for_session(fancy)
    assert j.path.parent == tmp_path
    # The slash-lookalike must be substituted.
    assert division_slash not in j.path.name
    # Only whitelisted chars survive.
    stem = j.path.stem  # filename without .jsonl
    assert all(c.isascii() and (c.isalnum() or c in "_-") for c in stem)


def test_record_shape_matches_design(tmp_path: Path) -> None:
    j = Journal(tmp_path / "s1.jsonl")
    j.append({"hook_event_name": "Stop", "session_id": "s1"})
    line = (tmp_path / "s1.jsonl").read_text(encoding="utf-8").strip()
    rec = json.loads(line)
    assert set(rec.keys()) == {"ts", "idx", "raw"}

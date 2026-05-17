"""Read-only diagnostic for the local garden + journals.

Run on any user's machine when something looks wrong with the
garden TUI. Prints:

* the schema version from ``schema_meta``,
* every table and view in the DB,
* row counts per table,
* the first few rows of ``sessions`` (id, started_at, status,
  event_count, project_path, ended_at) so a missing or
  filtered-out row stands out immediately,
* the journals directory: every ``*.jsonl`` with its size, first
  and last event timestamps, the session id parsed from the
  first event, and whether the garden has a matching row.

The script never writes to the DB or the journals. Without
``--verbose`` it prints only to stdout; with ``--verbose`` it
mirrors progress to stderr too. Exit code is always 0 (this is
diagnosis, not a check).

Usage::

    uv run python scripts/diagnose_garden.py
    uv run python scripts/diagnose_garden.py --verbose
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from bonsai_cc.config import get_config


def _log(msg: str, *, verbose: bool) -> None:
    if verbose:
        print(msg, file=sys.stderr)


def _dump_db(db_path: Path, *, verbose: bool) -> None:
    print("=== garden.db ===")
    print(f"path: {db_path}")
    if not db_path.exists():
        print("MISSING — recovery has nothing to populate.")
        return
    print(f"size: {db_path.stat().st_size} bytes")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # schema_meta version
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
            print(f"schema version: {row[0] if row else '<missing row>'}")
        except sqlite3.OperationalError as exc:
            print(f"schema_meta unreadable: {exc}")

        # Tables and views
        objs = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        print("objects:")
        for o in objs:
            print(f"  {o['type']:>5}  {o['name']}")

        # Row counts
        print("row counts:")
        for o in objs:
            if o["type"] != "table":
                continue
            try:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {o['name']}"
                ).fetchone()[0]
            except sqlite3.OperationalError as exc:
                print(f"  {o['name']}: COUNT failed: {exc}")
                continue
            print(f"  {o['name']}: {count}")

        # Sessions table dump
        print("sessions (first 5):")
        try:
            cols = [c[1] for c in conn.execute("PRAGMA table_info(sessions)")]
            print(f"  columns: {cols}")
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 5"
            ).fetchall()
            if not rows:
                print("  (no rows)")
            for r in rows:
                summary = {
                    "id": r["id"],
                    "started_at": r["started_at"],
                    "ended_at": r["ended_at"],
                    "status": _safe_get(r, "status"),
                    "event_count": r["tool_call_count"],
                    "project_path": r["project_path"],
                    "lang": r["detected_language"],
                    "event_log_path": r["event_log_path"],
                }
                print(f"  {summary}")
        except sqlite3.OperationalError as exc:
            print(f"  sessions query failed: {exc}")
    finally:
        conn.close()
    _log("db dump complete", verbose=verbose)


def _safe_get(row: sqlite3.Row, key: str) -> object:
    try:
        return row[key]
    except (IndexError, KeyError):
        return "<column missing>"


def _dump_journals(
    journals_dir: Path,
    known_session_ids: set[str],
    *,
    verbose: bool,
) -> None:
    print("\n=== journals/ ===")
    print(f"path: {journals_dir}")
    if not journals_dir.exists():
        print("MISSING — nothing to recover.")
        return
    files = sorted(
        p for p in journals_dir.iterdir()
        if p.is_file() and p.suffix == ".jsonl"
    )
    if not files:
        print("(empty)")
        return
    for path in files:
        size = path.stat().st_size
        first_ts: int | None = None
        last_ts: int | None = None
        first_sid: str | None = None
        n_records = 0
        try:
            with path.open(encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    n_records += 1
                    ts = rec.get("ts")
                    if isinstance(ts, int):
                        if first_ts is None:
                            first_ts = ts
                        last_ts = ts
                    raw = rec.get("raw")
                    if first_sid is None and isinstance(raw, dict):
                        first_sid = str(raw.get("session_id", "")) or None
        except OSError as exc:
            print(f"  {path.name}: read failed: {exc}")
            continue
        in_garden = (
            "yes" if first_sid and first_sid in known_session_ids else "no"
        )
        print(
            f"  {path.name}: {size}B, {n_records} records, "
            f"sid={first_sid!r}, first_ts={first_ts}, last_ts={last_ts}, "
            f"in_garden={in_garden}"
        )


def _list_known_session_ids(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    out: set[str] = set()
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            rows = conn.execute("SELECT id FROM sessions").fetchall()
        except sqlite3.OperationalError:
            return out
        for r in rows:
            out.add(str(r[0]))
    finally:
        conn.close()
    return out


def _dump_logs(logs_dir: Path, *, verbose: bool) -> None:
    print("\n=== logs/ ===")
    print(f"path: {logs_dir}")
    if not logs_dir.exists():
        print("MISSING — no logs yet.")
        return
    log_files = sorted(logs_dir.glob("bonsai-cc-*.log"))
    if not log_files:
        print("(no log files)")
        return
    # Grep the most-recent log file for recovery / persistence noise.
    most_recent = log_files[-1]
    print(f"most recent: {most_recent.name} ({most_recent.stat().st_size}B)")
    keywords = (
        "orphan", "recover", "garden_saved", "garden_session_persisted",
        "garden_persist_failed", "growth_runner_session_bound",
    )
    matches: list[str] = []
    try:
        with most_recent.open(encoding="utf-8", errors="replace") as fp:
            for line in fp:
                if any(k in line for k in keywords):
                    matches.append(line.rstrip())
    except OSError as exc:
        print(f"  read failed: {exc}")
        return
    if not matches:
        print("  no relevant entries (recovery / persistence / session bind)")
        return
    print(f"  {len(matches)} relevant entries — showing the last 10:")
    for line in matches[-10:]:
        print(f"    {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose", action="store_true",
        help="Mirror progress to stderr as well.",
    )
    args = parser.parse_args()

    cfg = get_config()
    _log(f"home={cfg.home}", verbose=args.verbose)
    _dump_db(cfg.garden_db, verbose=args.verbose)
    known_ids = _list_known_session_ids(cfg.garden_db)
    _dump_journals(cfg.journals_dir, known_ids, verbose=args.verbose)
    _dump_logs(cfg.logs_dir, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Diagnose the /api/replay/<id> SSE stream end-to-end.

Spins up an in-process aiohttp test client (no real port needed),
seeds a temporary GardenStore with one row pointing at the supplied
journal file, hits /api/replay/<id>?speed=0, and prints one diagnostic
line per SSE frame:

    frame N: ts=... event_index=I/total svg_len=... tool_counts=... event_name=...

Also writes the first state frame's full SVG to
``./debug_frame_0.svg`` for visual inspection (open it in a browser).

Usage::

    uv run python scripts/debug_replay.py [journal_path]

Defaults to ``tests/fixtures/real_session_2026-05-15.jsonl`` — the
sample of session 86abd5d6 the user reports as broken.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from bonsai_cc.events.bus import reset_event_bus_for_tests
from bonsai_cc.events.models import parse_event
from bonsai_cc.garden.store import GardenStore
from bonsai_cc.growth.apply import apply_all
from bonsai_cc.web.broadcaster import WebBroadcaster
from bonsai_cc.web.server import build_app


async def _read_frames(resp) -> list[tuple[str, dict]]:
    """Pull every SSE frame from ``resp`` until the stream ends."""
    out: list[tuple[str, dict]] = []
    name: str | None = None
    data_lines: list[str] = []
    async for raw in resp.content:
        line = raw.decode("utf-8").rstrip("\n").rstrip("\r")
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
        elif line == "" and name is not None:
            payload = json.loads("".join(data_lines)) if data_lines else {}
            out.append((name, payload))
            if name == "replay_done":
                break
            name = None
            data_lines = []
    return out


async def main() -> int:
    journal_path = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "tests/fixtures/real_session_2026-05-15.jsonl"
    ).resolve()
    if not journal_path.exists():
        print(f"!! journal not found: {journal_path}", file=sys.stderr)
        return 2

    # Peek at the journal to extract session id + count records.
    with journal_path.open(encoding="utf-8") as fp:
        first = json.loads(fp.readline())
    sid = first["raw"]["session_id"]
    print(f"journal: {journal_path}")
    print(f"session_id: {sid}")
    with journal_path.open(encoding="utf-8") as fp:
        n_lines = sum(1 for line in fp if line.strip())
    print(f"records in journal: {n_lines}")

    # Set up an in-process server pointing at a temp garden that
    # has one row for this session.
    reset_event_bus_for_tests()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        garden = GardenStore(path=tmp / "garden.db")
        # Compute final state via apply_all so the saved row is
        # consistent with the journal we'll feed back to replay.
        events: list[tuple[int, object]] = []
        with journal_path.open(encoding="utf-8") as fp:
            for line in fp:
                if not line.strip():
                    continue
                rec = json.loads(line)
                events.append((int(rec["idx"]), parse_event(rec["raw"])))
        state = apply_all(sid, events, theme="python")
        garden.save_session(
            state,
            project_path=str(journal_path.parent),
            event_log_path=journal_path,
            detected_language="python",
        )

        b = WebBroadcaster()
        async with (
            TestClient(TestServer(build_app(b, garden=garden))) as client,
            client.get(f"/api/replay/{sid}?speed=0") as resp,
        ):
            if resp.status != 200:
                text = await resp.text()
                print(f"!! /api/replay returned HTTP {resp.status}")
                print(f"   body: {text[:200]}")
                return 1
            frames = await asyncio.wait_for(_read_frames(resp), timeout=10.0)
        garden.close()

    # Summarise.
    state_frames = [(n, p) for n, p in frames if n == "state"]
    done_frames = [(n, p) for n, p in frames if n == "replay_done"]
    print(f"\nframes received: {len(frames)} ({len(state_frames)} state, "
          f"{len(done_frames)} replay_done)")
    if not state_frames:
        print("!! NO state frames — the bug is somewhere before payload assembly")
        return 1

    # Per-frame diagnostic line.
    print("\nframe-by-frame:")
    for i, (_name, p) in enumerate(state_frames):
        svg = p.get("svg") or ""
        tc = p.get("tool_counts") or {}
        non_zero = {k: v for k, v in tc.items() if v}
        print(
            f"  [{i:>2}] event_index={p.get('replay_idx')}/"
            f"{p.get('replay_total')}  "
            f"svg_len={len(svg):>5}  "
            f"tool_counts(non-zero)={non_zero or '{}'}  "
            f"event_name={p.get('event_name')!r}  "
            f"idle={p.get('idle')!r}"
        )
        if i < 3 or i == len(state_frames) - 1:
            head = svg.replace("\n", " ")[:120]
            print(f"        svg[:120] = {head!r}")

    # Dump first + last frame SVGs to disk for visual inspection.
    out_dir = Path.cwd()
    first_svg = state_frames[0][1].get("svg") or ""
    last_svg = state_frames[-1][1].get("svg") or ""
    (out_dir / "debug_frame_0.svg").write_text(first_svg, encoding="utf-8")
    (out_dir / "debug_frame_last.svg").write_text(last_svg, encoding="utf-8")
    print(
        f"\nwrote: {out_dir / 'debug_frame_0.svg'} ({len(first_svg)} bytes)\n"
        f"       {out_dir / 'debug_frame_last.svg'} ({len(last_svg)} bytes)"
    )

    # Quick structural checks.
    print("\nstructural checks on final-frame SVG:")
    for marker, label in (
        ("<svg", "svg envelope present"),
        ("</svg>", "svg envelope closed"),
        ('id="bcc-pot"', "pot gradient present"),
        ("<path", "any path elements"),
        ("<ellipse", "any ellipse elements"),
        ("<text", "text elements (idle placeholder)"),
        ("Waiting for", "idle placeholder copy"),
    ):
        present = marker in last_svg
        print(f"  {'✓' if present else '✗'} {label}: {marker!r}")

    # Tool counts summary.
    final = state_frames[-1][1].get("tool_counts") or {}
    print(f"\nfinal tool_counts: {final}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""Journal append + fsync latency benchmark (local diagnostic).

Run locally to verify per-event fsync stays cheap on your disk.
Not wired into CI; shared-storage runners produce too much variance
for a meaningful threshold.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

from bonsai_cc.events.journal import Journal


def _bench(journal: Journal, n: int) -> list[float]:
    """Time each append with nanosecond resolution.

    ``time.monotonic`` on Windows has ~16 ms granularity (the
    default system clock tick), which made the original journal
    bench histogram bimodal between "below the tick" and "one
    tick." ``perf_counter_ns`` is sub-microsecond on every
    platform we target.
    """
    samples: list[float] = []
    payload = {
        "session_id": "_bench",
        "hook_event_name": "Notification",
        "tool_input": {"command": "x" * 64},
    }
    for _ in range(n):
        start = time.perf_counter_ns()
        journal.append(payload)
        samples.append((time.perf_counter_ns() - start) / 1e9)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Directory to journal in. Defaults to a temp dir on $TMPDIR.",
    )
    # Defaults tuned for CI's shared-storage variance: a single 200 ms
    # outlier on a 1000-event run is normal on GitHub runners. Real
    # pathology (FUSE, NFS, antivirus-hooking-syscalls) shows up well
    # above 300 ms p99. Locally on SSD you'll usually see p99 < 20 ms.
    parser.add_argument("--warn-ms", type=float, default=30.0)
    parser.add_argument("--fail-ms", type=float, default=300.0)
    args = parser.parse_args()

    if args.dir:
        target_dir = Path(args.dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        journal_path = target_dir / "bench.jsonl"
        if journal_path.exists():
            journal_path.unlink()
        samples_s = _bench(Journal(journal_path), args.events)
    else:
        with tempfile.TemporaryDirectory(prefix="bonsai-jbench-") as tmp:
            samples_s = _bench(Journal(Path(tmp) / "bench.jsonl"), args.events)

    samples_ms = sorted(s * 1000 for s in samples_s)
    p50 = statistics.median(samples_ms)
    p90 = samples_ms[int(0.90 * (len(samples_ms) - 1))]
    p99 = samples_ms[int(0.99 * (len(samples_ms) - 1))]
    p999 = samples_ms[int(0.999 * (len(samples_ms) - 1))] if len(samples_ms) >= 1000 else samples_ms[-1]
    print(
        f"journal.append+fsync (n={args.events}): "
        f"p50={p50:.2f}ms p90={p90:.2f}ms p99={p99:.2f}ms p999={p999:.2f}ms "
        f"min={samples_ms[0]:.2f}ms max={samples_ms[-1]:.2f}ms"
    )

    if p99 > args.fail_ms:
        print(
            f"FAIL: p99 {p99:.2f}ms > {args.fail_ms:.2f}ms threshold. "
            "Disk is pathologically slow -- check for FUSE, network "
            "filesystems, or AV hooking syscalls.",
            file=sys.stderr,
        )
        return 1
    if p99 > args.warn_ms:
        print(
            f"WARN: p99 {p99:.2f}ms > {args.warn_ms:.2f}ms ideal. "
            "Per-event fsync is acceptable but watch for drift.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

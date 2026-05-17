"""Cold-start benchmark for the hook client.

Per the design contract, the hook script is invoked once per Claude
Code event and **must not slow Claude Code down**. The single most
important non-functional property is its p99 wall-clock latency on a
no-op event (no daemon listening — the most common case for users who
haven't started ``bonsai-cc watch`` yet).

CI gate
-------
p99 must be < 150ms over 50 runs on the CI runner. If it exceeds that,
the build fails. We log p50/p90/p99 so a regression is visible at a
glance.

Usage::

    uv run python scripts/bench_hook_client.py
    uv run python scripts/bench_hook_client.py --runs 200 --threshold-ms 100

The benchmark deliberately runs the hook client with no daemon up:
that exercises the "no socket / no port file" fast-fail path which
is the cheapest scenario and the one users will hit while the daemon
is offline. A regression here is the canary.
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import time
from importlib import resources
from pathlib import Path

PAYLOAD = b'{"session_id":"_bench","hook_event_name":"Notification","message":"bench"}\n'
DEFAULT_RUNS = 50
DEFAULT_THRESHOLD_MS = 150.0


def _template_path() -> Path:
    res = resources.files("bonsai_cc.hook").joinpath("client_template.py")
    with resources.as_file(res) as p:
        return Path(p)


def _one_run(home: Path) -> float:
    env = os.environ.copy()
    env["BONSAI_CC_HOME"] = str(home)
    env.pop("LOCALAPPDATA", None)
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(_template_path())],
        input=PAYLOAD,
        capture_output=True,
        env=env,
        check=False,
    )
    elapsed = time.monotonic() - start
    if proc.returncode != 0 or proc.stdout or proc.stderr:
        raise RuntimeError(
            f"hook client misbehaved: rc={proc.returncode} "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    return elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--threshold-ms", type=float, default=DEFAULT_THRESHOLD_MS)
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="Throwaway runs to amortise interpreter cache effects.",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="bonsai-bench-") as tmp:
        home = Path(tmp)
        for _ in range(args.warmup):
            _one_run(home)
        samples_s = [_one_run(home) for _ in range(args.runs)]

    samples_ms = sorted(s * 1000 for s in samples_s)
    p50 = statistics.median(samples_ms)
    p90 = samples_ms[int(0.90 * (len(samples_ms) - 1))]
    p99 = samples_ms[int(0.99 * (len(samples_ms) - 1))]
    print(
        f"hook_client cold-start (n={args.runs}): "
        f"p50={p50:.1f}ms p90={p90:.1f}ms p99={p99:.1f}ms "
        f"min={samples_ms[0]:.1f}ms max={samples_ms[-1]:.1f}ms"
    )

    if p99 > args.threshold_ms:
        print(
            f"FAIL: p99 {p99:.1f}ms exceeds threshold {args.threshold_ms:.1f}ms",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

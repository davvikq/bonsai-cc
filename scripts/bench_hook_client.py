"""Cold-start benchmark for the hook client (local diagnostic).

Run this locally before / after touching the hook to catch
regressions. Not wired into CI -- GitHub Actions runners produce
too much variance for a meaningful threshold.
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
# 100 runs makes p99 the 99th percentile of 100 (= the second-worst
# sample) instead of "the single worst spike", which is what 50 runs
# effectively measured. GitHub Actions runners produce 1-2 outliers
# per 100 from process-start jitter; thresholding on those was
# flaky theatre.
DEFAULT_RUNS = 100
# Cold-start budget. Real pathology (heavy import, slow disk for the
# fsync inside the hook) shows up well above this; CI runner noise
# without a regression stays under it.
DEFAULT_THRESHOLD_MS = 600.0


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
        default=10,
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

"""Time luxvigil on fake logs of different sizes.

    python scripts/bench.py 10000 100000 1000000

Each size runs in its own process so peak memory isn't mixed up between runs.
Numbers depend on your machine, so compare runs on the same one.
"""

import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run_one(n, path):
    from luxvigil.generate import generate, write_csv
    from luxvigil.report import build_report
    from luxvigil.schema import load_logs

    # ~5k abusive requests are always planted, the rest are normal users at ~32 requests each
    users = max(10, (n - 5000) // 32)
    rows, _ = generate(n_users=users)
    write_csv(rows, path)
    del rows

    t0 = time.perf_counter()
    logs = load_logs(path)
    t1 = time.perf_counter()
    report = build_report(logs)
    t2 = time.perf_counter()
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"{len(logs):>10,} requests | load {t1 - t0:6.1f}s | checks {t2 - t1:6.1f}s | "
          f"total {t2 - t0:6.1f}s | peak ~{peak_mb:,.0f} MB | likely abuse ${report.abuse_cost:,.0f}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--one":
        run_one(int(sys.argv[2]), Path(sys.argv[3]))
        sys.exit()
    sizes = [int(x) for x in sys.argv[1:]] or [10_000, 100_000]
    with tempfile.TemporaryDirectory() as tmp:
        for n in sizes:
            subprocess.run([sys.executable, __file__, "--one", str(n), str(Path(tmp) / f"{n}.csv")], check=True)

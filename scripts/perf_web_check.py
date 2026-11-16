"""Measure idle backend resources and actual loopback HTTP snapshot latency."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

USS_LIMIT_MB = 50.0
CPU_LIMIT_PCT = 1.0
LATENCY_P95_LIMIT_MS = 100.0


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def measure(samples: int = 30, idle_seconds: float = 10.0) -> dict:
    import psutil

    from neuralmeter.web import LocalDashboard

    with tempfile.TemporaryDirectory(prefix="neuralmeter-web-perf-") as temporary:
        dashboard = LocalDashboard(port=0, data_dir=Path(temporary))
        dashboard.start(open_browser=False)
        process = psutil.Process(os.getpid())
        try:
            with urlopen(dashboard.url + "api/snapshot", timeout=2) as response:
                if response.status != 200:
                    raise RuntimeError(f"snapshot HTTP {response.status}")
                response.read()
            process.cpu_percent(None)
            deadline = time.monotonic() + idle_seconds
            while time.monotonic() < deadline:
                time.sleep(min(0.1, deadline - time.monotonic()))
            cpu = process.cpu_percent(None) / (psutil.cpu_count(logical=True) or 1)
            uss = process.memory_full_info().uss / 2**20

            timings = []
            for _ in range(samples):
                started = time.perf_counter()
                with urlopen(dashboard.url + "api/snapshot", timeout=2) as response:
                    if response.status != 200:
                        raise RuntimeError(f"snapshot HTTP {response.status}")
                    response.read()
                timings.append((time.perf_counter() - started) * 1000)
        finally:
            dashboard.close()
    return {
        "scope": (
            "LocalDashboard Python process; loopback HTTP GET /api/snapshot; "
            "provider calls excluded"
        ),
        "uss_mb": round(uss, 2),
        "cpu_pct_avg": round(cpu, 3),
        "http_requests": len(timings),
        "http_snapshot_p50_ms": round(percentile(timings, 50), 2),
        "http_snapshot_p95_ms": round(percentile(timings, 95), 2),
    }


def evaluate(result: dict) -> list[str]:
    failures = []
    if result["uss_mb"] >= USS_LIMIT_MB:
        failures.append(f"USS {result['uss_mb']} MB >= {USS_LIMIT_MB} MB")
    if result["cpu_pct_avg"] >= CPU_LIMIT_PCT:
        failures.append(f"CPU {result['cpu_pct_avg']}% >= {CPU_LIMIT_PCT}%")
    if result["http_snapshot_p95_ms"] >= LATENCY_P95_LIMIT_MS:
        failures.append(
            f"HTTP snapshot p95 {result['http_snapshot_p95_ms']} ms >= {LATENCY_P95_LIMIT_MS} ms"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--idle-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    if args.samples < 1 or args.idle_seconds < 0:
        parser.error("--samples must be positive; --idle-seconds non-negative")
    result = measure(args.samples, args.idle_seconds)
    failures = evaluate(result)
    result["fails"] = failures
    print(json.dumps(result, indent=None if args.json else 2))
    return int(bool(failures) and not args.report_only)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    raise SystemExit(main())

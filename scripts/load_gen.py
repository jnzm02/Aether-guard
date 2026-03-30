#!/usr/bin/env python3
"""
Aether-Guard Load Generator — Phase 1 Verification Tool

Drives traffic against the target-service to:
  1. Establish a healthy SLI baseline (normal requests)
  2. Inject chaos scenarios on demand
  3. Verify that Prometheus is recording the expected metrics

Usage:
    python3 scripts/load_gen.py                    # baseline traffic only
    python3 scripts/load_gen.py --scenario memleak
    python3 scripts/load_gen.py --scenario latency
    python3 scripts/load_gen.py --scenario error
    python3 scripts/load_gen.py --scenario all     # kitchen-sink chaos
"""

import argparse
import random
import sys
import time
from dataclasses import dataclass, field

import urllib.request
import urllib.error

BASE_URL = "http://localhost:8080"

# ─────────────────────────────────────────────────────────────────────────────
# Stats collector
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Stats:
    total:      int = 0
    success:    int = 0
    errors:     int = 0
    latencies:  list = field(default_factory=list)

    def record(self, status: int, elapsed_ms: float) -> None:
        self.total += 1
        self.latencies.append(elapsed_ms)
        if 200 <= status < 400:
            self.success += 1
        else:
            self.errors += 1

    def print_summary(self) -> None:
        if not self.latencies:
            return
        sorted_lat = sorted(self.latencies)
        n = len(sorted_lat)
        p50  = sorted_lat[int(n * 0.50)]
        p99  = sorted_lat[int(n * 0.99)]
        p999 = sorted_lat[min(int(n * 0.999), n - 1)]
        error_rate = self.errors / self.total * 100 if self.total else 0
        slo_ok = "✅" if p99 < 200 else "❌  SLO BREACH"

        print(f"\n{'─'*55}")
        print(f"  Requests : {self.total:>6}   Errors : {self.errors:>6} ({error_rate:.1f}%)")
        print(f"  p50      : {p50:>6.1f} ms")
        print(f"  p99      : {p99:>6.1f} ms   {slo_ok}  (SLO: < 200 ms)")
        print(f"  p99.9    : {p999:>6.1f} ms")
        print(f"{'─'*55}")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def get(path: str, timeout: float = 10.0) -> tuple[int, float]:
    url = BASE_URL + path
    start = time.monotonic()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            elapsed = (time.monotonic() - start) * 1000
            return resp.status, elapsed
    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        return e.code, elapsed
    except Exception:
        elapsed = (time.monotonic() - start) * 1000
        return 0, elapsed


# ─────────────────────────────────────────────────────────────────────────────
# Traffic patterns
# ─────────────────────────────────────────────────────────────────────────────

NORMAL_ENDPOINTS = ["/api/users", "/api/orders"]

def run_baseline(stats: Stats, rps: int = 10, duration_s: int = 30) -> None:
    """Send healthy traffic at `rps` requests/second for `duration_s` seconds."""
    print(f"\n🟢  Baseline traffic: {rps} RPS for {duration_s}s ...")
    deadline = time.monotonic() + duration_s
    interval = 1.0 / rps

    while time.monotonic() < deadline:
        path = random.choice(NORMAL_ENDPOINTS)
        status, elapsed = get(path)
        stats.record(status, elapsed)
        print(f"   {status}  {path:<20}  {elapsed:>7.1f} ms", end="\r")
        time.sleep(interval)


def inject_memleak(mb_per_call: int = 20, calls: int = 5) -> None:
    """Inject a memory leak in `calls` successive calls of `mb_per_call` MiB each."""
    print(f"\n🔴  Injecting memory leak: {calls} × {mb_per_call} MiB ...")
    for i in range(1, calls + 1):
        status, elapsed = get(f"/chaos/memleak?mb={mb_per_call}")
        print(f"   [{i}/{calls}]  {status}  +{mb_per_call} MiB  ({elapsed:.0f} ms)")
        time.sleep(1)


def inject_latency(ms: int = 3000, requests: int = 5) -> None:
    """Inject latency spikes that will breach the p99 < 200 ms SLO."""
    print(f"\n🔴  Injecting latency spikes: {ms} ms × {requests} requests ...")
    for i in range(1, requests + 1):
        status, elapsed = get(f"/chaos/latency?ms={ms}", timeout=ms / 1000 + 5)
        print(f"   [{i}/{requests}]  {status}  elapsed={elapsed:.0f} ms  (target={ms} ms)")


def inject_errors(rate: float = 1.0, requests: int = 20) -> None:
    """Inject 500 errors at the given rate to burn the error budget."""
    print(f"\n🔴  Injecting errors: rate={rate:.0%} × {requests} requests ...")
    for i in range(1, requests + 1):
        status, elapsed = get(f"/chaos/error?rate={rate}")
        symbol = "❌" if status == 500 else "✅"
        print(f"   [{i}/{requests}]  {symbol}  HTTP {status}  ({elapsed:.0f} ms)")
        time.sleep(0.1)


def reset_chaos() -> None:
    print("\n♻️   Resetting chaos state ...")
    status, elapsed = get("/chaos/reset")
    print(f"   {status}  ({elapsed:.0f} ms)")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Aether-Guard Load Generator")
    parser.add_argument(
        "--scenario",
        choices=["baseline", "memleak", "latency", "error", "all"],
        default="baseline",
    )
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()

    global BASE_URL
    BASE_URL = args.base_url

    # Smoke-test connectivity.
    status, _ = get("/health")
    if status != 200:
        print(f"❌  target-service /health returned {status}. Is it running?")
        sys.exit(1)
    print(f"✅  target-service reachable at {BASE_URL}")

    stats = Stats()

    if args.scenario == "baseline":
        run_baseline(stats, rps=10, duration_s=30)

    elif args.scenario == "memleak":
        run_baseline(stats, rps=5, duration_s=10)
        inject_memleak(mb_per_call=50, calls=8)
        run_baseline(stats, rps=5, duration_s=10)

    elif args.scenario == "latency":
        run_baseline(stats, rps=5, duration_s=10)
        inject_latency(ms=3000, requests=5)
        run_baseline(stats, rps=5, duration_s=10)

    elif args.scenario == "error":
        run_baseline(stats, rps=5, duration_s=10)
        inject_errors(rate=1.0, requests=30)
        run_baseline(stats, rps=5, duration_s=10)

    elif args.scenario == "all":
        run_baseline(stats, rps=10, duration_s=15)
        inject_memleak(mb_per_call=30, calls=5)
        inject_latency(ms=2500, requests=3)
        inject_errors(rate=0.8, requests=20)
        run_baseline(stats, rps=5, duration_s=15)
        reset_chaos()

    stats.print_summary()


if __name__ == "__main__":
    main()

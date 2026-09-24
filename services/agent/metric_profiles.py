"""
Runtime-aware metric profiles for Aether-Guard.

The rule engine and enrichment layer reason over LOGICAL signals (heap bytes,
thread/goroutine count, CPU rate, GC pressure). The *PromQL that produces* each
signal depends on the monitored service's runtime: a Go service exposes
`go_goroutines` / `go_memstats_heap_*`, while a JVM service (Micrometer) exposes
`jvm_threads_live_threads` / `jvm_memory_used_bytes{area="heap"}` / `jvm_gc_*`.

This module isolates that runtime-specific PromQL so the rule *logic* (thresholds,
actions, decisions) stays identical across runtimes. The active runtime is chosen
by the MONITORED_RUNTIME env var (default "go" → byte-identical to the historical
behavior). Signals that don't exist for a runtime return None; callers treat a
None expression as "signal unavailable" and degrade gracefully (the metric is
simply absent from the snapshot, exactly as a failed query already behaves).

Metrics whose names are already runtime-neutral are NOT listed here and stay
inline at their call sites:
  - aether_guard_http_* (the Java demo emits identical names)
  - aether_guard_chaos_* (demo chaos emits identical names)
  - process_cpu_seconds_total / process_open_fds (Micrometer emits the SAME names)
"""

import os

# Logical signal -> PromQL template with a `{job}` placeholder.
# None means "this runtime has no such signal".
_PROFILES: dict[str, dict[str, str | None]] = {
    "go": {
        # gauge: current goroutine count
        "goroutine_count": 'go_goroutines{{job="{job}"}}',
        # gauge: in-use heap bytes (leak booster)
        "heap_inuse_bytes": 'go_memstats_heap_inuse_bytes{{job="{job}"}}',
        # gauge: allocated heap bytes (leak trend)
        "heap_alloc_bytes": 'go_memstats_heap_alloc_bytes{{job="{job}"}}',
        # counter rate: CPU seconds/sec (efficiency trend)
        "cpu_rate": 'rate(process_cpu_seconds_total{{job="{job}"}}[5m])',
        # counter rate as percent (enrichment snapshot)
        "cpu_usage_pct": 'rate(process_cpu_seconds_total{{job="{job}"}}[5m]) * 100',
        # mean GC pause (seconds) — Go has no directly-comparable signal here
        "gc_pause_mean_seconds": None,
    },
    "jvm": {
        # JVM threads are the analog of goroutines (Micrometer JvmThreadMetrics)
        "goroutine_count": 'jvm_threads_live_threads{{job="{job}"}}',
        # sum across heap pools (eden/survivor/old) — Micrometer JvmMemoryMetrics
        "heap_inuse_bytes": 'sum(jvm_memory_used_bytes{{job="{job}", area="heap"}})',
        "heap_alloc_bytes": 'sum(jvm_memory_used_bytes{{job="{job}", area="heap"}})',
        # Micrometer ProcessorMetrics emits process_cpu_seconds_total (same name)
        "cpu_rate": 'rate(process_cpu_seconds_total{{job="{job}"}}[5m])',
        "cpu_usage_pct": 'rate(process_cpu_seconds_total{{job="{job}"}}[5m]) * 100',
        # mean GC pause over 5m (Micrometer JvmGcMetrics: jvm_gc_pause_seconds_*)
        "gc_pause_mean_seconds": (
            'rate(jvm_gc_pause_seconds_sum{{job="{job}"}}[5m])'
            ' / clamp_min(rate(jvm_gc_pause_seconds_count{{job="{job}"}}[5m]), 1e-9)'
        ),
    },
}


def get_runtime() -> str:
    """Return the active runtime ("go" or "jvm"), defaulting to "go"."""
    rt = os.getenv("MONITORED_RUNTIME", "go").lower()
    return rt if rt in _PROFILES else "go"


def metric_expr(signal: str, job: str, runtime: str | None = None) -> str | None:
    """
    Return the PromQL for a logical signal under the active (or given) runtime,
    with `{job}` substituted. Returns None when the signal is unavailable for
    that runtime (caller should treat it as "metric absent").
    """
    prof = _PROFILES.get(runtime or get_runtime(), _PROFILES["go"])
    template = prof.get(signal)
    return template.format(job=job) if template else None

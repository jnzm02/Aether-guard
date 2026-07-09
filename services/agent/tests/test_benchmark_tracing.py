"""
Aether-Guard — Fast-Path Benchmark Test (Workstream 1 Checkpoint)

Test coverage:
  1. Measure latency of each of 8 rule patterns WITH tracing enabled
  2. Measure latency of each of 8 rule patterns WITHOUT tracing (no-op tracer)
  3. Calculate per-pattern overhead
  4. Verify overhead is <1ms per pattern (Workstream 1 requirement)

This test produces ACTUAL before/after numbers per pattern, not just pass/fail.
"""

import time
import pytest
from unittest.mock import patch
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from rules import RuleEngine


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures — Representative alerts/metrics/logs for each pattern
# ─────────────────────────────────────────────────────────────────────────────

# Pattern 1: OOM_KILL
OOM_KILL_LOGS = [
    "2026-07-09 10:00:00 INFO Server started on port 8080",
    "2026-07-09 10:05:00 WARN Memory usage high: 85%",
    "2026-07-09 10:10:00 ERROR Out of memory: Kill process 1234 (target-service)",
    "2026-07-09 10:10:01 INFO Container restarted",
]

# Pattern 2: RESTART_LOOP
RESTART_LOOP_LOGS = [
    "2026-07-09 10:00:00 INFO Starting server on port 8080",
    "2026-07-09 10:00:05 ERROR Exited with code 1",
    "2026-07-09 10:00:10 INFO Starting server on port 8080",
    "2026-07-09 10:00:15 ERROR Exited with code 1",
    "2026-07-09 10:00:20 INFO Starting server on port 8080",
    "2026-07-09 10:00:25 ERROR Exited with code 1",
]

# Pattern 3: MEMORY_LEAK (requires metrics)
MEMORY_LEAK_METRICS = {
    "go_memstats_heap_alloc_bytes": 500_000_000.0,  # 500 MB
    "rss_bytes": 600_000_000.0,  # 600 MB
    "container_memory_limit_bytes": 1_000_000_000.0,  # 1 GB
    # Simulating trend analysis would return positive slope (leak detected)
}
MEMORY_LEAK_LOGS = [
    "2026-07-09 10:00:00 WARN Memory usage increasing steadily",
]

# Pattern 4: CPU_SATURATION (two variants)
CPU_SATURATION_TRAFFIC_METRICS = {
    "cpu_usage_percent": 95.0,
    "requests_per_second": 2000.0,  # High traffic
    "p99_latency_seconds": 0.5,
}
CPU_SATURATION_EFFICIENCY_METRICS = {
    "cpu_usage_percent": 95.0,
    "requests_per_second": 50.0,  # Low traffic, efficiency issue
    "p99_latency_seconds": 2.0,
}

# Pattern 5: TRAFFIC_SPIKE
TRAFFIC_SPIKE_METRICS = {
    "requests_per_second": 5000.0,  # Spike
    "error_rate_percent": 0.5,
    "cpu_usage_percent": 60.0,
}

# Pattern 6: DEPENDENCY_FAILURE
DEPENDENCY_FAILURE_LOGS = [
    "2026-07-09 10:00:00 INFO Server started",
    "2026-07-09 10:05:00 ERROR Database connection failed: connection refused",
    "2026-07-09 10:05:01 ERROR Failed to connect to redis: timeout",
    "2026-07-09 10:05:02 ERROR API call to auth-service failed: 503",
]

# Pattern 7: BAD_DEPLOYMENT
BAD_DEPLOYMENT_LOGS = [
    "2026-07-09 10:00:00 INFO Deployed version v1.2.3",
    "2026-07-09 10:00:05 ERROR Panic: undefined variable 'config'",
    "2026-07-09 10:00:06 ERROR Exited with code 2",
]
BAD_DEPLOYMENT_METRICS = {
    "error_rate_percent": 25.0,  # High error rate
    "requests_per_second": 100.0,
}

# Pattern 8: GOROUTINE_LEAK (async, requires Prometheus query_range)
GOROUTINE_LEAK_METRICS = {
    "go_goroutines": 1500.0,  # High count
}
GOROUTINE_LEAK_LOGS = [
    "2026-07-09 10:00:00 WARN Goroutine count increasing",
]


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

async def measure_pattern_latency(
    rule_engine: RuleEngine,
    alert: dict,
    metrics: dict,
    logs: list[str],
    iterations: int = 100,
) -> float:
    """
    Measure average latency of pattern matching over multiple iterations.

    Returns:
        Average latency in milliseconds
    """
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        await rule_engine.analyze(alert, metrics, logs)
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms

    return sum(times) / len(times)


# ─────────────────────────────────────────────────────────────────────────────
# Main Benchmark Test
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fast_path_benchmark_with_actual_numbers():
    """
    Benchmark test: Measure latency overhead of tracing for each of 8 rule patterns.

    Expected output format (per pattern):
      OOM_KILL: 0.008ms → 0.012ms (+0.004ms overhead)

    Requirement: <1ms overhead per pattern
    """
    # ─────────────────────────────────────────────────────────────────────────
    # Setup: Mock async Prometheus queries to avoid network I/O in benchmark
    # ─────────────────────────────────────────────────────────────────────────

    # For benchmark purposes, we only care about pattern matching latency, not Prometheus I/O
    # Mock the TrendClassifier to return instant results
    async def mock_analyze_metric(*args, **kwargs):
        from trend_analysis import TrendAnalysis
        return TrendAnalysis(
            is_rising=True,  # Simulate leak detected
            slope=150_000.0,  # 150 KB/sec
            confidence=0.90,
            data_points=20,
            window_duration_sec=600,
            start_value=100_000_000.0,
            end_value=190_000_000.0,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Test data for all 8 patterns
    # ─────────────────────────────────────────────────────────────────────────

    test_cases = [
        {
            "name": "OOM_KILL",
            "alert": {"labels": {"alertname": "HighMemoryUsage"}},
            "metrics": {},
            "logs": OOM_KILL_LOGS,
        },
        {
            "name": "RESTART_LOOP",
            "alert": {"labels": {"alertname": "PodCrashLooping"}},
            "metrics": {},
            "logs": RESTART_LOOP_LOGS,
        },
        {
            "name": "MEMORY_LEAK",
            "alert": {"labels": {"alertname": "HighMemoryUsage"}},
            "metrics": MEMORY_LEAK_METRICS,
            "logs": MEMORY_LEAK_LOGS,
        },
        {
            "name": "CPU_SATURATION_TRAFFIC",
            "alert": {"labels": {"alertname": "HighCPUUsage"}},
            "metrics": CPU_SATURATION_TRAFFIC_METRICS,
            "logs": [],
        },
        {
            "name": "CPU_SATURATION_EFFICIENCY",
            "alert": {"labels": {"alertname": "HighCPUUsage"}},
            "metrics": CPU_SATURATION_EFFICIENCY_METRICS,
            "logs": [],
        },
        {
            "name": "TRAFFIC_SPIKE",
            "alert": {"labels": {"alertname": "HighTraffic"}},
            "metrics": TRAFFIC_SPIKE_METRICS,
            "logs": [],
        },
        {
            "name": "DEPENDENCY_FAILURE",
            "alert": {"labels": {"alertname": "ServiceUnavailable"}},
            "metrics": {},
            "logs": DEPENDENCY_FAILURE_LOGS,
        },
        {
            "name": "BAD_DEPLOYMENT",
            "alert": {"labels": {"alertname": "HighErrorRate"}},
            "metrics": BAD_DEPLOYMENT_METRICS,
            "logs": BAD_DEPLOYMENT_LOGS,
        },
        {
            "name": "GOROUTINE_LEAK",
            "alert": {"labels": {"alertname": "HighGoroutineCount"}},
            "metrics": GOROUTINE_LEAK_METRICS,
            "logs": GOROUTINE_LEAK_LOGS,
        },
    ]

    # ─────────────────────────────────────────────────────────────────────────
    # Benchmark WITHOUT tracing (baseline)
    # ─────────────────────────────────────────────────────────────────────────

    print("\n" + "=" * 80)
    print("FAST-PATH BENCHMARK RESULTS (Workstream 1 Checkpoint)")
    print("=" * 80)
    print("\nMeasuring baseline latency WITHOUT tracing (100 iterations per pattern)...\n")

    baseline_results = {}

    with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric):

        # Use no-op tracer (tracing disabled)
        trace.set_tracer_provider(trace.NoOpTracerProvider())
        rule_engine_baseline = RuleEngine()

        for test_case in test_cases:
            latency = await measure_pattern_latency(
                rule_engine_baseline,
                test_case["alert"],
                test_case["metrics"],
                test_case["logs"],
                iterations=100,
            )
            baseline_results[test_case["name"]] = latency
            print(f"  {test_case['name']:30s}: {latency:8.5f} ms")

    # ─────────────────────────────────────────────────────────────────────────
    # Benchmark WITH tracing (instrumented)
    # ─────────────────────────────────────────────────────────────────────────

    print("\nMeasuring latency WITH tracing enabled (100 iterations per pattern)...\n")

    traced_results = {}

    with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric):

        # Set up real tracer with in-memory exporter
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        rule_engine_traced = RuleEngine()

        for test_case in test_cases:
            latency = await measure_pattern_latency(
                rule_engine_traced,
                test_case["alert"],
                test_case["metrics"],
                test_case["logs"],
                iterations=100,
            )
            traced_results[test_case["name"]] = latency
            print(f"  {test_case['name']:30s}: {latency:8.5f} ms")

    # ─────────────────────────────────────────────────────────────────────────
    # Calculate and display overhead
    # ─────────────────────────────────────────────────────────────────────────

    print("\n" + "-" * 80)
    print("OVERHEAD ANALYSIS (traced - baseline)")
    print("-" * 80)
    print(f"{'Pattern':<30s} {'Baseline':>12s} {'Traced':>12s} {'Overhead':>12s} {'Pass/Fail':>10s}")
    print("-" * 80)

    all_passed = True
    max_overhead = 0.0
    max_overhead_pattern = ""

    for test_case in test_cases:
        pattern_name = test_case["name"]
        baseline = baseline_results[pattern_name]
        traced = traced_results[pattern_name]
        overhead = traced - baseline

        # Track maximum overhead
        if overhead > max_overhead:
            max_overhead = overhead
            max_overhead_pattern = pattern_name

        # Check if overhead exceeds 1ms threshold
        passed = overhead < 1.0
        status = "✓ PASS" if passed else "✗ FAIL"

        if not passed:
            all_passed = False

        print(
            f"{pattern_name:<30s} "
            f"{baseline:10.5f} ms "
            f"{traced:10.5f} ms "
            f"{overhead:+10.5f} ms "
            f"{status:>10s}"
        )

    print("-" * 80)
    print(f"\nMaximum overhead: {max_overhead:.5f} ms ({max_overhead_pattern})")
    print("Requirement: <1ms overhead per pattern")

    if all_passed:
        print("\n✅ BENCHMARK PASSED: All patterns under 1ms overhead threshold")
    else:
        print("\n❌ BENCHMARK FAILED: Some patterns exceed 1ms overhead threshold")

    print("=" * 80 + "\n")

    # ─────────────────────────────────────────────────────────────────────────
    # Assertions
    # ─────────────────────────────────────────────────────────────────────────

    for pattern_name in baseline_results:
        overhead = traced_results[pattern_name] - baseline_results[pattern_name]
        assert overhead < 1.0, \
            f"Pattern {pattern_name} overhead {overhead:.5f}ms exceeds 1ms threshold"


# ─────────────────────────────────────────────────────────────────────────────
# Additional Benchmark: Full analyze_alert pipeline overhead
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_pipeline_benchmark():
    """
    Benchmark the full analyze_alert() pipeline with tracing.

    This measures end-to-end overhead including:
      - Trace context extraction
      - RCA analysis span
      - Rule engine check span
      - All span attribute setting

    Expected: <5ms total overhead for full pipeline (not just rule matching)
    """
    from agent import analyze_alert
    from unittest.mock import patch

    # Mock Claude API (to isolate tracing overhead)
    mock_claude_response = {
        "analysis": "Test analysis",
        "root_cause": "Test root cause",
        "confidence": 0.8,
        "action": "IGNORE",
        "reasoning": "Test reasoning",
        "slo_impact": "None",
        "recommended_followup": "None",
    }

    async def mock_call_claude(*args, **kwargs):
        return mock_claude_response

    # Test alert (will trigger LLM path since no rule matches)
    test_alert = {
        "id": "benchmark-test-001",
        "labels": {"alertname": "UnknownAlert"},
        "prometheus_snapshot": {},
        "logs": [],
        "status": "firing",
        "annotations": {},
    }

    iterations = 50
    baseline_times = []
    traced_times = []

    # Baseline (no tracing)
    trace.set_tracer_provider(trace.NoOpTracerProvider())

    with patch("agent.call_claude", new=mock_call_claude), \
         patch("agent._rule_engine", None):  # Disable rule engine to force LLM path

        for _ in range(iterations):
            start = time.perf_counter()
            await analyze_alert(test_alert)
            end = time.perf_counter()
            baseline_times.append((end - start) * 1000)

    baseline_avg = sum(baseline_times) / len(baseline_times)

    # With tracing
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    with patch("agent.call_claude", new=mock_call_claude), \
         patch("agent._rule_engine", None):

        for _ in range(iterations):
            start = time.perf_counter()
            await analyze_alert(test_alert)
            end = time.perf_counter()
            traced_times.append((end - start) * 1000)

    traced_avg = sum(traced_times) / len(traced_times)
    overhead = traced_avg - baseline_avg

    print("\n" + "=" * 80)
    print("FULL PIPELINE BENCHMARK (analyze_alert end-to-end)")
    print("=" * 80)
    print(f"Baseline (no tracing):  {baseline_avg:8.5f} ms")
    print(f"With tracing:           {traced_avg:8.5f} ms")
    print(f"Overhead:              {overhead:+8.5f} ms")
    print("Threshold:             <5.000 ms")
    print("=" * 80 + "\n")

    assert overhead < 5.0, \
        f"Full pipeline overhead {overhead:.5f}ms exceeds 5ms threshold"

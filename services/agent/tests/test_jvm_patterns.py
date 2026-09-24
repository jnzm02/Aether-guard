"""
Tests for the JVM-specific RCA behavior added in the generalization work.

Covers the two issues the safety review flagged:
  1. A SINGLE healthy Spring Boot startup must NOT be misclassified as a crash
     loop (RESTART_LOOP → ROLLBACK).
  2. GC pressure fires (RESTART) only with NORMAL traffic; under a traffic spike
     it must NOT auto-fire (load-driven GC, not a leak).
"""

import asyncio

from rules import RuleEngine


def _analyze(alert, metrics, logs):
    return asyncio.run(RuleEngine().analyze(alert, metrics, logs))


# A single, healthy Spring Boot boot prints all of these on ONE startup.
SINGLE_HEALTHY_JVM_BOOT = [
    "2026-09-24 10:00:00 INFO Root WebApplicationContext: initialization completed in 1084 ms",
    "2026-09-24 10:00:01 INFO Tomcat started on port(s): 8085 (http) with context path ''",
    "2026-09-24 10:00:02 INFO Started DemoApplication in 2.345 seconds (JVM running for 2.789)",
]


def test_single_healthy_jvm_startup_is_not_a_restart_loop():
    """One normal JVM boot must not trip the >=3 restart-loop threshold."""
    result = _analyze(
        {"labels": {"alertname": "ServiceRestarted"}},
        {},
        SINGLE_HEALTHY_JVM_BOOT,
    )
    assert result is None or result.rule_name != "RESTART_LOOP"


def test_three_distinct_jvm_restarts_still_detected():
    """A genuine loop (3 real restarts) must still be caught."""
    logs = SINGLE_HEALTHY_JVM_BOOT[-1:] * 3  # 3 "Started ... in ... seconds" lines
    logs = [
        "Started DemoApplication in 2.1 seconds (JVM running for 2.5)",
        "Exited with code 1",
        "Started DemoApplication in 2.2 seconds (JVM running for 2.6)",
        "Exited with code 1",
        "Started DemoApplication in 2.0 seconds (JVM running for 2.4)",
    ]
    result = _analyze({"labels": {"alertname": "PodCrashLooping"}}, {}, logs)
    assert result is not None and result.rule_name == "RESTART_LOOP"


def test_gc_pressure_fires_with_normal_traffic():
    result = _analyze(
        {"labels": {"alertname": "JvmGcPressure"}},
        {"gc_pause_mean_seconds": 0.25, "request_rate_5m": 50.0},
        [],
    )
    assert result is not None
    assert result.rule_name == "GC_PRESSURE"
    assert result.recommended_action == "RESTART"
    assert result.confidence >= 0.85


def test_gc_pressure_skipped_under_traffic_spike():
    """High GC under a traffic spike is load-driven — do not auto-RESTART."""
    result = _analyze(
        {"labels": {"alertname": "JvmGcPressure"}},
        {"gc_pause_mean_seconds": 0.25, "request_rate_5m": 5000.0},
        [],
    )
    assert result is None or result.rule_name != "GC_PRESSURE"


def test_gc_pressure_absent_for_go_service():
    """Go snapshots never carry gc_pause_mean_seconds → rule is a no-op."""
    result = _analyze(
        {"labels": {"alertname": "HighLatency"}},
        {"request_rate_5m": 50.0},  # no gc key
        [],
    )
    assert result is None or result.rule_name != "GC_PRESSURE"


def test_jvm_oom_log_detected():
    """A JVM OutOfMemoryError in logs triggers the OOM rule."""
    result = _analyze(
        {"labels": {"alertname": "HighMemory"}},
        {},
        ["Exception in thread \"http-nio-8085-exec-3\" java.lang.OutOfMemoryError: Java heap space"],
    )
    assert result is not None and result.rule_name == "OOM_KILL"

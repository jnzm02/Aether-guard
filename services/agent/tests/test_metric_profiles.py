"""
Tests for the runtime-aware metric profiles.

The Go profile is a regression guard: its expressions MUST equal the literals
that were previously hard-coded in enrichment.py / rules.py, so existing Go
behavior is byte-identical. The JVM profile returns the Micrometer equivalents.
"""

import importlib

import pytest

import metric_profiles


@pytest.fixture
def profiles(monkeypatch):
    """Reload the module so MONITORED_RUNTIME is re-read per test."""
    def _load(runtime=None):
        if runtime is None:
            monkeypatch.delenv("MONITORED_RUNTIME", raising=False)
        else:
            monkeypatch.setenv("MONITORED_RUNTIME", runtime)
        return importlib.reload(metric_profiles)
    return _load


def test_default_runtime_is_go(profiles):
    mp = profiles()
    assert mp.get_runtime() == "go"


def test_unknown_runtime_falls_back_to_go(profiles):
    mp = profiles("dotnet")
    assert mp.get_runtime() == "go"


def test_jvm_runtime_selected(profiles):
    mp = profiles("jvm")
    assert mp.get_runtime() == "jvm"


def test_go_profile_is_byte_identical_regression(profiles):
    """Go expressions must match the previously hard-coded literals exactly."""
    mp = profiles("go")
    J = "target-service"
    assert mp.metric_expr("goroutine_count", J) == 'go_goroutines{job="target-service"}'
    assert mp.metric_expr("heap_inuse_bytes", J) == 'go_memstats_heap_inuse_bytes{job="target-service"}'
    assert mp.metric_expr("heap_alloc_bytes", J) == 'go_memstats_heap_alloc_bytes{job="target-service"}'
    assert mp.metric_expr("cpu_rate", J) == 'rate(process_cpu_seconds_total{job="target-service"}[5m])'
    assert mp.metric_expr("cpu_usage_pct", J) == 'rate(process_cpu_seconds_total{job="target-service"}[5m]) * 100'
    # Go has no GC-pressure signal
    assert mp.metric_expr("gc_pause_mean_seconds", J) is None


def test_jvm_profile_expressions(profiles):
    mp = profiles("jvm")
    J = "target-service-java"
    assert mp.metric_expr("goroutine_count", J) == 'jvm_threads_live_threads{job="target-service-java"}'
    assert mp.metric_expr("heap_inuse_bytes", J) == 'sum(jvm_memory_used_bytes{job="target-service-java", area="heap"})'
    assert mp.metric_expr("heap_alloc_bytes", J) == 'sum(jvm_memory_used_bytes{job="target-service-java", area="heap"})'
    # CPU comes out of Micrometer under the SAME name as Go
    assert mp.metric_expr("cpu_rate", J) == 'rate(process_cpu_seconds_total{job="target-service-java"}[5m])'
    # JVM has a GC-pressure signal
    gc = mp.metric_expr("gc_pause_mean_seconds", J)
    assert gc is not None and "jvm_gc_pause_seconds_sum" in gc


def test_explicit_runtime_override_arg(profiles):
    """The runtime= arg overrides the env for a single call."""
    mp = profiles("go")
    assert mp.metric_expr("goroutine_count", "svc", runtime="jvm") == 'jvm_threads_live_threads{job="svc"}'


def test_unknown_signal_returns_none(profiles):
    mp = profiles("jvm")
    assert mp.metric_expr("does_not_exist", "svc") is None

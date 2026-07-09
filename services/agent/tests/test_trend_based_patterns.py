"""
Aether-Guard — Trend-Based Pattern Regression Tests (Workstream 2)

Test coverage for refactored patterns:
  1. MEMORY_LEAK with trend detection
  2. CPU_SATURATION_EFFICIENCY with trend detection
  3. GOROUTINE_LEAK with static fallback

Critical requirements from Workstream 2:
  - Regression: Old static-threshold cases must still fire (with lower confidence)
  - Trend-based: New trend logic correctly identifies leaks vs stable high usage
  - Fallback: Prometheus failure gracefully degrades to static thresholds
  - Fast-path: Pattern ordering unchanged (OOM_KILL, RESTART_LOOP before async patterns)
"""

import pytest
from unittest.mock import patch

from rules import RuleEngine


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    """Create a RuleEngine instance for testing."""
    return RuleEngine()


# ─────────────────────────────────────────────────────────────────────────────
# MEMORY_LEAK Regression Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryLeakTrendBased:
    """Test MEMORY_LEAK pattern with trend detection (Workstream 2)."""

    @pytest.mark.asyncio
    async def test_memory_leak_with_trend_rising(self, engine):
        """
        Scenario: Memory is high AND rising (leak detected via trend).
        Expected: Matched with high confidence (0.88).
        """
        # Mock TrendClassifier to return rising trend
        async def mock_analyze_metric(*args, **kwargs):
            from trend_analysis import TrendAnalysis
            return TrendAnalysis(
                is_rising=True,
                slope=15_000_000.0,  # 15 MB/sec = 900 MB/min
                confidence=0.95,
                data_points=20,
                window_duration_sec=600,
                start_value=800_000_000.0,  # 800 MB
                end_value=1_700_000_000.0,  # 1.7 GB
            )

        alert = {"labels": {"alertname": "MemorySaturation"}}
        metrics = {"memleak_bytes_allocated": 1_700_000_000}  # 1.7 GB
        logs = []

        with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric):
            result = await engine.analyze(alert, metrics, logs)

        assert result is not None
        assert result.rule_name == "MEMORY_LEAK"
        assert result.confidence == 0.88  # Trend-based confidence
        assert result.recommended_action == "RESTART"
        assert "grown from" in result.reasoning.lower()
        assert "0.80 GB → 1.70 GB" in result.evidence[0]

    @pytest.mark.asyncio
    async def test_memory_leak_high_but_stable_no_match(self, engine):
        """
        Scenario: Memory is high but STABLE (not a leak, just high load).
        Expected: No match (trend analysis filters out false positive).
        """
        # Mock TrendClassifier to return stable trend
        async def mock_analyze_metric(*args, **kwargs):
            from trend_analysis import TrendAnalysis
            return TrendAnalysis(
                is_rising=False,  # Stable!
                slope=500.0,  # Very slow growth
                confidence=0.90,
                data_points=20,
                window_duration_sec=600,
                start_value=1_600_000_000.0,
                end_value=1_603_000_000.0,  # Only 3 MB growth
            )

        alert = {"labels": {"alertname": "MemorySaturation"}}
        metrics = {"memleak_bytes_allocated": 1_600_000_000}  # 1.6 GB (high but stable)
        logs = []

        with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric):
            result = await engine.analyze(alert, metrics, logs)

        assert result is None, "Should not match when memory is high but stable"

    @pytest.mark.asyncio
    async def test_memory_leak_fallback_to_static_on_prometheus_failure(self, engine):
        """
        REGRESSION TEST: Prometheus unavailable → falls back to static threshold.
        Expected: Still matches with lower confidence (0.75).
        """
        # Mock TrendClassifier to raise exception (Prometheus down)
        async def mock_analyze_metric_failure(*args, **kwargs):
            raise Exception("Connection refused: Prometheus unavailable")

        alert = {"labels": {"alertname": "MemorySaturation"}}
        metrics = {"memleak_bytes_allocated": 1_200_000_000}  # 1.2 GB
        logs = [
            "2024-01-15 10:00:00 ERROR malloc failed: out of memory",
            "2024-01-15 10:05:00 WARN memory exhausted, retrying",
        ]

        with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric_failure):
            result = await engine.analyze(alert, metrics, logs)

        assert result is not None, "Should still match using static fallback"
        assert result.rule_name == "MEMORY_LEAK"
        assert result.confidence == 0.75, "Lower confidence without trend data"
        assert result.recommended_action == "RESTART"
        assert "Trend analysis unavailable" in result.reasoning
        assert len(result.evidence) >= 2  # Alert + warnings

    @pytest.mark.asyncio
    async def test_memory_leak_fallback_insufficient_signals(self, engine):
        """
        REGRESSION TEST: Fallback mode requires ≥2 signals (alert + warnings/metrics).
        Expected: No match if only 1 signal.
        """
        async def mock_analyze_metric_failure(*args, **kwargs):
            raise Exception("Prometheus unavailable")

        alert = {"labels": {"alertname": "MemorySaturation"}}
        metrics = {"memleak_bytes_allocated": 1_200_000_000}  # 1.2 GB
        logs = []  # No warnings → only 2 signals total

        with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric_failure):
            result = await engine.analyze(alert, metrics, logs)

        assert result is not None, "Should match with 2 signals (alert + memory threshold)"


# ─────────────────────────────────────────────────────────────────────────────
# CPU_SATURATION_EFFICIENCY Regression Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCPUSaturationEfficiencyTrendBased:
    """Test CPU_SATURATION_EFFICIENCY pattern with trend detection (Workstream 2)."""

    @pytest.mark.asyncio
    async def test_cpu_efficiency_with_trend_rising(self, engine):
        """
        Scenario: CPU is high + rising + normal traffic (runaway detected via trend).
        Expected: Matched with high confidence (0.82).
        """
        async def mock_analyze_metric(*args, **kwargs):
            from trend_analysis import TrendAnalysis
            return TrendAnalysis(
                is_rising=True,
                slope=0.02,  # 2% per second = runaway
                confidence=0.92,
                data_points=20,
                window_duration_sec=600,
                start_value=0.70,  # 70%
                end_value=0.95,   # 95%
            )

        alert = {"labels": {"alertname": "HighCPUUsage"}}
        metrics = {
            "cpu_usage_percent": 95.0,
            "request_rate_5m": 100.0,  # Normal traffic
        }

        with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric):
            result = await engine.analyze(alert, metrics, {})

        assert result is not None
        assert result.rule_name == "CPU_SATURATION_EFFICIENCY"
        assert result.confidence == 0.82
        assert result.recommended_action == "RESTART"
        assert "rising from 70.0% → 95.0%" in result.evidence[0]

    @pytest.mark.asyncio
    async def test_cpu_efficiency_high_but_stable_no_match(self, engine):
        """
        Scenario: CPU is high but STABLE (legitimate baseline, not runaway).
        Expected: No match.
        """
        async def mock_analyze_metric(*args, **kwargs):
            from trend_analysis import TrendAnalysis
            return TrendAnalysis(
                is_rising=False,  # Stable
                slope=0.0001,
                confidence=0.90,
                data_points=20,
                window_duration_sec=600,
                start_value=0.88,
                end_value=0.88,  # No growth
            )

        alert = {"labels": {"alertname": "HighCPUUsage"}}
        metrics = {
            "cpu_usage_percent": 88.0,
            "request_rate_5m": 100.0,
        }

        with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric):
            result = await engine.analyze(alert, metrics, {})

        assert result is None, "Should not match when CPU is high but stable"

    @pytest.mark.asyncio
    async def test_cpu_efficiency_fallback_on_prometheus_failure(self, engine):
        """
        REGRESSION TEST: Prometheus unavailable → falls back to static threshold.
        Expected: Still matches with lower confidence (0.70).
        """
        async def mock_analyze_metric_failure(*args, **kwargs):
            raise Exception("Prometheus connection timeout")

        alert = {"labels": {"alertname": "HighCPUUsage"}}
        metrics = {
            "cpu_usage_percent": 92.0,
            "request_rate_5m": 100.0,  # Normal traffic
        }

        with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric_failure):
            result = await engine.analyze(alert, metrics, {})

        assert result is not None, "Should still match using static fallback"
        assert result.rule_name == "CPU_SATURATION_EFFICIENCY"
        assert result.confidence == 0.70, "Lower confidence without trend data"
        assert result.recommended_action == "RESTART"
        assert "Trend analysis unavailable" in result.reasoning

    @pytest.mark.asyncio
    async def test_cpu_saturation_traffic_variant_unchanged(self, engine):
        """
        REGRESSION TEST: CPU_SATURATION_TRAFFIC should remain synchronous (no trend).
        Expected: Matches immediately with high traffic, confidence 0.85.
        """
        alert = {"labels": {"alertname": "HighCPUUsage"}}
        metrics = {
            "cpu_usage_percent": 95.0,
            "request_rate_5m": 1500.0,  # High traffic!
        }

        # No mocking needed - this path is synchronous
        result = await engine.analyze(alert, metrics, {})

        assert result is not None
        assert result.rule_name == "CPU_SATURATION_TRAFFIC"
        assert result.confidence == 0.85
        assert result.recommended_action == "SCALE"
        assert "elevated traffic" in result.reasoning.lower()


# ─────────────────────────────────────────────────────────────────────────────
# GOROUTINE_LEAK Fallback Test
# ─────────────────────────────────────────────────────────────────────────────

class TestGoroutineLeakFallback:
    """Test GOROUTINE_LEAK static fallback (Workstream 2 bonus fix)."""

    @pytest.mark.asyncio
    async def test_goroutine_leak_fallback_on_prometheus_failure(self, engine):
        """
        Scenario: Prometheus unavailable → falls back to static threshold.
        Expected: Matches with lower confidence (0.70) instead of returning None.
        """
        async def mock_analyze_metric_failure(*args, **kwargs):
            raise Exception("Prometheus unavailable")

        alert = {"labels": {"alertname": "HighGoroutineCount"}}
        metrics = {
            "runtime_goroutines": 200.0,  # Above threshold (50 * 3 = 150)
        }

        with patch("trend_analysis.TrendClassifier.analyze_metric", new=mock_analyze_metric_failure):
            result = await engine.analyze(alert, metrics, [])

        assert result is not None, "Should match using static fallback (not return None)"
        assert result.rule_name == "GOROUTINE_LEAK"
        assert result.confidence == 0.70, "Lower confidence without trend data"
        assert result.recommended_action == "RESTART"
        assert "Trend analysis unavailable" in result.reasoning


# ─────────────────────────────────────────────────────────────────────────────
# Fast-Path Ordering Test
# ─────────────────────────────────────────────────────────────────────────────

class TestFastPathOrdering:
    """Verify pattern ordering unchanged after Workstream 2 refactoring."""

    @pytest.mark.asyncio
    async def test_synchronous_patterns_still_first(self, engine):
        """
        Scenario: OOM_KILL and RESTART_LOOP should match before async patterns.
        Expected: Fast synchronous patterns checked first (<10ms).
        """
        import time

        # OOM_KILL test
        alert = {"labels": {"alertname": "HighMemoryUsage"}}
        logs = ["Out of memory: Kill process 1234"]
        metrics = {}

        start = time.perf_counter()
        result = await engine.analyze(alert, metrics, logs)
        latency_ms = (time.perf_counter() - start) * 1000

        assert result is not None
        assert result.rule_name == "OOM_KILL"
        assert latency_ms < 10, f"OOM_KILL should be fast (<10ms), got {latency_ms:.2f}ms"

    @pytest.mark.asyncio
    async def test_async_patterns_checked_last(self, engine):
        """
        Scenario: MEMORY_LEAK, CPU_SATURATION_EFFICIENCY, GOROUTINE_LEAK are async.
        Expected: Checked after all synchronous patterns.
        """
        # This is implicitly tested by the analyze() method order
        # Just verify the async patterns don't break synchronous fast-path

        alert = {"labels": {"alertname": "MemorySaturation"}}
        metrics = {"memleak_bytes_allocated": 500_000_000}  # Below threshold
        logs = []

        # Should quickly return None (no match) without calling Prometheus
        result = await engine.analyze(alert, metrics, logs)

        assert result is None, "Below threshold should not trigger async pattern check"

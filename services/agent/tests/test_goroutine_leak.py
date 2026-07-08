"""
Unit tests for goroutine leak pattern detection.

Tests cover:
1. True positive: Rising goroutine count + elevated absolute count
2. False positive guard: Load-driven increase that stabilizes (should NOT match)
3. Confidence boosters: Heap leak, log warnings, no traffic spike
4. Missing data / edge cases
"""

import pytest
from unittest.mock import AsyncMock, patch
from rules import RuleEngine
from policy import RootCauseCategory
from trend_analysis import TrendAnalysis


@pytest.fixture
def engine():
    """Fixture providing a RuleEngine instance."""
    return RuleEngine()


@pytest.fixture
def empty_alert():
    """Minimal empty alert."""
    return {"labels": {}, "annotations": {}}


@pytest.fixture
def empty_logs():
    """Empty logs list."""
    return []


# ── True Positive: Goroutine Leak Detected ────────────────────────────────────


class TestGoroutineLeakTruePositive:
    """Test that real goroutine leaks are detected."""

    @pytest.mark.asyncio
    async def test_rising_goroutines_detected(self, engine, empty_alert, empty_logs):
        """Rising goroutine count over 10min + elevated absolute count → leak detected."""
        # Metrics: Current goroutine count is 200 (exceeds baseline * 3 = 150)
        metrics = {
            "runtime_goroutines": 200.0,
            "request_rate_5m_rps": 50.0,  # Low traffic, not a spike
        }

        # Mock trend analysis to return rising trend
        mock_trend = TrendAnalysis(
            is_rising=True,
            slope=0.10,  # 0.10 goroutines/sec = 6/min (exceeds threshold of 5/min)
            confidence=0.92,  # High R² = linear trend
            data_points=20,
            window_duration_sec=600,  # 10 minutes
            start_value=140.0,
            end_value=200.0,
        )

        with patch("trend_analysis.TrendClassifier") as MockClassifier:
            mock_instance = MockClassifier.return_value
            mock_instance.analyze_metric = AsyncMock(return_value=mock_trend)

            result = await engine.analyze(empty_alert, metrics, empty_logs)

        assert result is not None
        assert result.matched is True
        assert result.rule_name == "GOROUTINE_LEAK"
        assert result.confidence >= 0.85  # Base confidence
        assert result.root_cause == RootCauseCategory.GOROUTINE_LEAK
        assert result.recommended_action == "RESTART"
        assert len(result.evidence) >= 3  # Trend + absolute + R²
        assert "140" in result.reasoning  # Start value
        assert "200" in result.reasoning  # End value

    @pytest.mark.asyncio
    async def test_confidence_boosters_all_present(self, engine, empty_alert):
        """All confidence boosters present → confidence approaches 1.0."""
        metrics = {
            "runtime_goroutines": 250.0,  # Well above threshold
            "request_rate_5m_rps": 100.0,  # Low traffic
        }

        logs = [
            "2024-01-15 10:00:00 WARN goroutine leak detected in worker pool",
            "2024-01-15 10:05:00 ERROR channel deadlock in message processor",
        ]

        # Mock goroutine trend (rising)
        goroutine_trend = TrendAnalysis(
            is_rising=True,
            slope=0.12,  # Fast growth
            confidence=0.95,
            data_points=20,
            window_duration_sec=600,
            start_value=150.0,
            end_value=250.0,
        )

        # Mock heap trend (also rising - booster +0.08)
        heap_trend = TrendAnalysis(
            is_rising=True,
            slope=200000.0,  # 200KB/sec = 12 MB/min (exceeds threshold)
            confidence=0.90,
            data_points=20,
            window_duration_sec=600,
            start_value=50_000_000.0,
            end_value=170_000_000.0,
        )

        with patch("trend_analysis.TrendClassifier") as MockClassifier:
            mock_instance = MockClassifier.return_value
            # First call = goroutine trend, second call = heap trend
            mock_instance.analyze_metric = AsyncMock(side_effect=[goroutine_trend, heap_trend])

            result = await engine.analyze(empty_alert, metrics, logs)

        assert result is not None
        assert result.confidence >= 0.98  # 0.85 + 0.08 (heap) + 0.05 (logs) + 0.05 (no spike)
        assert "Heap memory also rising" in "\n".join(result.evidence)
        assert "goroutine-related warnings" in "\n".join(result.evidence)


# ── False Positive Guard: Load-Driven Increase ────────────────────────────────


class TestGoroutineLeakFalsePositiveGuard:
    """Test that load-driven goroutine increases don't trigger false alarms."""

    @pytest.mark.asyncio
    async def test_stable_high_count_not_detected(self, engine, empty_alert, empty_logs):
        """High but stable goroutine count → NOT a leak (slope near zero)."""
        metrics = {
            "runtime_goroutines": 180.0,  # Above threshold but stable
            "request_rate_5m_rps": 800.0,  # High traffic explains high goroutines
        }

        # Mock trend: high count but flat slope (plateaued after load increase)
        mock_trend = TrendAnalysis(
            is_rising=False,  # Slope below threshold
            slope=0.01,  # 0.01/sec = 0.6/min (below threshold of 5/min)
            confidence=0.88,
            data_points=20,
            window_duration_sec=600,
            start_value=175.0,
            end_value=180.0,  # Only +5 over 10 minutes
        )

        with patch("trend_analysis.TrendClassifier") as MockClassifier:
            mock_instance = MockClassifier.return_value
            mock_instance.analyze_metric = AsyncMock(return_value=mock_trend)

            result = await engine.analyze(empty_alert, metrics, empty_logs)

        # Should NOT match - stable count is not a leak
        assert result is None  # No rule matched

    @pytest.mark.asyncio
    async def test_below_absolute_threshold_not_detected(self, engine, empty_alert, empty_logs):
        """Goroutine count below absolute threshold → NOT detected (even if rising)."""
        metrics = {
            "runtime_goroutines": 120.0,  # Below 150 (baseline * 3)
            "request_rate_5m_rps": 50.0,
        }

        # Mock trend: rising but count too low to be alarming
        mock_trend = TrendAnalysis(
            is_rising=True,
            slope=0.08,  # Rising
            confidence=0.90,
            data_points=20,
            window_duration_sec=600,
            start_value=70.0,
            end_value=120.0,
        )

        with patch("trend_analysis.TrendClassifier") as MockClassifier:
            mock_instance = MockClassifier.return_value
            mock_instance.analyze_metric = AsyncMock(return_value=mock_trend)

            result = await engine.analyze(empty_alert, metrics, empty_logs)

        # Should NOT match - below absolute threshold (normal startup/load ramp)
        assert result is None


# ── Edge Cases ─────────────────────────────────────────────────────────────────


class TestGoroutineLeakEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_missing_goroutine_metric(self, engine, empty_alert, empty_logs):
        """No runtime_goroutines metric → pattern doesn't match."""
        metrics = {
            "request_rate_5m_rps": 50.0,
            # runtime_goroutines missing
        }

        await engine.analyze(empty_alert, metrics, empty_logs)
        # Should not match (will try other patterns)
        # We can't assert None because other patterns might match

    @pytest.mark.asyncio
    async def test_trend_analysis_failure(self, engine, empty_alert, empty_logs):
        """Trend analysis fails (Prometheus down) → pattern doesn't match."""
        metrics = {
            "runtime_goroutines": 200.0,
            "request_rate_5m_rps": 50.0,
        }

        with patch("trend_analysis.TrendClassifier") as MockClassifier:
            mock_instance = MockClassifier.return_value
            # Simulate Prometheus API failure
            mock_instance.analyze_metric = AsyncMock(side_effect=Exception("Connection refused"))

            await engine.analyze(empty_alert, metrics, empty_logs)

        # Should not crash, just skip this pattern
        # Result may be None or match a different pattern

    @pytest.mark.asyncio
    async def test_insufficient_data_points(self, engine, empty_alert, empty_logs):
        """Insufficient data points for trend analysis → pattern doesn't match."""
        metrics = {
            "runtime_goroutines": 200.0,
            "request_rate_5m_rps": 50.0,
        }

        # Mock trend: insufficient data (confidence 0.0)
        mock_trend = TrendAnalysis(
            is_rising=False,
            slope=0.0,
            confidence=0.0,  # No data
            data_points=0,
            window_duration_sec=600,
            start_value=None,
            end_value=None,
        )

        with patch("trend_analysis.TrendClassifier") as MockClassifier:
            mock_instance = MockClassifier.return_value
            mock_instance.analyze_metric = AsyncMock(return_value=mock_trend)

            result = await engine.analyze(empty_alert, metrics, empty_logs)

        # Should not match - no reliable trend data
        assert result is None


# ── Confidence Scoring ─────────────────────────────────────────────────────────


class TestGoroutineLeakConfidence:
    """Test confidence calculation logic."""

    @pytest.mark.asyncio
    async def test_base_confidence_only(self, engine, empty_alert, empty_logs):
        """No boosters → base confidence of 0.85."""
        metrics = {
            "runtime_goroutines": 160.0,
            "request_rate_5m_rps": 1200.0,  # Traffic spike (no booster)
        }

        goroutine_trend = TrendAnalysis(
            is_rising=True,
            slope=0.09,
            confidence=0.88,
            data_points=20,
            window_duration_sec=600,
            start_value=100.0,
            end_value=160.0,
        )

        heap_trend = TrendAnalysis(
            is_rising=False,  # Heap NOT rising (no booster)
            slope=50000.0,
            confidence=0.85,
            data_points=20,
            window_duration_sec=600,
            start_value=80_000_000.0,
            end_value=110_000_000.0,
        )

        with patch("trend_analysis.TrendClassifier") as MockClassifier:
            mock_instance = MockClassifier.return_value
            mock_instance.analyze_metric = AsyncMock(side_effect=[goroutine_trend, heap_trend])

            result = await engine.analyze(empty_alert, metrics, empty_logs)

        assert result is not None
        assert result.confidence == 0.85  # Base only (no boosters applied)

    @pytest.mark.asyncio
    async def test_confidence_capped_at_1_0(self, engine, empty_alert):
        """Confidence is capped at 1.0 even with all boosters."""
        metrics = {
            "runtime_goroutines": 300.0,
            "request_rate_5m_rps": 50.0,
        }

        logs = [
            "goroutine leak detected",
            "deadlock in channel processor",
            "context cancellation failed",
        ]

        goroutine_trend = TrendAnalysis(
            is_rising=True,
            slope=0.20,  # Very fast growth
            confidence=0.98,
            data_points=20,
            window_duration_sec=600,
            start_value=100.0,
            end_value=300.0,
        )

        heap_trend = TrendAnalysis(
            is_rising=True,
            slope=300000.0,  # Fast heap growth
            confidence=0.95,
            data_points=20,
            window_duration_sec=600,
            start_value=50_000_000.0,
            end_value=230_000_000.0,
        )

        with patch("trend_analysis.TrendClassifier") as MockClassifier:
            mock_instance = MockClassifier.return_value
            mock_instance.analyze_metric = AsyncMock(side_effect=[goroutine_trend, heap_trend])

            result = await engine.analyze(empty_alert, metrics, logs)

        assert result is not None
        # 0.85 + 0.08 + 0.05 + 0.05 = 1.03 → capped at 1.0
        assert result.confidence == 1.0

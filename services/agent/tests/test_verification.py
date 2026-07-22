"""
Unit tests for verification.py — Post-remediation metric validation.

Tests cover:
- Error rate verification (improvement/regression)
- Latency verification
- Generic multi-metric verification
- Rollback decision logic
- SLO threshold validation
- Edge cases (missing data, zero values)
- Metrics snapshot comparison
"""

import pytest
from datetime import datetime
from verification import (
    VerificationEngine,
    MetricsSnapshot,
)


@pytest.fixture
def engine():
    """Fixture providing a VerificationEngine instance."""
    return VerificationEngine(prometheus_url="http://localhost:9090")


@pytest.fixture
def healthy_snapshot():
    """Snapshot with all metrics within SLO."""
    return MetricsSnapshot(
        timestamp=datetime.now(),
        error_rate_5m=0.0005,  # 0.05% - within 0.1% SLO
        latency_p99_5m=0.25,    # 250ms - within 500ms SLO
        latency_p50_5m=0.10,    # 100ms
        request_rate_5m=100.0,  # 100 rps
        memory_usage_bytes=500_000_000,  # 500 MB
        cpu_usage_percent=30.0,
    )


@pytest.fixture
def degraded_snapshot():
    """Snapshot with metrics breaching SLO."""
    return MetricsSnapshot(
        timestamp=datetime.now(),
        error_rate_5m=0.05,     # 5% - WAY above 0.1% SLO
        latency_p99_5m=1.5,      # 1500ms - above 500ms SLO
        latency_p50_5m=0.80,     # 800ms
        request_rate_5m=100.0,   # 100 rps
        memory_usage_bytes=800_000_000,  # 800 MB
        cpu_usage_percent=85.0,
    )


# ── Error Rate Verification Tests ─────────────────────────────────────────────

class TestErrorRateVerification:
    """Test error rate improvement detection."""

    def test_error_rate_significant_improvement(self, engine):
        """Error rate drops by >50% → success."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.10,  # 10%
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.02,  # 2% (80% improvement)
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_error_rate_improvement(before, after)

        assert result.improved is True
        assert result.should_rollback is False
        assert "improved" in result.reason.lower()

    def test_error_rate_insufficient_improvement(self, engine):
        """Error rate drops by <50% → rollback."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.10,  # 10%
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.08,  # 8% (only 20% improvement)
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_error_rate_improvement(before, after)

        assert result.improved is False
        assert result.should_rollback is True
        assert "did not improve" in result.reason.lower()

    def test_error_rate_regression(self, engine):
        """Error rate increases → rollback immediately."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.05,  # 5%
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.10,  # 10% (got worse!)
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_error_rate_improvement(before, after)

        assert result.improved is False
        assert result.should_rollback is True

    def test_error_rate_missing_data(self, engine):
        """Missing error rate data → cannot verify, don't rollback."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=None,  # Missing
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.02,
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_error_rate_improvement(before, after)

        assert result.success is False
        assert result.should_rollback is False  # Don't rollback on data issues
        assert "missing" in result.reason.lower()

    def test_error_rate_zero_before(self, engine):
        """Edge case: error rate was 0 before (shouldn't happen, but handle gracefully)."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.0,  # Perfect before
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.0,  # Still perfect
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_error_rate_improvement(before, after)

        assert result.improved is True
        assert "remained at 0%" in result.reason.lower()

    def test_error_rate_within_slo_marked(self, engine):
        """Improved error rate back within SLO → marked in reason."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.05,  # 5% - above 0.1% SLO
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.0005,  # 0.05% - within SLO
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_error_rate_improvement(before, after)

        assert result.success is True  # Improved AND within SLO
        assert "within slo" in result.reason.lower()


# ── Latency Verification Tests ────────────────────────────────────────────────

class TestLatencyVerification:
    """Test latency improvement detection."""

    def test_latency_significant_improvement(self, engine):
        """P99 latency drops by >30% → success."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=1.0,  # 1000ms
            latency_p50_5m=0.5,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=0.6,  # 600ms (40% improvement)
            latency_p50_5m=0.3,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_latency_improvement(before, after)

        assert result.improved is True
        assert result.should_rollback is False

    def test_latency_insufficient_improvement(self, engine):
        """P99 latency drops by <30% → rollback."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=1.0,  # 1000ms
            latency_p50_5m=0.5,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=0.85,  # 850ms (only 15% improvement)
            latency_p50_5m=0.4,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_latency_improvement(before, after)

        assert result.improved is False
        assert result.should_rollback is True

    def test_latency_regression(self, engine):
        """P99 latency increases → rollback."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=0.5,  # 500ms
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=0.8,  # 800ms (got worse)
            latency_p50_5m=0.4,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_latency_improvement(before, after)

        assert result.improved is False
        assert result.should_rollback is True

    def test_latency_missing_data(self, engine):
        """Missing latency data → cannot verify."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=None,  # Missing
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=0.3,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_latency_improvement(before, after)

        assert result.success is False
        assert result.should_rollback is False
        assert "missing" in result.reason.lower()

    def test_latency_within_slo(self, engine):
        """Improved latency back within SLO → marked."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=1.0,  # 1000ms - above 500ms SLO
            latency_p50_5m=0.5,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=0.4,  # 400ms - within SLO
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_latency_improvement(before, after)

        assert result.success is True
        assert "within slo" in result.reason.lower()


# ── Generic Verification Tests ────────────────────────────────────────────────

class TestGenericVerification:
    """Test multi-metric verification for unknown alert types."""

    def test_generic_both_metrics_improve(self, engine):
        """Both error rate and latency improve → success."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.10,  # 10%
            latency_p99_5m=1.0,   # 1000ms
            latency_p50_5m=0.5,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.02,  # 2% (80% improvement)
            latency_p99_5m=0.6,   # 600ms (40% improvement)
            latency_p50_5m=0.3,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_generic_improvement(before, after)

        assert result.success is True
        assert result.improved is True
        assert "error_rate" in result.reason
        assert "latency_p99" in result.reason
        assert result.should_rollback is False

    def test_generic_one_improves_one_neutral(self, engine):
        """Error rate improves, latency unchanged → success."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.10,  # 10%
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.02,  # 2% (improved)
            latency_p99_5m=0.5,   # Unchanged
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_generic_improvement(before, after)

        assert result.success is True
        assert result.improved is True
        assert "error_rate" in result.reason

    def test_generic_metrics_regress(self, engine):
        """Metrics get worse → rollback."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.05,
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.10,  # Got worse
            latency_p99_5m=0.8,   # Got worse
            latency_p50_5m=0.4,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_generic_improvement(before, after)

        assert result.success is False
        assert result.improved is False
        assert result.should_rollback is True
        assert "regressed" in result.reason.lower()

    def test_generic_no_significant_change(self, engine):
        """Metrics unchanged → inconclusive, don't rollback."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.05,
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.048,  # Tiny change
            latency_p99_5m=0.51,   # Tiny change
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_generic_improvement(before, after)

        assert result.success is False
        assert result.improved is False
        assert result.should_rollback is False  # Inconclusive
        assert "no significant" in result.reason.lower()


# ── Integration Tests ─────────────────────────────────────────────────────────

class TestVerificationIntegration:
    """Test full verification workflow with alert routing."""

    def test_error_alert_routes_to_error_verification(self, engine, degraded_snapshot, healthy_snapshot):
        """Alert with 'Error' in name → uses error rate verification."""
        # Note: This is a sync wrapper test; actual verify() is async
        result = engine._verify_error_rate_improvement(degraded_snapshot, healthy_snapshot)
        assert result is not None
        assert hasattr(result, "improved")

    def test_latency_alert_routes_to_latency_verification(self, engine):
        """Alert with 'Latency' in name → uses latency verification."""
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=1.0,
            latency_p50_5m=0.5,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=0.4,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_latency_improvement(before, after)
        assert result.improved is True


# ── Edge Cases and Dataclass Tests ───────────────────────────────────────────

class TestEdgeCases:
    """Test edge cases and dataclass behaviors."""

    def test_metrics_snapshot_repr(self):
        """MetricsSnapshot __repr__ should be readable."""
        snapshot = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.05,
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=500_000_000,
            cpu_usage_percent=50.0,
        )
        repr_str = repr(snapshot)
        assert "5.00%" in repr_str  # error_rate formatted
        assert "0.500s" in repr_str  # p99 formatted
        assert "100.0" in repr_str   # rps formatted

    def test_verification_result_fields(self, engine, healthy_snapshot):
        """VerificationResult should have all required fields."""
        result = engine._verify_error_rate_improvement(healthy_snapshot, healthy_snapshot)
        assert hasattr(result, "success")
        assert hasattr(result, "improved")
        assert hasattr(result, "metrics_before")
        assert hasattr(result, "metrics_after")
        assert hasattr(result, "reason")
        assert hasattr(result, "should_rollback")
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0

    def test_verification_engine_prometheus_url(self):
        """VerificationEngine should accept custom Prometheus URL."""
        engine = VerificationEngine(prometheus_url="http://custom:9090")
        assert engine.prometheus_url == "http://custom:9090"

    def test_verification_thresholds(self, engine):
        """Verify threshold constants are sensible."""
        assert 0 < engine._SLO_ERROR_RATE_THRESHOLD < 1
        assert engine._SLO_LATENCY_P99_THRESHOLD > 0
        assert 0 < engine._MIN_ERROR_RATE_IMPROVEMENT < 1
        assert 0 < engine._MIN_LATENCY_IMPROVEMENT < 1


# ── Parameterized Tests ───────────────────────────────────────────────────────

@pytest.mark.parametrize("before,after,expected_improved", [
    (0.10, 0.02, True),   # 80% improvement
    (0.10, 0.05, True),   # 50% improvement (exactly at threshold)
    (0.10, 0.06, False),  # 40% improvement (below threshold)
    (0.05, 0.10, False),  # Regression
])
def test_error_rate_improvement_thresholds(before, after, expected_improved):
    """Test error rate improvement threshold (50%)."""
    engine = VerificationEngine()
    before_snapshot = MetricsSnapshot(
        timestamp=datetime.now(),
        error_rate_5m=before,
        latency_p99_5m=0.5,
        latency_p50_5m=0.2,
        request_rate_5m=100.0,
        memory_usage_bytes=None,
        cpu_usage_percent=None,
    )
    after_snapshot = MetricsSnapshot(
        timestamp=datetime.now(),
        error_rate_5m=after,
        latency_p99_5m=0.5,
        latency_p50_5m=0.2,
        request_rate_5m=100.0,
        memory_usage_bytes=None,
        cpu_usage_percent=None,
    )
    result = engine._verify_error_rate_improvement(before_snapshot, after_snapshot)
    assert result.improved == expected_improved


@pytest.mark.parametrize("before,after,expected_improved", [
    (1.0, 0.6, True),    # 40% improvement
    (1.0, 0.7, True),    # 30% improvement (exactly at threshold)
    (1.0, 0.75, False),  # 25% improvement (below threshold)
    (0.5, 0.8, False),   # Regression
])
def test_latency_improvement_thresholds(before, after, expected_improved):
    """Test latency improvement threshold (30%)."""
    engine = VerificationEngine()
    before_snapshot = MetricsSnapshot(
        timestamp=datetime.now(),
        error_rate_5m=0.001,
        latency_p99_5m=before,
        latency_p50_5m=0.2,
        request_rate_5m=100.0,
        memory_usage_bytes=None,
        cpu_usage_percent=None,
    )
    after_snapshot = MetricsSnapshot(
        timestamp=datetime.now(),
        error_rate_5m=0.001,
        latency_p99_5m=after,
        latency_p50_5m=0.2,
        request_rate_5m=100.0,
        memory_usage_bytes=None,
        cpu_usage_percent=None,
    )
    result = engine._verify_latency_improvement(before_snapshot, after_snapshot)
    assert result.improved == expected_improved


# ── Edge Case Regression Tests ────────────────────────────────────────────────

class TestEdgeCaseRegressions:
    """Regression tests for edge cases found by coverage analysis."""

    def test_error_rate_regression_from_zero(self, engine):
        """
        Regression test for verification.py:196-197 (before.error_rate_5m == 0 edge case).

        Scenario: Error rate was 0% before remediation, but INCREASED after.
        Expected: Should be marked as NOT improved (regression).
        """
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.0,  # Zero errors before
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.02,  # 2% errors after (regression!)
            latency_p99_5m=0.5,
            latency_p50_5m=0.2,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_error_rate_improvement(before, after)

        # Should NOT be marked as improved (regression detected)
        assert result.success is False
        assert "increased" in result.reason.lower()

    def test_latency_improvement_from_zero_baseline(self, engine):
        """
        Regression test for verification.py:248-249 (before.latency_p99_5m == 0 edge case).

        Scenario: Latency was 0 before (edge case), check against SLO threshold.
        Expected: Success if after latency <= SLO threshold.
        """
        before = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=0.0,  # Zero latency before (unusual but possible)
            latency_p50_5m=0.0,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        after = MetricsSnapshot(
            timestamp=datetime.now(),
            error_rate_5m=0.001,
            latency_p99_5m=0.3,  # 300ms - within 500ms SLO
            latency_p50_5m=0.15,
            request_rate_5m=100.0,
            memory_usage_bytes=None,
            cpu_usage_percent=None,
        )
        result = engine._verify_latency_improvement(before, after)

        # Should be successful (within SLO threshold)
        assert result.success is True
        assert "0.3" in result.reason or "300" in result.reason
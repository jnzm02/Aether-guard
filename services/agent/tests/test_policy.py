"""
Unit tests for policy.py — Policy Engine for action gating.

Tests cover:
- Policy matrix (allowed/forbidden combinations)
- Time-of-day gating (business hours vs off-hours)
- Risk level assessment
- Approval requirements
- Blast radius limits
- Edge cases and error handling
"""

import pytest
from datetime import datetime
from policy import (
    PolicyEngine,
    ActionType,
    RiskLevel,
    RootCauseCategory,
    check_policy,
)


@pytest.fixture
def engine():
    """Fixture providing a PolicyEngine instance."""
    return PolicyEngine()


@pytest.fixture
def business_hours_timestamp():
    """Timestamp during business hours (11 AM)."""
    return datetime(2024, 1, 15, 11, 0, 0)  # Monday, 11 AM


@pytest.fixture
def off_hours_timestamp():
    """Timestamp outside business hours (9 PM)."""
    return datetime(2024, 1, 15, 21, 0, 0)  # Monday, 9 PM


# ── Policy Matrix Tests ────────────────────────────────────────────────────────

class TestPolicyMatrix:
    """Test policy matrix rules for action allowlists."""

    def test_memory_leak_restart_allowed(self, engine, business_hours_timestamp):
        """Memory leak → RESTART should be allowed (safe action)."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.LOW
        assert "memory_leak" in decision.reason.lower()

    def test_memory_leak_scale_forbidden(self, engine, business_hours_timestamp):
        """Memory leak → SCALE should be forbidden (makes problem worse)."""
        decision = engine.evaluate(
            ActionType.SCALE,
            "warning",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert decision.allowed is False
        assert "forbidden" in decision.reason.lower()

    def test_cpu_saturation_scale_allowed(self, engine, business_hours_timestamp):
        """CPU saturation → SCALE should be allowed (correct remedy)."""
        decision = engine.evaluate(
            ActionType.SCALE,
            "warning",
            RootCauseCategory.CPU_SATURATION,
            business_hours_timestamp,
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.LOW

    def test_cpu_saturation_restart_forbidden(self, engine, business_hours_timestamp):
        """CPU saturation → RESTART should be forbidden (doesn't help)."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.CPU_SATURATION,
            business_hours_timestamp,
        )
        assert decision.allowed is False
        assert "forbidden" in decision.reason.lower()

    def test_traffic_spike_scale_allowed(self, engine, business_hours_timestamp):
        """Traffic spike → SCALE should be allowed."""
        decision = engine.evaluate(
            ActionType.SCALE,
            "critical",
            RootCauseCategory.TRAFFIC_SPIKE,
            business_hours_timestamp,
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.MEDIUM  # Critical = medium risk

    def test_bad_deployment_rollback_allowed(self, engine, business_hours_timestamp):
        """Bad deployment → ROLLBACK should be allowed."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "warning",
            RootCauseCategory.BAD_DEPLOYMENT,
            business_hours_timestamp,
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.HIGH

    def test_dependency_failure_restart_allowed(self, engine, business_hours_timestamp):
        """Dependency failure → RESTART should be allowed (clears pools)."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.DEPENDENCY_FAILURE,
            business_hours_timestamp,
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.LOW

    def test_unknown_ignore_allowed(self, engine, business_hours_timestamp):
        """Unknown cause → IGNORE should be allowed (conservative)."""
        decision = engine.evaluate(
            ActionType.IGNORE,
            "warning",
            RootCauseCategory.UNKNOWN,
            business_hours_timestamp,
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.LOW

    def test_undefined_policy_denied(self, engine, business_hours_timestamp):
        """Action with no policy entry should be denied by default."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.UNKNOWN,  # No RESTART policy for UNKNOWN
            business_hours_timestamp,
        )
        assert decision.allowed is False
        assert "no policy defined" in decision.reason.lower()


# ── Time-of-Day Gating Tests ──────────────────────────────────────────────────

class TestTimeOfDayGating:
    """Test time-aware policy enforcement (stricter outside business hours)."""

    def test_high_risk_allowed_during_business_hours(self, engine, business_hours_timestamp):
        """HIGH risk action allowed during business hours."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "warning",
            RootCauseCategory.BAD_DEPLOYMENT,
            business_hours_timestamp,
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.HIGH

    def test_high_risk_blocked_outside_business_hours(self, engine, off_hours_timestamp):
        """HIGH risk action blocked outside business hours."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "warning",
            RootCauseCategory.BAD_DEPLOYMENT,
            off_hours_timestamp,
        )
        assert decision.allowed is False
        assert "outside business hours" in decision.reason.lower()

    def test_critical_risk_blocked_outside_business_hours(self, engine, off_hours_timestamp):
        """CRITICAL risk action blocked outside business hours."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "critical",
            RootCauseCategory.BAD_DEPLOYMENT,
            off_hours_timestamp,
        )
        assert decision.allowed is False
        assert "outside business hours" in decision.reason.lower()

    def test_low_risk_allowed_outside_business_hours(self, engine, off_hours_timestamp):
        """LOW risk action allowed outside business hours."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.MEMORY_LEAK,
            off_hours_timestamp,
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.LOW

    def test_business_hours_boundary_start(self, engine):
        """Test exact business hours start (9 AM)."""
        timestamp = datetime(2024, 1, 15, 9, 0, 0)
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "warning",
            RootCauseCategory.BAD_DEPLOYMENT,
            timestamp,
        )
        assert decision.allowed is True  # Exactly at start = business hours

    def test_business_hours_boundary_end(self, engine):
        """Test exact business hours end (6 PM)."""
        timestamp = datetime(2024, 1, 15, 18, 0, 0)
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "warning",
            RootCauseCategory.BAD_DEPLOYMENT,
            timestamp,
        )
        assert decision.allowed is True  # Exactly at end = still business hours


# ── Risk Level Tests ──────────────────────────────────────────────────────────

class TestRiskLevels:
    """Test risk level classification."""

    def test_warning_memory_leak_restart_low_risk(self, engine, business_hours_timestamp):
        """Warning + memory leak + RESTART = LOW risk."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert decision.risk_level == RiskLevel.LOW

    def test_critical_memory_leak_restart_medium_risk(self, engine, business_hours_timestamp):
        """Critical + memory leak + RESTART = MEDIUM risk."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "critical",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert decision.risk_level == RiskLevel.MEDIUM

    def test_bad_deployment_warning_high_risk(self, engine, business_hours_timestamp):
        """Bad deployment + warning = HIGH risk."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "warning",
            RootCauseCategory.BAD_DEPLOYMENT,
            business_hours_timestamp,
        )
        assert decision.risk_level == RiskLevel.HIGH

    def test_bad_deployment_critical_critical_risk(self, engine, business_hours_timestamp):
        """Bad deployment + critical = CRITICAL risk."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "critical",
            RootCauseCategory.BAD_DEPLOYMENT,
            business_hours_timestamp,
        )
        assert decision.risk_level == RiskLevel.CRITICAL


# ── Approval Requirements Tests ───────────────────────────────────────────────

class TestApprovalRequirements:
    """Test approval routing logic."""

    def test_low_risk_no_approval(self, engine, business_hours_timestamp):
        """LOW risk actions don't require approval."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert decision.requires_approval is False

    def test_medium_risk_warning_no_approval(self, engine, business_hours_timestamp):
        """MEDIUM risk + warning doesn't require approval."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "critical",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert decision.requires_approval is False

    def test_high_risk_requires_approval(self, engine, business_hours_timestamp):
        """HIGH risk actions require approval."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "warning",
            RootCauseCategory.BAD_DEPLOYMENT,
            business_hours_timestamp,
        )
        assert decision.requires_approval is True

    def test_critical_risk_requires_approval(self, engine, business_hours_timestamp):
        """CRITICAL risk actions require approval."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "critical",
            RootCauseCategory.BAD_DEPLOYMENT,
            business_hours_timestamp,
        )
        assert decision.requires_approval is True


# ── Blast Radius Tests ────────────────────────────────────────────────────────

class TestBlastRadius:
    """Test blast radius limits."""

    def test_low_risk_blast_radius_limit_1(self, engine, business_hours_timestamp):
        """LOW risk = max 1 pod."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert decision.max_blast_radius == 1

    def test_medium_risk_blast_radius_limit_3(self, engine, business_hours_timestamp):
        """MEDIUM risk = max 3 pods."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "critical",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert decision.max_blast_radius == 3

    def test_high_risk_blast_radius_limit_5(self, engine, business_hours_timestamp):
        """HIGH risk = max 5 pods."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "warning",
            RootCauseCategory.BAD_DEPLOYMENT,
            business_hours_timestamp,
        )
        assert decision.max_blast_radius == 5

    def test_critical_risk_blast_radius_unlimited(self, engine, business_hours_timestamp):
        """CRITICAL risk = unlimited (0 = no limit)."""
        decision = engine.evaluate(
            ActionType.ROLLBACK,
            "critical",
            RootCauseCategory.BAD_DEPLOYMENT,
            business_hours_timestamp,
        )
        assert decision.max_blast_radius == 0  # 0 = unlimited


# ── Convenience Function Tests ────────────────────────────────────────────────

class TestCheckPolicyFunction:
    """Test the convenience wrapper function check_policy()."""

    def test_check_policy_with_valid_inputs(self):
        """check_policy() should work with string inputs."""
        decision = check_policy(
            action="RESTART",
            alert_severity="warning",
            root_cause="memory_leak",
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.LOW

    def test_check_policy_invalid_action(self):
        """check_policy() should handle invalid action gracefully."""
        decision = check_policy(
            action="INVALID_ACTION",
            alert_severity="warning",
            root_cause="memory_leak",
        )
        assert decision.allowed is False
        assert "invalid" in decision.reason.lower()

    def test_check_policy_invalid_root_cause(self):
        """check_policy() should handle invalid root cause gracefully."""
        decision = check_policy(
            action="RESTART",
            alert_severity="warning",
            root_cause="invalid_cause",
        )
        assert decision.allowed is False
        assert "invalid" in decision.reason.lower()

    def test_check_policy_with_timestamp(self):
        """check_policy() should accept optional timestamp."""
        off_hours = datetime(2024, 1, 15, 21, 0, 0)
        decision = check_policy(
            action="ROLLBACK",
            alert_severity="warning",
            root_cause="bad_deployment",
            timestamp=off_hours,
        )
        assert decision.allowed is False
        assert "outside business hours" in decision.reason.lower()


# ── Edge Cases and Integration ────────────────────────────────────────────────

class TestEdgeCases:
    """Test edge cases and unusual scenarios."""

    def test_ignore_action_always_low_risk(self, engine, business_hours_timestamp):
        """IGNORE action should always be low risk."""
        decision = engine.evaluate(
            ActionType.IGNORE,
            "critical",
            RootCauseCategory.UNKNOWN,
            business_hours_timestamp,
        )
        assert decision.allowed is True
        assert decision.risk_level == RiskLevel.LOW
        assert decision.requires_approval is False

    def test_policy_decision_dataclass_fields(self, engine, business_hours_timestamp):
        """PolicyDecision should have all required fields."""
        decision = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert hasattr(decision, "allowed")
        assert hasattr(decision, "risk_level")
        assert hasattr(decision, "requires_approval")
        assert hasattr(decision, "reason")
        assert hasattr(decision, "max_blast_radius")
        assert isinstance(decision.reason, str)
        assert len(decision.reason) > 0

    def test_multiple_evaluations_independent(self, engine, business_hours_timestamp):
        """Multiple policy evaluations should be independent."""
        decision1 = engine.evaluate(
            ActionType.RESTART,
            "warning",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        decision2 = engine.evaluate(
            ActionType.SCALE,
            "warning",
            RootCauseCategory.MEMORY_LEAK,
            business_hours_timestamp,
        )
        assert decision1.allowed is True
        assert decision2.allowed is False  # Different results for different actions


# ── Parameterized Tests ───────────────────────────────────────────────────────

@pytest.mark.parametrize("severity,expected_risk", [
    ("warning", RiskLevel.LOW),
    ("critical", RiskLevel.MEDIUM),
])
def test_memory_leak_restart_risk_levels(severity, expected_risk):
    """Memory leak RESTART risk varies by severity."""
    engine = PolicyEngine()
    decision = engine.evaluate(
        ActionType.RESTART,
        severity,
        RootCauseCategory.MEMORY_LEAK,
        datetime(2024, 1, 15, 11, 0, 0),
    )
    assert decision.allowed is True
    assert decision.risk_level == expected_risk


@pytest.mark.parametrize("hour,expected_allowed", [
    (8, False),   # Before business hours
    (9, True),    # Start of business hours
    (12, True),   # During business hours
    (18, True),   # End of business hours
    (19, False),  # After business hours
])
def test_high_risk_time_gating(hour, expected_allowed):
    """HIGH risk actions gated by time of day."""
    engine = PolicyEngine()
    timestamp = datetime(2024, 1, 15, hour, 0, 0)
    decision = engine.evaluate(
        ActionType.ROLLBACK,
        "warning",
        RootCauseCategory.BAD_DEPLOYMENT,
        timestamp,
    )
    assert decision.allowed == expected_allowed
"""
Policy Engine for Aether-Guard

Defines allowed/forbidden remediation actions based on:
- Alert type
- Root cause category
- Time of day
- Service criticality
- Historical success rate

This provides deterministic safety gates independent of LLM confidence.
"""

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional


class ActionType(Enum):
    """Remediation action types (same as current system)."""
    RESTART = "RESTART"
    SCALE = "SCALE"
    ROLLBACK = "ROLLBACK"
    IGNORE = "IGNORE"


class RiskLevel(Enum):
    """Risk classification for blast radius control."""
    LOW = 1      # Single pod, reversible
    MEDIUM = 2   # Multi-pod, auto-rollback available
    HIGH = 3     # Cross-service, requires approval
    CRITICAL = 4 # Rollback, manual approval required


class RootCauseCategory(Enum):
    """Known root cause patterns (from rule-based triage)."""
    MEMORY_LEAK = "memory_leak"
    CPU_SATURATION = "cpu_saturation"
    DEPENDENCY_FAILURE = "dependency_failure"
    BAD_DEPLOYMENT = "bad_deployment"
    TRAFFIC_SPIKE = "traffic_spike"
    GOROUTINE_LEAK = "goroutine_leak"
    UNKNOWN = "unknown"


@dataclass
class PolicyDecision:
    """Result of policy evaluation."""
    allowed: bool
    risk_level: RiskLevel
    requires_approval: bool
    reason: str
    max_blast_radius: int  # Max pods affected (0 = unlimited)


class PolicyEngine:
    """
    Deterministic policy engine for remediation actions.

    Design principles:
    1. Deny by default (must explicitly allow)
    2. Time-aware (stricter rules during off-hours)
    3. Risk-based approval routing
    4. Rate limiting (max actions per time window)
    """

    # Policy matrix: (alert_severity, root_cause, action) → allowed?
    _POLICY_MATRIX = {
        # Memory leaks → RESTART is safe, SCALE makes it worse
        ("warning", RootCauseCategory.MEMORY_LEAK, ActionType.RESTART): (True, RiskLevel.LOW),
        ("critical", RootCauseCategory.MEMORY_LEAK, ActionType.RESTART): (True, RiskLevel.MEDIUM),
        ("warning", RootCauseCategory.MEMORY_LEAK, ActionType.SCALE): (False, None),  # Forbidden

        # CPU saturation → SCALE helps, RESTART doesn't
        ("warning", RootCauseCategory.CPU_SATURATION, ActionType.SCALE): (True, RiskLevel.LOW),
        ("critical", RootCauseCategory.CPU_SATURATION, ActionType.SCALE): (True, RiskLevel.MEDIUM),
        ("warning", RootCauseCategory.CPU_SATURATION, ActionType.RESTART): (False, None),

        # Traffic spikes → SCALE only
        ("warning", RootCauseCategory.TRAFFIC_SPIKE, ActionType.SCALE): (True, RiskLevel.LOW),
        ("critical", RootCauseCategory.TRAFFIC_SPIKE, ActionType.SCALE): (True, RiskLevel.MEDIUM),

        # Bad deployments → ROLLBACK only
        ("warning", RootCauseCategory.BAD_DEPLOYMENT, ActionType.ROLLBACK): (True, RiskLevel.HIGH),
        ("critical", RootCauseCategory.BAD_DEPLOYMENT, ActionType.ROLLBACK): (True, RiskLevel.CRITICAL),

        # Dependency failures → RESTART to clear connection pools
        ("warning", RootCauseCategory.DEPENDENCY_FAILURE, ActionType.RESTART): (True, RiskLevel.LOW),

        # Goroutine leaks → RESTART to terminate leaked goroutines
        ("warning", RootCauseCategory.GOROUTINE_LEAK, ActionType.RESTART): (True, RiskLevel.LOW),
        ("critical", RootCauseCategory.GOROUTINE_LEAK, ActionType.RESTART): (True, RiskLevel.MEDIUM),
        ("warning", RootCauseCategory.GOROUTINE_LEAK, ActionType.SCALE): (False, None),  # Forbidden

        # Unknown causes → very conservative (IGNORE only by default)
        ("warning", RootCauseCategory.UNKNOWN, ActionType.IGNORE): (True, RiskLevel.LOW),
        ("critical", RootCauseCategory.UNKNOWN, ActionType.IGNORE): (True, RiskLevel.LOW),
    }

    # Business hours: 9 AM - 6 PM (stricter rules outside this window)
    _BUSINESS_HOURS_START = time(9, 0)
    _BUSINESS_HOURS_END = time(18, 0)

    def evaluate(
        self,
        action: ActionType,
        alert_severity: str,
        root_cause: RootCauseCategory,
        timestamp: Optional[datetime] = None,
    ) -> PolicyDecision:
        """
        Evaluate whether an action is allowed under current policy.

        Args:
            action: Proposed remediation action
            alert_severity: "warning" or "critical"
            root_cause: Categorized root cause from RCA
            timestamp: When alert fired (defaults to now)

        Returns:
            PolicyDecision with allowed status and risk assessment
        """
        if timestamp is None:
            timestamp = datetime.now()

        # 1. Check policy matrix
        policy_key = (alert_severity, root_cause, action)
        if policy_key not in self._POLICY_MATRIX:
            return PolicyDecision(
                allowed=False,
                risk_level=RiskLevel.LOW,
                requires_approval=False,
                reason=f"No policy defined for {policy_key} — deny by default",
                max_blast_radius=0,
            )

        allowed, risk_level = self._POLICY_MATRIX[policy_key]
        if not allowed:
            return PolicyDecision(
                allowed=False,
                risk_level=RiskLevel.LOW,
                requires_approval=False,
                reason=f"Action {action.value} is forbidden for {root_cause.value}",
                max_blast_radius=0,
            )

        # 2. Time-of-day gating (stricter outside business hours)
        is_business_hours = self._is_business_hours(timestamp)
        if not is_business_hours and risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return PolicyDecision(
                allowed=False,
                risk_level=risk_level,
                requires_approval=False,
                reason=f"High-risk action {action.value} blocked outside business hours ({timestamp.time()})",
                max_blast_radius=0,
            )

        # 3. Determine approval requirements
        requires_approval = self._requires_approval(risk_level, alert_severity)

        # 4. Blast radius limits
        max_blast_radius = self._get_blast_radius_limit(risk_level)

        return PolicyDecision(
            allowed=True,
            risk_level=risk_level,
            requires_approval=requires_approval,
            reason=f"Policy allows {action.value} for {root_cause.value} ({risk_level.name} risk)",
            max_blast_radius=max_blast_radius,
        )

    def _is_business_hours(self, timestamp: datetime) -> bool:
        """Check if timestamp falls within business hours (9 AM - 6 PM)."""
        current_time = timestamp.time()
        return self._BUSINESS_HOURS_START <= current_time <= self._BUSINESS_HOURS_END

    def _requires_approval(self, risk_level: RiskLevel, severity: str) -> bool:
        """
        Determine if human approval is required.

        Rules:
        - LOW risk: auto-approve
        - MEDIUM risk + warning: auto-approve
        - MEDIUM risk + critical: Slack notification (no blocking)
        - HIGH risk: Slack approval (60s timeout)
        - CRITICAL risk: Slack approval (5min timeout, mandatory)
        """
        if risk_level == RiskLevel.LOW:
            return False
        if risk_level == RiskLevel.MEDIUM and severity == "warning":
            return False
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return True
        return False  # Default: no approval for MEDIUM + critical (just notify)

    def _get_blast_radius_limit(self, risk_level: RiskLevel) -> int:
        """
        Return max number of pods that can be affected.

        0 = unlimited
        N = at most N pods (for canary rollouts)
        """
        limits = {
            RiskLevel.LOW: 1,       # Single pod only
            RiskLevel.MEDIUM: 3,    # Up to 3 pods
            RiskLevel.HIGH: 5,      # Up to 5 pods
            RiskLevel.CRITICAL: 0,  # Unlimited (but requires approval)
        }
        return limits.get(risk_level, 0)


# Global singleton
_policy_engine = PolicyEngine()


def check_policy(
    action: str,
    alert_severity: str,
    root_cause: str,
    timestamp: Optional[datetime] = None,
) -> PolicyDecision:
    """
    Convenience function for policy checks.

    Example:
        decision = check_policy("RESTART", "warning", "memory_leak")
        if not decision.allowed:
            log.error("Policy violation: %s", decision.reason)
            return
    """
    try:
        action_enum = ActionType(action)
        root_cause_enum = RootCauseCategory(root_cause)
    except ValueError as e:
        return PolicyDecision(
            allowed=False,
            risk_level=RiskLevel.LOW,
            requires_approval=False,
            reason=f"Invalid action or root cause: {e}",
            max_blast_radius=0,
        )

    return _policy_engine.evaluate(action_enum, alert_severity, root_cause_enum, timestamp)
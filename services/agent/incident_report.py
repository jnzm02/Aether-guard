"""
Aether-Guard — Structured Incident Report (Priority 1)

Design:
  - Single queryable document per incident (not scattered log lines)
  - Stored in both Redis (48h TTL, fast recent access) and Postgres (long-term analytics)
  - Renderable as markdown for human incident retros
  - Indexed by matched_pattern for trust metrics (Priority 2 prep)

Public API:
  build_report(analysis: dict) -> IncidentReport
  render_summary_table(report: IncidentReport) -> str
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any


@dataclass
class IncidentReport:
    """
    Structured incident report with all critical fields for querying and analysis.

    Outcome taxonomy (5 categories, for trust metrics):
      - auto_resolved: Verification confirmed metrics improved
      - rolled_back: Auto-rollback triggered after verification failure
      - escalated_low_confidence: Agent wasn't confident, escalated immediately without action
      - escalated_verification_failed: Agent acted but couldn't confirm fix worked
      - ignored: Policy/cooldown blocked action, or action was IGNORE
    """

    # Core identification
    incident_id: str          # Same as alert_id
    detected_at: str          # ISO timestamp from alert.starts_at
    resolved_at: str          # ISO timestamp when processing complete
    duration_ms: int          # detected_at → resolved_at in milliseconds

    # RCA
    trigger: str              # alertname (e.g., "SLOErrorBudgetBurnCritical")
    matched_pattern: str      # "rule:oom_kill" | "llm_fallback" | "fallback_error"
    confidence: float         # 0.0-1.0
    root_cause: str           # Human-readable root cause description
    reasoning: str            # Agent's reasoning / evidence

    # Action
    action_taken: str         # RESTART | SCALE | ROLLBACK | IGNORE
    remediation_outcome: str  # "success" | "failed" | "skipped" | "cooldown_blocked"

    # Verification
    verification_performed: bool
    verification_result: str | None  # "improved" | "no_improvement" | "failed" | None
    auto_rollback_triggered: bool

    # Final outcome (5 categories for trust metrics)
    outcome: str  # auto_resolved | rolled_back | escalated_low_confidence | escalated_verification_failed | ignored

    # Metadata
    rca_method: str           # "rule-based" | "llm-assisted" | "fallback-error"
    policy_blocked: bool
    approval_required: bool
    severity: str             # "critical" | "warning" | "info"

    # Full analysis (for deep inspection)
    full_analysis: dict[str, Any]

    # Override tracking (Priority 2 — trust metrics)
    # Used to track when a human operator overrides or escalates the agent's decision
    override_status: str = "none"  # "none" | "manual_reversal" | "manual_escalation"
    override_reason: str | None = None  # Human-provided explanation
    override_at: str | None = None      # ISO timestamp when override occurred
    override_by: str | None = None      # Source:identifier (e.g. "slack:U12345", "api:username")
                                        # This is an unstructured string, NOT a foreign key.
                                        # Convention: "source:identifier" to track origin without
                                        # coupling to any specific user table or auth system.

    def as_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization (Redis, Postgres)."""
        return asdict(self)


def build_report(analysis: dict[str, Any]) -> IncidentReport:
    """
    Build a structured IncidentReport from the raw analysis dict produced by agent.py.

    Args:
        analysis: The dict returned by agent.py's analyze_alert(), enriched with
                  remediation and verification results.

    Returns:
        IncidentReport with all fields populated and outcome derived.
    """
    # Extract timestamps
    starts_at = analysis.get("alert_labels", {}).get("startsAt") or analysis.get("starts_at", "")
    analyzed_at = analysis.get("analyzed_at", "")

    # Calculate duration
    duration_ms = _calculate_duration_ms(starts_at, analyzed_at)

    # Build matched_pattern (indexed for Priority 2 trust metrics)
    rca_method = analysis.get("rca_method", "unknown")
    if rca_method == "rule-based":
        rule_name = analysis.get("rule_name", "unknown_rule")
        matched_pattern = f"rule:{rule_name}"
    elif rca_method == "llm-assisted":
        matched_pattern = "llm_fallback"
    elif rca_method == "fallback-error":
        matched_pattern = "fallback_error"
    else:
        matched_pattern = f"unknown:{rca_method}"

    # Extract remediation info
    remediation = analysis.get("remediation", {})
    action_taken = analysis.get("action", "IGNORE")
    remediation_outcome = remediation.get("outcome", "unknown")

    # Extract verification info
    verification = analysis.get("verification", {})
    verification_performed = "verification" in analysis
    verification_result = None
    if verification_performed:
        if verification.get("success"):
            verification_result = "improved"
        elif verification.get("improved") is False:
            verification_result = "no_improvement"
        else:
            verification_result = "failed"

    auto_rollback_triggered = "auto_rollback" in analysis

    # Derive outcome (5 categories)
    outcome = _derive_outcome(
        action=action_taken,
        confidence=analysis.get("confidence", 0.0),
        remediation_outcome=remediation_outcome,
        verification_result=verification_result,
        auto_rollback_triggered=auto_rollback_triggered,
        policy_blocked=analysis.get("policy_blocked", False),
    )

    # Extract metadata
    severity = analysis.get("alert_labels", {}).get("severity", "unknown")
    policy_blocked = analysis.get("policy_blocked", False)
    approval_required = analysis.get("approval_required", False)

    return IncidentReport(
        incident_id=analysis.get("alert_id", "unknown"),
        detected_at=starts_at or analyzed_at,  # Fallback to analyzed_at if starts_at missing
        resolved_at=analyzed_at,
        duration_ms=duration_ms,
        trigger=analysis.get("alertname", "unknown"),
        matched_pattern=matched_pattern,
        confidence=float(analysis.get("confidence", 0.0)),
        root_cause=analysis.get("root_cause", "unknown"),
        reasoning=analysis.get("reasoning", ""),
        action_taken=action_taken,
        remediation_outcome=remediation_outcome,
        verification_performed=verification_performed,
        verification_result=verification_result,
        auto_rollback_triggered=auto_rollback_triggered,
        outcome=outcome,
        rca_method=rca_method,
        policy_blocked=policy_blocked,
        approval_required=approval_required,
        severity=severity,
        full_analysis=analysis,
    )


def _derive_outcome(
    action: str,
    confidence: float,
    remediation_outcome: str,
    verification_result: str | None,
    auto_rollback_triggered: bool,
    policy_blocked: bool,
) -> str:
    """
    Derive the final outcome category from remediation and verification results.

    5 categories (for Priority 2 trust metrics):
      1. auto_resolved: Verification confirmed metrics improved
      2. rolled_back: Auto-rollback triggered after verification failure
      3. escalated_low_confidence: Agent wasn't confident, escalated immediately
      4. escalated_verification_failed: Agent acted but couldn't confirm fix worked
      5. ignored: Policy/cooldown blocked action, or action was IGNORE (not due to low confidence)

    Order matters: Check low-confidence BEFORE checking general IGNORE case.
    """
    # Category 2: Rolled back (auto-rollback was triggered)
    if auto_rollback_triggered:
        return "rolled_back"

    # Category 1: Auto-resolved (verification confirmed improvement)
    if verification_result == "improved":
        return "auto_resolved"

    # Category 4: Escalated after verification failure
    # Agent executed action but verification failed or showed no improvement
    if verification_result in ("no_improvement", "failed"):
        return "escalated_verification_failed"

    # Category 3: Escalated due to low confidence (MUST check before general IGNORE)
    # Agent wasn't confident enough to act, downgraded action to IGNORE
    if confidence < 0.60 and action == "IGNORE" and remediation_outcome == "skipped":
        return "escalated_low_confidence"

    # Category 5: Ignored (policy/cooldown blocked, or action was IGNORE for other reasons)
    if action == "IGNORE" or remediation_outcome in ("skipped", "cooldown_blocked") or policy_blocked:
        return "ignored"

    # Default: If verification wasn't performed but action succeeded, treat as auto_resolved
    # (This handles the case where V2 verification is disabled)
    if remediation_outcome == "success":
        return "auto_resolved"

    # Fallback: If we can't determine outcome, escalate for human review
    return "escalated_verification_failed"


def _calculate_duration_ms(starts_at: str, analyzed_at: str) -> int:
    """Calculate duration in milliseconds between detected_at and resolved_at."""
    if not starts_at or not analyzed_at:
        return 0

    try:
        t0 = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(analyzed_at.replace("Z", "+00:00"))
        delta_ms = int(abs((t1 - t0).total_seconds() * 1000))
        return delta_ms
    except (ValueError, AttributeError):
        return 0


def render_summary_table(report: IncidentReport) -> str:
    """
    Render a human-readable markdown table summarizing the incident.

    This is designed to be inserted at the top of the post-mortem document.
    """
    # Format duration
    if report.duration_ms < 1000:
        duration_str = f"{report.duration_ms}ms"
    elif report.duration_ms < 60_000:
        duration_str = f"{report.duration_ms / 1000:.1f}s"
    else:
        duration_str = f"{report.duration_ms / 60_000:.1f}m"

    # Format confidence
    confidence_pct = f"{report.confidence * 100:.0f}%"

    # Format verification result
    if report.verification_performed:
        if report.verification_result == "improved":
            verification_str = "✅ Metrics improved"
        elif report.verification_result == "no_improvement":
            verification_str = "⚠️ No improvement detected"
        elif report.verification_result == "failed":
            verification_str = "❌ Verification failed"
        else:
            verification_str = "❓ Unknown"
    else:
        verification_str = "— (not performed)"

    # Format outcome with emoji
    outcome_emoji = {
        "auto_resolved": "✅",
        "rolled_back": "🔄",
        "escalated_low_confidence": "⚠️",
        "escalated_verification_failed": "❌",
        "ignored": "⏭️",
    }
    outcome_display = f"{outcome_emoji.get(report.outcome, '❓')} {report.outcome.replace('_', ' ').title()}"

    return f"""\
## Incident Summary

| Field | Value |
|-------|-------|
| **Incident ID** | `{report.incident_id[:12]}...` |
| **Detected** | `{_format_timestamp(report.detected_at)}` |
| **Duration** | {duration_str} |
| **Trigger** | `{report.trigger}` |
| **Pattern Matched** | `{report.matched_pattern}` |
| **Confidence** | {confidence_pct} |
| **Action Taken** | **{report.action_taken}** |
| **Outcome** | {outcome_display} |
| **Verification** | {verification_str} |
"""


def _format_timestamp(iso: str) -> str:
    """Format ISO timestamp as human-readable UTC string."""
    if not iso:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, AttributeError):
        return iso
"""
Aether-Guard — Incident Report Tests (Priority 1)

Edge cases to validate:
  1. Auto-rollback scenario (outcome = "rolled_back")
  2. Low-confidence LLM fallback (outcome = "escalated_low_confidence")
  3. Verification timeout/failure (outcome = "escalated_verification_failed")
  4. Policy-blocked action (outcome = "ignored")
  5. Cooldown-blocked remediation (outcome = "ignored")
  6. Duration calculation accuracy
  7. Markdown rendering correctness
"""


from incident_report import build_report, render_summary_table, IncidentReport


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Auto-rollback after verification failure
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_rollback():
    """
    Scenario: Agent executed RESTART, verification showed no improvement,
    auto-rollback was triggered.

    Expected outcome: "rolled_back"
    """
    analysis = {
        "alert_id": "test-rollback-001",
        "alertname": "SLOErrorBudgetBurnCritical",
        "alert_labels": {
            "severity": "critical",
            "startsAt": "2026-07-07T10:00:00Z",
        },
        "analyzed_at": "2026-07-07T10:02:30Z",
        "rca_method": "rule-based",
        "rule_name": "oom_kill",
        "root_cause": "Out of Memory (OOM)",
        "confidence": 0.95,
        "action": "RESTART",
        "reasoning": "OOM kill detected in kernel logs",
        "remediation": {
            "action": "RESTART",
            "outcome": "success",
            "executed": True,
        },
        "verification": {
            "success": False,
            "improved": False,
            "should_rollback": True,
            "reason": "Error rate did not decrease after restart",
        },
        "auto_rollback": {
            "action": "ROLLBACK",
            "outcome": "success",
        },
        "policy_blocked": False,
    }

    report = build_report(analysis)

    assert report.incident_id == "test-rollback-001"
    assert report.outcome == "rolled_back"
    assert report.action_taken == "RESTART"
    assert report.verification_performed is True
    assert report.verification_result == "no_improvement"
    assert report.auto_rollback_triggered is True
    assert report.matched_pattern == "rule:oom_kill"
    assert report.confidence == 0.95


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Low-confidence LLM escalation
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_llm_fallback_low_confidence():
    """
    Scenario: LLM analysis returned confidence 0.45, agent overrode action to IGNORE,
    no remediation was attempted.

    Expected outcome: "escalated_low_confidence"

    This distinguishes "agent wasn't confident enough to act" from
    "agent acted but verification failed" (escalated_verification_failed).
    """
    analysis = {
        "alert_id": "test-lowconf-002",
        "alertname": "HighLatencyP99",
        "alert_labels": {
            "severity": "warning",
            "startsAt": "2026-07-07T11:00:00Z",
        },
        "analyzed_at": "2026-07-07T11:01:15Z",
        "rca_method": "llm-assisted",
        "root_cause": "Possible database connection pool exhaustion",
        "confidence": 0.45,
        "action": "IGNORE",  # Overridden due to low confidence
        "reasoning": "Signals are ambiguous, no clear pattern detected. [Agent override: confidence 0.45 < threshold 0.60 — action downgraded to IGNORE]",
        "remediation": {
            "action": "IGNORE",
            "outcome": "skipped",
            "executed": False,
        },
        "policy_blocked": False,
    }

    report = build_report(analysis)

    assert report.incident_id == "test-lowconf-002"
    assert report.outcome == "escalated_low_confidence"
    assert report.action_taken == "IGNORE"
    assert report.confidence == 0.45
    assert report.verification_performed is False
    assert report.matched_pattern == "llm_fallback"
    assert report.remediation_outcome == "skipped"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Verification failure (agent acted, but couldn't confirm success)
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_verification_failed():
    """
    Scenario: Agent executed SCALE with high confidence, but verification
    showed no improvement. Auto-rollback was NOT triggered (perhaps rollback
    is not applicable for SCALE).

    Expected outcome: "escalated_verification_failed"

    This is distinct from "escalated_low_confidence" — agent was confident
    and acted, but the fix didn't work.
    """
    analysis = {
        "alert_id": "test-verifail-003",
        "alertname": "HighCPUSaturation",
        "alert_labels": {
            "severity": "critical",
            "startsAt": "2026-07-07T12:00:00Z",
        },
        "analyzed_at": "2026-07-07T12:04:00Z",
        "rca_method": "rule-based",
        "rule_name": "cpu_saturation_with_traffic",
        "root_cause": "CPU Saturation",
        "confidence": 0.85,
        "action": "SCALE",
        "reasoning": "High CPU with traffic spike, scaling out",
        "remediation": {
            "action": "SCALE",
            "outcome": "success",
            "executed": True,
        },
        "verification": {
            "success": False,
            "improved": False,
            "should_rollback": False,  # SCALE doesn't auto-rollback
            "reason": "CPU still saturated after scaling",
        },
        "policy_blocked": False,
    }

    report = build_report(analysis)

    assert report.incident_id == "test-verifail-003"
    assert report.outcome == "escalated_verification_failed"
    assert report.action_taken == "SCALE"
    assert report.verification_performed is True
    assert report.verification_result == "no_improvement"
    assert report.auto_rollback_triggered is False
    assert report.confidence == 0.85


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Policy-blocked action
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_policy_blocked():
    """
    Scenario: Agent wanted to execute ROLLBACK, but policy gate blocked it
    (e.g., outside business hours for CRITICAL risk action).

    Expected outcome: "ignored"
    """
    analysis = {
        "alert_id": "test-policy-004",
        "alertname": "BadDeploymentDetected",
        "alert_labels": {
            "severity": "critical",
            "startsAt": "2026-07-07T03:00:00Z",  # 3 AM, outside 9-6 window
        },
        "analyzed_at": "2026-07-07T03:01:00Z",
        "rca_method": "rule-based",
        "rule_name": "bad_deployment",
        "root_cause": "Bad Deployment",
        "confidence": 0.78,
        "action": "IGNORE",  # Overridden by policy
        "reasoning": "Error spike after deployment. [Policy blocked: ROLLBACK requires approval (CRITICAL risk)]",
        "remediation": {
            "action": "IGNORE",
            "outcome": "skipped",
            "executed": False,
        },
        "policy_blocked": True,
        "policy_decision": {
            "allowed": False,
            "reason": "ROLLBACK requires approval (CRITICAL risk), not allowed outside business hours",
        },
    }

    report = build_report(analysis)

    assert report.incident_id == "test-policy-004"
    assert report.outcome == "ignored"
    assert report.action_taken == "IGNORE"
    assert report.policy_blocked is True
    assert report.remediation_outcome == "skipped"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Cooldown-blocked remediation
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_cooldown_blocked():
    """
    Scenario: Agent wanted to RESTART, but Redis cooldown was active
    (container was restarted <5 minutes ago).

    Expected outcome: "ignored"
    """
    analysis = {
        "alert_id": "test-cooldown-005",
        "alertname": "MemoryLeakDetected",
        "alert_labels": {
            "severity": "warning",
            "startsAt": "2026-07-07T14:00:00Z",
        },
        "analyzed_at": "2026-07-07T14:00:30Z",
        "rca_method": "rule-based",
        "rule_name": "memory_leak",
        "root_cause": "Memory Leak",
        "confidence": 0.88,
        "action": "RESTART",
        "reasoning": "Memory leak pattern detected",
        "remediation": {
            "action": "RESTART",
            "outcome": "cooldown_blocked",
            "executed": False,
            "reason": "Cooldown active: target-service was actioned 3 minutes ago",
        },
        "policy_blocked": False,
    }

    report = build_report(analysis)

    assert report.incident_id == "test-cooldown-005"
    assert report.outcome == "ignored"
    assert report.action_taken == "RESTART"
    assert report.remediation_outcome == "cooldown_blocked"
    assert report.verification_performed is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Duration calculation accuracy
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_duration_calculation():
    """
    Scenario: Validate that duration_ms is calculated correctly from
    detected_at (startsAt) to resolved_at (analyzed_at).
    """
    # 2 minutes 34 seconds = 154 seconds = 154000 ms
    detected_at = "2026-07-07T15:00:00Z"
    resolved_at = "2026-07-07T15:02:34Z"

    analysis = {
        "alert_id": "test-duration-006",
        "alertname": "TestAlert",
        "alert_labels": {
            "severity": "info",
            "startsAt": detected_at,
        },
        "analyzed_at": resolved_at,
        "rca_method": "rule-based",
        "rule_name": "test_rule",
        "root_cause": "Test",
        "confidence": 0.90,
        "action": "RESTART",
        "reasoning": "Test",
        "remediation": {
            "action": "RESTART",
            "outcome": "success",
            "executed": True,
        },
        "verification": {
            "success": True,
            "improved": True,
            "should_rollback": False,
            "reason": "Metrics improved",
        },
        "policy_blocked": False,
    }

    report = build_report(analysis)

    # Expected: 154 seconds = 154000 ms
    assert report.duration_ms == 154000
    assert report.detected_at == detected_at
    assert report.resolved_at == resolved_at


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Successful auto-resolution (baseline happy path)
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_auto_resolved():
    """
    Scenario: Agent executed RESTART, verification confirmed metrics improved.

    Expected outcome: "auto_resolved"

    This is the happy path — everything worked as intended.
    """
    analysis = {
        "alert_id": "test-success-007",
        "alertname": "SLOErrorBudgetBurnCritical",
        "alert_labels": {
            "severity": "critical",
            "startsAt": "2026-07-07T16:00:00Z",
        },
        "analyzed_at": "2026-07-07T16:03:00Z",
        "rca_method": "rule-based",
        "rule_name": "oom_kill",
        "root_cause": "Out of Memory (OOM)",
        "confidence": 0.95,
        "action": "RESTART",
        "reasoning": "OOM kill detected in kernel logs",
        "remediation": {
            "action": "RESTART",
            "outcome": "success",
            "executed": True,
        },
        "verification": {
            "success": True,
            "improved": True,
            "should_rollback": False,
            "reason": "Error rate dropped 87%, latency improved 62%",
        },
        "policy_blocked": False,
    }

    report = build_report(analysis)

    assert report.incident_id == "test-success-007"
    assert report.outcome == "auto_resolved"
    assert report.action_taken == "RESTART"
    assert report.verification_performed is True
    assert report.verification_result == "improved"
    assert report.auto_rollback_triggered is False
    assert report.confidence == 0.95


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Markdown rendering correctness
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_render_markdown():
    """
    Scenario: Validate that render_summary_table produces valid markdown
    with all expected fields.
    """
    report = IncidentReport(
        incident_id="test-markdown-008-abc123def456",
        trace_id="abcd1234567890abcdef1234567890ab",
        detected_at="2026-07-07T17:00:00+00:00",
        resolved_at="2026-07-07T17:02:45+00:00",
        duration_ms=165000,  # 2m 45s
        trigger="SLOErrorBudgetBurnCritical",
        matched_pattern="rule:oom_kill",
        confidence=0.95,
        root_cause="Out of Memory (OOM)",
        reasoning="OOM kill detected in kernel logs",
        action_taken="RESTART",
        remediation_outcome="success",
        verification_performed=True,
        verification_result="improved",
        auto_rollback_triggered=False,
        outcome="auto_resolved",
        rca_method="rule-based",
        policy_blocked=False,
        approval_required=False,
        severity="critical",
        full_analysis={},
    )

    markdown = render_summary_table(report)

    # Validate structure
    assert "## Incident Summary" in markdown
    assert "| Field | Value |" in markdown
    assert "|-------|-------|" in markdown

    # Validate key fields
    assert "test-markdow" in markdown  # Incident ID (truncated to 12 chars: "test-markdown-008-abc123def456"[:12] = "test-markdow")
    assert "2026-07-07 17:00:00 UTC" in markdown  # Detected timestamp
    assert "2.8m" in markdown or "165000ms" in markdown  # Duration (either format acceptable)
    assert "SLOErrorBudgetBurnCritical" in markdown  # Trigger
    assert "rule:oom_kill" in markdown  # Pattern
    assert "95%" in markdown  # Confidence
    assert "RESTART" in markdown  # Action
    assert "Auto Resolved" in markdown or "auto_resolved" in markdown  # Outcome
    assert "✅" in markdown  # Success emoji (either for outcome or verification)


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Edge case - missing timestamps (fallback behavior)
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_missing_timestamps():
    """
    Scenario: Alert has missing startsAt, should fallback to analyzed_at.
    Duration should be 0 if timestamps are invalid.
    """
    analysis = {
        "alert_id": "test-notimestamp-009",
        "alertname": "TestAlert",
        "alert_labels": {
            "severity": "info",
            # startsAt missing
        },
        "analyzed_at": "2026-07-07T18:00:00Z",
        "rca_method": "llm-assisted",
        "root_cause": "Unknown",
        "confidence": 0.70,
        "action": "IGNORE",
        "reasoning": "Test",
        "remediation": {
            "action": "IGNORE",
            "outcome": "skipped",
            "executed": False,
        },
        "policy_blocked": False,
    }

    report = build_report(analysis)

    assert report.incident_id == "test-notimestamp-009"
    assert report.detected_at == "2026-07-07T18:00:00Z"  # Fallback to analyzed_at
    assert report.resolved_at == "2026-07-07T18:00:00Z"
    assert report.duration_ms == 0  # Same timestamp = 0 duration


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Verification timeout (treated as failed)
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_verification_timeout():
    """
    Scenario: Verification was attempted but failed due to timeout or error.
    The verification.success = False but improved is not set (None).

    Expected outcome: "escalated_verification_failed"
    """
    analysis = {
        "alert_id": "test-timeout-010",
        "alertname": "HighLatencyP99",
        "alert_labels": {
            "severity": "warning",
            "startsAt": "2026-07-07T19:00:00Z",
        },
        "analyzed_at": "2026-07-07T19:03:00Z",
        "rca_method": "rule-based",
        "rule_name": "dependency_failure",
        "root_cause": "Dependency Failure",
        "confidence": 0.75,
        "action": "RESTART",
        "reasoning": "Connection errors to downstream service",
        "remediation": {
            "action": "RESTART",
            "outcome": "success",
            "executed": True,
        },
        "verification": {
            "success": False,
            "improved": None,  # Timeout, couldn't determine improvement
            "should_rollback": False,
            "reason": "Verification timeout after 120s",
        },
        "policy_blocked": False,
    }

    report = build_report(analysis)

    assert report.incident_id == "test-timeout-010"
    assert report.outcome == "escalated_verification_failed"
    assert report.verification_performed is True
    assert report.verification_result == "failed"
    assert report.auto_rollback_triggered is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: LLM fallback with fallback-error RCA method
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_fallback_error():
    """
    Scenario: All LLM attempts failed, agent produced a fallback record
    with confidence 0.0 and action IGNORE.

    Expected outcome: "escalated_low_confidence"
    """
    analysis = {
        "alert_id": "test-fallback-011",
        "alertname": "UnknownAlert",
        "alert_labels": {
            "severity": "warning",
            "startsAt": "2026-07-07T20:00:00Z",
        },
        "analyzed_at": "2026-07-07T20:00:30Z",
        "rca_method": "fallback-error",
        "root_cause": "Unknown — analysis failed",
        "confidence": 0.0,
        "action": "IGNORE",
        "reasoning": "Defaulting to IGNORE due to analysis failure.",
        "remediation": {
            "action": "IGNORE",
            "outcome": "skipped",
            "executed": False,
        },
        "policy_blocked": False,
    }

    report = build_report(analysis)

    assert report.incident_id == "test-fallback-011"
    assert report.outcome == "escalated_low_confidence"
    assert report.matched_pattern == "fallback_error"
    assert report.confidence == 0.0
    assert report.action_taken == "IGNORE"
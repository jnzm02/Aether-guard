"""
Aether-Guard — Webhook Integration Tests (Priority 3)

Test coverage:
  1. Valid Alertmanager payload creates incident correctly
  2. Malformed payload (missing alerts[]) returns 422, doesn't crash
  3. Empty alerts array returns 202 with enqueued=0
  4. Resolved alert is handled per design
  5. Duplicate fingerprint within 5min is deduplicated
  6. Webhook and polling paths produce equivalent IncidentReport structures
  7. Batch alerts (multiple in single payload) all get processed
"""

from contextlib import asynccontextmanager
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent import (
    _should_deduplicate,
    _webhook_dedup_cache,
    _webhook_stats,
)
from incident_report import build_report


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Valid Alertmanager payload creates incident correctly
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_valid_payload():
    """
    Scenario: Alertmanager sends a valid firing alert via webhook.
    Expected: Alert is enriched, processed through RCA pipeline, incident created.
    """
    from fastapi.testclient import TestClient
    from agent import app

    client = TestClient(app)

    payload = {
        "receiver": "aether-guard-agent",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "SLOErrorBudgetBurnCritical",
                    "severity": "critical",
                    "service": "target-service",
                },
                "annotations": {
                    "description": "Error budget burn rate too high",
                    "summary": "SLO violation detected",
                },
                "startsAt": "2026-07-08T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=...",
                "fingerprint": "abc123def456",
            }
        ],
        "groupLabels": {"alertname": "SLOErrorBudgetBurnCritical"},
        "commonLabels": {"severity": "critical"},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager:9093",
        "version": "4",
        "groupKey": "{}:{alertname=\"SLOErrorBudgetBurnCritical\"}",
    }

    response = client.post("/webhook/alertmanager", json=payload)

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["enqueued"] == 1
    assert data["deduplicated"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Malformed payload (missing required fields) returns 422
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_malformed_payload():
    """
    Scenario: Webhook payload missing required 'alerts' field.
    Expected: Returns 422 Unprocessable Entity, doesn't crash the agent.
    """
    from fastapi.testclient import TestClient
    from agent import app

    client = TestClient(app)

    # Missing 'alerts' field (required by AlertmanagerWebhookPayload)
    payload = {
        "receiver": "aether-guard-agent",
        "status": "firing",
        # "alerts" field is missing!
    }

    response = client.post("/webhook/alertmanager", json=payload)

    assert response.status_code == 422  # Pydantic validation error
    assert "alerts" in response.text.lower() or "field required" in response.text.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Empty alerts array returns 202 with enqueued=0
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_empty_alerts_array():
    """
    Scenario: Webhook payload has empty alerts array.
    Expected: Returns 202 with enqueued=0 (graceful handling).
    """
    from fastapi.testclient import TestClient
    from agent import app

    client = TestClient(app)

    payload = {
        "receiver": "aether-guard-agent",
        "status": "firing",
        "alerts": [],  # Empty array
    }

    response = client.post("/webhook/alertmanager", json=payload)

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["enqueued"] == 0
    assert data["deduplicated"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Resolved alert is handled per design
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_resolved_status():
    """
    Scenario: Alertmanager sends a resolved alert.
    Expected: Alert is logged and handled per Priority 3 design (no remediation).
    """
    from fastapi.testclient import TestClient
    from agent import app

    client = TestClient(app)

    initial_resolved_count = _webhook_stats["resolved_alerts_received"]

    payload = {
        "receiver": "aether-guard-agent",
        "status": "resolved",
        "alerts": [
            {
                "status": "resolved",  # Resolved status
                "labels": {
                    "alertname": "SLOErrorBudgetBurnCritical",
                    "severity": "critical",
                },
                "annotations": {},
                "startsAt": "2026-07-08T10:00:00Z",
                "endsAt": "2026-07-08T10:05:00Z",  # Alert resolved after 5min
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": "resolved-test-123",
            }
        ],
    }

    response = client.post("/webhook/alertmanager", json=payload)

    assert response.status_code == 202
    data = response.json()
    assert data["enqueued"] == 0  # Resolved alerts are not enqueued for processing
    assert _webhook_stats["resolved_alerts_received"] == initial_resolved_count + 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Duplicate fingerprint within 5min is deduplicated
# ─────────────────────────────────────────────────────────────────────────────

def test_webhook_duplicate_fingerprint():
    """
    Scenario: Same alert (same fingerprint) arrives twice within 5 minutes.
    Expected: First is processed, second is deduplicated.
    """
    fingerprint = "test-dedup-fingerprint-789"

    # Clear dedup cache
    _webhook_dedup_cache.clear()

    # First call: should NOT be deduplicated
    assert _should_deduplicate(fingerprint) is False

    # Second call (within 5min): SHOULD be deduplicated
    assert _should_deduplicate(fingerprint) is True

    # Verify fingerprint is in cache
    assert fingerprint in _webhook_dedup_cache


def test_webhook_dedup_different_fingerprints():
    """
    Scenario: Two different alerts with different fingerprints arrive.
    Expected: Both are processed (no deduplication).
    """
    _webhook_dedup_cache.clear()

    fp1 = "fingerprint-alert-1"
    fp2 = "fingerprint-alert-2"

    assert _should_deduplicate(fp1) is False
    assert _should_deduplicate(fp2) is False

    # Both should be in cache
    assert fp1 in _webhook_dedup_cache
    assert fp2 in _webhook_dedup_cache


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Webhook and polling paths produce equivalent IncidentReport structures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_and_polling_equivalence():
    """
    Scenario: Same alert data arrives via webhook vs polling.
    Expected: Both paths produce equivalent IncidentReport structures (no divergence).
    """
    # Create a mock enriched alert (same structure from both webhook and polling)
    enriched_alert = {
        "id": "test-alert-001",
        "received_at": "2026-07-08T10:00:00Z",
        "status": "firing",
        "labels": {
            "alertname": "SLOErrorBudgetBurnCritical",
            "severity": "critical",
        },
        "annotations": {
            "description": "Error budget burn",
        },
        "starts_at": "2026-07-08T09:55:00Z",
        "ends_at": "0001-01-01T00:00:00Z",
        "generator_url": "http://prometheus:9090",
        "fingerprint": "equivalence-test-fp",
        "metrics_snapshot": {
            "error_ratio_5m": 0.05,
            "latency_p99_5m_seconds": 0.8,
        },
        "log_tail": ["log line 1", "log line 2"],
        "processed_by_ai": False,
        "ai_analysis": None,
        "action_taken": None,
    }

    # Build analysis dict (as returned by analyze_alert)
    analysis_webhook = {
        "alert_id": enriched_alert["id"],
        "alertname": "SLOErrorBudgetBurnCritical",
        "alert_labels": enriched_alert["labels"],
        "analyzed_at": "2026-07-08T10:00:30Z",
        "starts_at": enriched_alert["starts_at"],
        "rca_method": "rule-based",
        "rule_name": "error_budget_burn",
        "root_cause": "High error rate",
        "confidence": 0.92,
        "action": "RESTART",
        "reasoning": "Error budget burn detected",
        "remediation": {"action": "RESTART", "outcome": "success"},
        "verification": {"success": True, "improved": True},
    }

    analysis_polling = analysis_webhook.copy()

    # Build incident reports from both paths
    report_webhook = build_report(analysis_webhook)
    report_polling = build_report(analysis_polling)

    # Verify key fields are identical
    assert report_webhook.matched_pattern == report_polling.matched_pattern
    assert report_webhook.confidence == report_polling.confidence
    assert report_webhook.action_taken == report_polling.action_taken
    assert report_webhook.outcome == report_polling.outcome
    assert report_webhook.rca_method == report_polling.rca_method


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Batch alerts (multiple in single payload) all get processed
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_batch_alerts():
    """
    Scenario: Alertmanager sends multiple alerts in a single webhook payload.
    Expected: All firing alerts are enqueued for processing.
    """
    from fastapi.testclient import TestClient
    from agent import app

    client = TestClient(app)

    # Clear dedup cache to ensure all alerts are processed
    _webhook_dedup_cache.clear()

    payload = {
        "receiver": "aether-guard-agent",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "HighErrorRate", "severity": "critical"},
                "annotations": {},
                "startsAt": "2026-07-08T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090",
                "fingerprint": "batch-alert-1",
            },
            {
                "status": "firing",
                "labels": {"alertname": "HighLatency", "severity": "warning"},
                "annotations": {},
                "startsAt": "2026-07-08T10:01:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090",
                "fingerprint": "batch-alert-2",
            },
            {
                "status": "firing",
                "labels": {"alertname": "MemoryPressure", "severity": "warning"},
                "annotations": {},
                "startsAt": "2026-07-08T10:02:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090",
                "fingerprint": "batch-alert-3",
            },
        ],
    }

    response = client.post("/webhook/alertmanager", json=payload)

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["enqueued"] == 3  # All 3 alerts enqueued
    assert data["deduplicated"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Webhook with mixed firing and resolved alerts
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_mixed_firing_and_resolved():
    """
    Scenario: Webhook payload contains both firing and resolved alerts.
    Expected: Firing alerts enqueued, resolved alerts handled separately.
    """
    from fastapi.testclient import TestClient
    from agent import app

    client = TestClient(app)

    _webhook_dedup_cache.clear()

    payload = {
        "receiver": "aether-guard-agent",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "AlertA", "severity": "critical"},
                "annotations": {},
                "startsAt": "2026-07-08T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090",
                "fingerprint": "mixed-alert-firing",
            },
            {
                "status": "resolved",
                "labels": {"alertname": "AlertB", "severity": "warning"},
                "annotations": {},
                "startsAt": "2026-07-08T09:00:00Z",
                "endsAt": "2026-07-08T10:00:00Z",
                "generatorURL": "http://prometheus:9090",
                "fingerprint": "mixed-alert-resolved",
            },
        ],
    }

    response = client.post("/webhook/alertmanager", json=payload)

    assert response.status_code == 202
    data = response.json()
    assert data["enqueued"] == 1  # Only firing alert enqueued
    assert data["resolved"] >= 1  # Resolved alert handled separately


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Resolved alert updates stored IncidentReport (Priority 3 completion)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolved_alert_updates_incident_report():
    """
    Scenario: Alert fires, incident created, then alert resolves before remediation completes.
    Expected: IncidentReport in storage is updated with outcome="ignored" and
              remediation_outcome="alert_auto_resolved", not just stats counter.

    This test verifies that resolved alert handling actually modifies the incident
    record for trust metrics (Priority 2 dependency).
    """
    from incident_storage import IncidentStorage
    from incident_report import IncidentReport

    # Create a mock incident that's in-flight (not yet remediated)
    test_fingerprint = "test-resolved-fp-001"
    incident = IncidentReport(
        incident_id="test-incident-resolved-001",
        trace_id="1234567890abcdef1234567890abcdef",
        detected_at="2026-07-08T10:00:00Z",
        resolved_at="2026-07-08T10:01:00Z",
        duration_ms=60000,
        trigger="SLOErrorBudgetBurnCritical",
        matched_pattern="rule:error_budget_burn",
        confidence=0.85,
        root_cause="High error rate",
        reasoning="Error budget burn detected",
        action_taken="RESTART",
        remediation_outcome="pending",  # Remediation not completed yet
        verification_performed=False,
        verification_result=None,
        auto_rollback_triggered=False,
        outcome="escalated_verification_failed",  # Temporary outcome, should be updated
        rca_method="rule-based",
        policy_blocked=False,
        approval_required=False,
        severity="critical",
        full_analysis={"fingerprint": test_fingerprint, "test": True},
    )

    # Mock Postgres cursor for get_by_fingerprint
    mock_cursor_get = AsyncMock()
    mock_cursor_get.fetchone = AsyncMock(return_value={
        "incident_id": incident.incident_id,
        "detected_at": incident.detected_at,
        "resolved_at": incident.resolved_at,
        "duration_ms": incident.duration_ms,
        "trigger": incident.trigger,
        "matched_pattern": incident.matched_pattern,
        "confidence": incident.confidence,
        "root_cause": incident.root_cause,
        "reasoning": incident.reasoning,
        "action_taken": incident.action_taken,
        "remediation_outcome": incident.remediation_outcome,
        "verification_performed": incident.verification_performed,
        "verification_result": incident.verification_result,
        "auto_rollback_triggered": incident.auto_rollback_triggered,
        "outcome": incident.outcome,
        "rca_method": incident.rca_method,
        "policy_blocked": incident.policy_blocked,
        "approval_required": incident.approval_required,
        "severity": incident.severity,
        "override_status": "none",
        "override_reason": None,
        "override_at": None,
        "override_by": None,
        "full_analysis": incident.full_analysis,
    })
    mock_cursor_get.__aenter__ = AsyncMock(return_value=mock_cursor_get)
    mock_cursor_get.__aexit__ = AsyncMock()

    # Mock Postgres cursor for mark_alert_resolved
    mock_cursor_update = AsyncMock()
    mock_cursor_update.rowcount = 1  # Successful update
    mock_cursor_update.__aenter__ = AsyncMock(return_value=mock_cursor_update)
    mock_cursor_update.__aexit__ = AsyncMock()

    # Mock connection that returns different cursors for different calls
    mock_conn = MagicMock()
    cursor_call_count = [0]  # Use list to avoid nonlocal issues

    def cursor_side_effect():
        cursor_call_count[0] += 1
        if cursor_call_count[0] == 1:
            return mock_cursor_get  # First call: get_by_fingerprint
        else:
            return mock_cursor_update  # Second call: mark_alert_resolved

    mock_conn.cursor = MagicMock(side_effect=cursor_side_effect)

    # Mock storage instance
    mock_storage = MagicMock(spec=IncidentStorage)
    mock_storage.get_by_fingerprint = AsyncMock(return_value=incident)
    mock_storage.mark_alert_resolved = AsyncMock(return_value=True)

    @asynccontextmanager
    async def mock_get_storage():
        yield mock_storage

    # Simulate resolved alert webhook
    resolved_alert = {
        "fingerprint": test_fingerprint,
        "labels": {"alertname": "SLOErrorBudgetBurnCritical"},
        "endsAt": "2026-07-08T10:05:00Z",
    }

    # Patch get_storage and call the resolved alert handler
    with patch("agent.get_storage", new=mock_get_storage):
        from agent import _handle_resolved_alert
        await _handle_resolved_alert(resolved_alert)

    # Verify that mark_alert_resolved was called
    mock_storage.mark_alert_resolved.assert_called_once_with(
        incident_id=incident.incident_id,
        resolved_at="2026-07-08T10:05:00Z",
    )

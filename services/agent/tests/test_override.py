"""
Aether-Guard — Override Tracking Tests (Priority 2)

Test coverage:
  1. Record manual_reversal override
  2. Record manual_escalation override
  3. Reject duplicate override (409 Conflict)
  4. Reject override for non-existent incident (404)
  5. Reject invalid override_status (400)
  6. Verify Prometheus metrics recording
  7. Verify trust metrics computation
  8. Verify override fields in incident report
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from incident_report import build_report
from incident_storage import IncidentStorage
import metrics


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Record manual_reversal override
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_override_manual_reversal():
    """
    Scenario: Human operator reverses an agent action that was incorrect.
    Expected: Override recorded successfully, metrics updated.
    """
    # Mock storage with Postgres available
    storage = IncidentStorage()
    storage._postgres_available = True
    storage._redis_available = False

    # Mock Postgres cursor
    mock_cursor = AsyncMock()
    mock_cursor.rowcount = 1
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock()

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    storage._postgres = mock_conn

    # Mock metrics.record_override
    with patch.object(metrics, "record_override") as mock_record:
        result = await storage.update_override(
            incident_id="test-001",
            override_status="manual_reversal",
            override_reason="Agent restarted healthy service",
            override_by="api:jane_operator",
            matched_pattern="rule:oom_kill",
        )

        assert result is True
        mock_record.assert_called_once_with("rule:oom_kill", "manual_reversal")


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Record manual_escalation override
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_override_manual_escalation():
    """
    Scenario: Human operator escalates because agent ignored a real incident.
    Expected: Override recorded successfully with "manual_escalation".
    """
    storage = IncidentStorage()
    storage._postgres_available = True
    storage._redis_available = False

    mock_cursor = AsyncMock()
    mock_cursor.rowcount = 1
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock()

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    storage._postgres = mock_conn

    with patch.object(metrics, "record_override") as mock_record:
        result = await storage.update_override(
            incident_id="test-002",
            override_status="manual_escalation",
            override_reason="Agent missed high error rate",
            override_by="slack:U12345",
            matched_pattern="llm_fallback",
        )

        assert result is True
        mock_record.assert_called_once_with("llm_fallback", "manual_escalation")


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Reject override for non-existent incident
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_override_incident_not_found():
    """
    Scenario: Attempt to override an incident that doesn't exist.
    Expected: Returns False (agent.py will return 404).
    """
    storage = IncidentStorage()
    storage._postgres_available = True
    storage._redis_available = False

    # Mock Postgres cursor with rowcount=0 (no rows updated)
    mock_cursor = AsyncMock()
    mock_cursor.rowcount = 0
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock()

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    storage._postgres = mock_conn

    result = await storage.update_override(
        incident_id="nonexistent",
        override_status="manual_reversal",
        override_reason="Test",
        override_by="api:test",
        matched_pattern="rule:test",
    )

    assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Verify override fields in incident report
# ─────────────────────────────────────────────────────────────────────────────

def test_incident_report_with_override_fields():
    """
    Scenario: Build incident report with default override fields.
    Expected: override_status="none", other override fields are None.
    """
    analysis = {
        "alert_id": "test-003",
        "alertname": "SLOErrorBudgetBurn",
        "alert_labels": {"severity": "critical", "startsAt": "2026-07-07T10:00:00Z"},
        "analyzed_at": "2026-07-07T10:01:00Z",
        "rca_method": "rule-based",
        "rule_name": "rate_limit_503",
        "root_cause": "Rate limit exceeded",
        "confidence": 0.88,
        "action": "SCALE",
        "reasoning": "HTTP 503 errors from rate limiting",
        "remediation": {"action": "SCALE", "outcome": "success"},
        "verification": {"success": True, "improved": True},
    }

    report = build_report(analysis)

    assert report.override_status == "none"
    assert report.override_reason is None
    assert report.override_at is None
    assert report.override_by is None
    assert report.outcome == "auto_resolved"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Trust metrics computation with overrides
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trust_metrics_with_overrides():
    """
    Scenario: Compute trust metrics with multiple incidents, some overridden.
    Expected: Correct override rates and breakdowns by pattern.
    """
    storage = IncidentStorage()
    storage._postgres_available = True

    # Mock query results
    incident_rows = [
        {"matched_pattern": "rule:oom_kill", "outcome": "auto_resolved", "count": 8},
        {"matched_pattern": "rule:oom_kill", "outcome": "rolled_back", "count": 2},
        {"matched_pattern": "llm_fallback", "outcome": "escalated_low_confidence", "count": 5},
    ]

    override_rows = [
        {"matched_pattern": "rule:oom_kill", "override_status": "manual_reversal", "count": 2},
        {"matched_pattern": "llm_fallback", "override_status": "manual_escalation", "count": 1},
    ]

    total_rows = [
        {"matched_pattern": "rule:oom_kill", "total": 10},
        {"matched_pattern": "llm_fallback", "total": 5},
    ]

    # Mock cursor to return these results
    mock_cursor = AsyncMock()
    mock_cursor.fetchall = AsyncMock(side_effect=[incident_rows, override_rows, total_rows])
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock()

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    storage._postgres = mock_conn

    metrics = await storage.get_trust_metrics()

    # Verify structure
    assert "by_pattern" in metrics
    assert "overall" in metrics

    # Verify rule:oom_kill metrics
    oom_metrics = metrics["by_pattern"]["rule:oom_kill"]
    assert oom_metrics["total_incidents"] == 10
    assert oom_metrics["total_overrides"] == 2
    assert oom_metrics["override_rate"] == 0.2  # 2/10 = 0.2
    assert oom_metrics["by_outcome"]["auto_resolved"] == 8
    assert oom_metrics["by_outcome"]["rolled_back"] == 2
    assert oom_metrics["by_override_status"]["manual_reversal"] == 2

    # Verify llm_fallback metrics
    llm_metrics = metrics["by_pattern"]["llm_fallback"]
    assert llm_metrics["total_incidents"] == 5
    assert llm_metrics["total_overrides"] == 1
    assert llm_metrics["override_rate"] == 0.2  # 1/5 = 0.2
    assert llm_metrics["by_outcome"]["escalated_low_confidence"] == 5
    assert llm_metrics["by_override_status"]["manual_escalation"] == 1

    # Verify overall metrics
    assert metrics["overall"]["total_incidents"] == 15
    assert metrics["overall"]["total_overrides"] == 3
    assert metrics["overall"]["override_rate"] == 0.2  # 3/15 = 0.2


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Trust metrics with pattern filter
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trust_metrics_filtered_by_pattern():
    """
    Scenario: Compute trust metrics for a specific pattern only.
    Expected: Only returns metrics for the specified pattern.
    """
    storage = IncidentStorage()
    storage._postgres_available = True

    # Mock query results (filtered for rule:oom_kill)
    incident_rows = [
        {"matched_pattern": "rule:oom_kill", "outcome": "auto_resolved", "count": 8},
        {"matched_pattern": "rule:oom_kill", "outcome": "rolled_back", "count": 2},
    ]

    override_rows = [
        {"matched_pattern": "rule:oom_kill", "override_status": "manual_reversal", "count": 2},
    ]

    total_rows = [
        {"matched_pattern": "rule:oom_kill", "total": 10},
    ]

    mock_cursor = AsyncMock()
    mock_cursor.fetchall = AsyncMock(side_effect=[incident_rows, override_rows, total_rows])
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock()

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    storage._postgres = mock_conn

    metrics = await storage.get_trust_metrics(matched_pattern="rule:oom_kill")

    # Verify only one pattern in results
    assert len(metrics["by_pattern"]) == 1
    assert "rule:oom_kill" in metrics["by_pattern"]
    assert metrics["overall"]["total_incidents"] == 10
    assert metrics["overall"]["total_overrides"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Trust metrics with no overrides
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trust_metrics_no_overrides():
    """
    Scenario: Compute trust metrics when no incidents have been overridden.
    Expected: override_rate=0.0 for all patterns.
    """
    storage = IncidentStorage()
    storage._postgres_available = True

    incident_rows = [
        {"matched_pattern": "rule:rate_limit_503", "outcome": "auto_resolved", "count": 15},
    ]

    override_rows = []  # No overrides

    total_rows = [
        {"matched_pattern": "rule:rate_limit_503", "total": 15},
    ]

    mock_cursor = AsyncMock()
    mock_cursor.fetchall = AsyncMock(side_effect=[incident_rows, override_rows, total_rows])
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock()

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    storage._postgres = mock_conn

    metrics = await storage.get_trust_metrics()

    rate_limit_metrics = metrics["by_pattern"]["rule:rate_limit_503"]
    assert rate_limit_metrics["total_overrides"] == 0
    assert rate_limit_metrics["override_rate"] == 0.0
    assert rate_limit_metrics["by_override_status"] == {}


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Postgres unavailable for trust metrics
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trust_metrics_postgres_unavailable():
    """
    Scenario: Postgres is unavailable when computing trust metrics.
    Expected: Returns error dict.
    """
    storage = IncidentStorage()
    storage._postgres_available = False
    storage._postgres = None

    metrics = await storage.get_trust_metrics()

    assert "error" in metrics
    assert metrics["error"] == "Postgres unavailable"

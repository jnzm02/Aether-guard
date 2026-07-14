"""
Aether-Guard — Embedding and Similarity Search Tests (Priority 8)

Tests for Phase A RAG foundation:
  1. Embedding text composition
  2. Similarity search with synthetic incidents
  3. Override field surfacing in results
  4. Graceful degradation when Voyage API unavailable
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from incident_report import IncidentReport
from embedding import compose_embedding_text, generate_embedding
from incident_storage import IncidentStorage


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Embedding text composition
# ─────────────────────────────────────────────────────────────────────────────

def test_compose_embedding_text_from_report():
    """
    Verify embedding text composition from IncidentReport includes all key fields.
    """
    report = IncidentReport(
        incident_id="test-001",
        trace_id="abc123",
        detected_at="2026-07-14T10:00:00Z",
        resolved_at="2026-07-14T10:05:00Z",
        duration_ms=300000,
        trigger="SLOErrorBudgetBurnCritical",
        matched_pattern="rule:oom_kill",
        confidence=0.95,
        root_cause="Out of Memory (OOM)",
        reasoning="OOM kill detected in kernel logs, heap usage 1.8GB",
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

    text = compose_embedding_text(report)

    # Verify all critical fields are included
    assert "SLOErrorBudgetBurnCritical" in text
    assert "rule:oom_kill" in text
    assert "Out of Memory (OOM)" in text
    assert "OOM kill detected in kernel logs" in text
    assert "RESTART" in text
    assert "auto_resolved" in text

    # Verify labeled format (not just raw concatenation)
    assert "Alert:" in text
    assert "Pattern:" in text
    assert "Root Cause:" in text
    assert "Reasoning:" in text
    assert "Action:" in text
    assert "Outcome:" in text


def test_compose_embedding_text_from_dict():
    """
    Verify embedding text composition works with dict input (for backfill script).
    """
    incident_dict = {
        "trigger": "TargetServiceDown",
        "matched_pattern": "rule:restart_loop",
        "root_cause": "Crash loop - pod restarting every 30s",
        "reasoning": "3 restarts in 5min, exit code 137 (OOM)",
        "action_taken": "SCALE",
        "outcome": "auto_resolved",
    }

    text = compose_embedding_text(incident_dict)

    assert "TargetServiceDown" in text
    assert "rule:restart_loop" in text
    assert "Crash loop" in text
    assert "SCALE" in text


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Voyage AI embedding generation (mocked)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_embedding_success():
    """
    Verify embedding generation returns 1024-dim vector.
    """
    # Mock httpx.AsyncClient
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [{"embedding": [0.1] * 1024, "index": 0}]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("embedding.httpx.AsyncClient") as mock_client:
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_context

        with patch("embedding.VOYAGE_API_KEY", "test-key"):
            embedding = await generate_embedding("test text")

            assert len(embedding) == 1024
            assert all(isinstance(v, float) for v in embedding)


@pytest.mark.asyncio
async def test_generate_embedding_missing_api_key():
    """
    Verify graceful error when VOYAGE_API_KEY not set.
    """
    with patch("embedding.VOYAGE_API_KEY", ""):
        with pytest.raises(ValueError, match="VOYAGE_API_KEY is not set"):
            await generate_embedding("test text")


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Similarity search with synthetic incidents
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_similar_incidents_returns_sensible_results():
    """
    Verify similarity search returns incidents sorted by distance.

    This test uses a mocked Postgres cursor to simulate similarity search results.
    """
    # Mock storage with mocked Postgres cursor
    storage = IncidentStorage()
    storage._postgres_available = True

    # Mock cursor returning 2 similar incidents
    mock_cursor = AsyncMock()
    mock_cursor.__aenter__.return_value.execute = AsyncMock()
    mock_cursor.__aenter__.return_value.fetchall = AsyncMock(return_value=[
        {
            "incident_id": "incident-001",
            "trigger": "SLOErrorBudgetBurnCritical",
            "root_cause": "Out of Memory (OOM)",
            "reasoning": "OOM kill detected in kernel logs",
            "matched_pattern": "rule:oom_kill",
            "confidence": 0.95,
            "outcome": "auto_resolved",
            "action_taken": "RESTART",
            "override_status": "none",
            "override_reason": None,
            "detected_at": datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc),
            "distance": 0.12,
        },
        {
            "incident_id": "incident-002",
            "trigger": "MemorySaturationWarning",
            "root_cause": "Memory leak in request handler",
            "reasoning": "Heap usage growing 10MB/min",
            "matched_pattern": "rule:memory_leak",
            "confidence": 0.88,
            "outcome": "auto_resolved",
            "action_taken": "RESTART",
            "override_status": "manual_reversal",
            "override_reason": "Agent restarted wrong pod - actually a traffic spike",
            "detected_at": datetime(2026, 6, 28, 15, 30, 0, tzinfo=timezone.utc),
            "distance": 0.25,
        },
    ])

    storage._postgres = MagicMock()
    storage._postgres.cursor = MagicMock(return_value=mock_cursor)

    # Query embedding (mocked 1024-dim vector)
    query_embedding = [0.5] * 1024

    # Perform similarity search
    results = await storage.find_similar_incidents(
        query_embedding=query_embedding,
        limit=5,
        min_confidence=0.7,
    )

    # Verify results
    assert len(results) == 2

    # First result (most similar)
    assert results[0]["incident_id"] == "incident-001"
    assert results[0]["trigger"] == "SLOErrorBudgetBurnCritical"
    assert results[0]["root_cause"] == "Out of Memory (OOM)"
    assert results[0]["matched_pattern"] == "rule:oom_kill"
    assert results[0]["confidence"] == 0.95
    assert results[0]["override_status"] == "none"
    assert results[0]["override_reason"] is None
    assert results[0]["distance"] == 0.12

    # Second result (overridden incident - still included!)
    assert results[1]["incident_id"] == "incident-002"
    assert results[1]["matched_pattern"] == "rule:memory_leak"
    assert results[1]["override_status"] == "manual_reversal"
    assert results[1]["override_reason"] == "Agent restarted wrong pod - actually a traffic spike"
    assert results[1]["distance"] == 0.25


@pytest.mark.asyncio
async def test_find_similar_incidents_filters_by_min_confidence():
    """
    Verify only incidents >= min_confidence are retrieved.
    """
    storage = IncidentStorage()
    storage._postgres_available = True

    # Mock cursor - simulate WHERE confidence >= 0.7 filter
    mock_cursor = AsyncMock()
    executed_query = None
    executed_params = None

    async def capture_execute(query, params):
        nonlocal executed_query, executed_params
        executed_query = query
        executed_params = params

    mock_cursor.__aenter__.return_value.execute = capture_execute
    mock_cursor.__aenter__.return_value.fetchall = AsyncMock(return_value=[])

    storage._postgres = MagicMock()
    storage._postgres.cursor = MagicMock(return_value=mock_cursor)

    query_embedding = [0.5] * 1024

    await storage.find_similar_incidents(
        query_embedding=query_embedding,
        limit=5,
        min_confidence=0.85,  # Higher threshold
    )

    # Verify SQL includes confidence filter with correct value
    assert "confidence >= %s" in executed_query
    assert 0.85 in executed_params


@pytest.mark.asyncio
async def test_find_similar_incidents_graceful_degradation():
    """
    Verify graceful degradation when Postgres unavailable (returns empty list).
    """
    storage = IncidentStorage()
    storage._postgres_available = False
    storage._postgres = None

    query_embedding = [0.5] * 1024

    results = await storage.find_similar_incidents(
        query_embedding=query_embedding,
        limit=5,
    )

    # Should return empty list, not raise exception
    assert results == []


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Backfill helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_all_without_embeddings():
    """
    Verify backfill helper retrieves incidents missing embeddings.
    """
    storage = IncidentStorage()
    storage._postgres_available = True

    # Mock cursor returning 3 incidents without embeddings
    mock_cursor = AsyncMock()
    mock_cursor.__aenter__.return_value.execute = AsyncMock()
    mock_cursor.__aenter__.return_value.fetchall = AsyncMock(return_value=[
        {
            "incident_id": "incident-001",
            "trigger": "SLOErrorBudgetBurnCritical",
            "matched_pattern": "rule:oom_kill",
            "root_cause": "Out of Memory (OOM)",
            "reasoning": "OOM kill detected",
            "action_taken": "RESTART",
            "outcome": "auto_resolved",
            "confidence": 0.95,
        },
        {
            "incident_id": "incident-002",
            "trigger": "MemorySaturationWarning",
            "matched_pattern": "rule:memory_leak",
            "root_cause": "Memory leak",
            "reasoning": "Heap growing",
            "action_taken": "RESTART",
            "outcome": "auto_resolved",
            "confidence": 0.88,
        },
    ])

    storage._postgres = MagicMock()
    storage._postgres.cursor = MagicMock(return_value=mock_cursor)

    incidents = await storage.get_all_without_embeddings(limit=10)

    assert len(incidents) == 2
    assert all("incident_id" in inc for inc in incidents)
    assert all("root_cause" in inc for inc in incidents)


@pytest.mark.asyncio
async def test_update_embedding():
    """
    Verify embedding update helper executes correct SQL.
    """
    storage = IncidentStorage()
    storage._postgres_available = True

    mock_cursor = AsyncMock()
    executed_query = None
    executed_params = None

    async def capture_execute(query, params):
        nonlocal executed_query, executed_params
        executed_query = query
        executed_params = params

    mock_cursor.__aenter__.return_value.execute = capture_execute

    storage._postgres = MagicMock()
    storage._postgres.cursor = MagicMock(return_value=mock_cursor)

    embedding = [0.1] * 1024
    success = await storage.update_embedding("incident-001", embedding)

    assert success is True
    assert "UPDATE incident_reports SET embedding" in executed_query
    assert "incident-001" in executed_params

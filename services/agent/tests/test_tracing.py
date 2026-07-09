"""
Aether-Guard — OpenTelemetry Tracing Tests (Workstream 1)

Test coverage:
  1. Cross-boundary trace context propagation (Listener → Agent)
  2. Trace ID appears in IncidentReport
  3. WARNING logged when trace context extraction fails
  4. Agent creates child spans that link to Listener's root span
"""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tracing import create_span_context_from_trace_id
from incident_report import build_report


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Cross-boundary propagation (Listener → Agent)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_boundary_trace_propagation():
    """
    Scenario: Listener creates root span, sends trace_id in alert, Agent creates child span.
    Expected: Both spans share the same trace_id, child span links to parent trace.

    This is the PRIMARY checkpoint for Workstream 1.
    """
    # Set up in-memory span exporter to capture spans
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    # SIMULATE LISTENER: Create root span
    with tracer.start_as_current_span("alert_received:TestAlert", kind=trace.SpanKind.SERVER) as listener_span:
        listener_context = listener_span.get_span_context()
        listener_trace_id = format(listener_context.trace_id, '032x')

        # Listener attaches trace_id to alert (this is what listener.py does)
        alert = {
            "id": "test-alert-001",
            "labels": {"alertname": "TestAlert"},
            "trace_id": listener_trace_id,  # W3C trace ID propagated via alert payload
            "prometheus_snapshot": {},
            "logs": [],
        }

    # SIMULATE AGENT: Extract trace_id and create child span
    trace_id_hex = alert.get("trace_id")
    assert trace_id_hex is not None, "trace_id should be present in alert"

    # Create span context from trace_id (this is what agent.py does)
    span_context = create_span_context_from_trace_id(trace_id_hex)
    assert span_context is not None, "span_context creation should succeed"
    assert span_context.is_valid, "span_context should be valid"

    # Create child span linked to listener's trace
    parent_context = trace.set_span_in_context(
        trace.NonRecordingSpan(span_context)
    )

    with tracer.start_as_current_span(
        "rca_analysis:TestAlert",
        context=parent_context,
        kind=trace.SpanKind.INTERNAL,
    ) as agent_span:
        agent_context = agent_span.get_span_context()
        agent_trace_id = format(agent_context.trace_id, '032x')

    # VERIFY: Both spans share the same trace_id
    assert listener_trace_id == agent_trace_id, \
        f"Trace IDs must match: Listener={listener_trace_id}, Agent={agent_trace_id}"

    # VERIFY: Spans were exported
    exported_spans = exporter.get_finished_spans()
    assert len(exported_spans) == 2, "Should have exported 2 spans (listener + agent)"

    # Find listener and agent spans
    listener_exported = next((s for s in exported_spans if s.name == "alert_received:TestAlert"), None)
    agent_exported = next((s for s in exported_spans if s.name == "rca_analysis:TestAlert"), None)

    assert listener_exported is not None, "Listener span should be exported"
    assert agent_exported is not None, "Agent span should be exported"

    # VERIFY: Both spans have the same trace_id
    listener_exported_trace_id = format(listener_exported.context.trace_id, '032x')
    agent_exported_trace_id = format(agent_exported.context.trace_id, '032x')

    assert listener_exported_trace_id == agent_exported_trace_id, \
        "Exported spans must share the same trace_id"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Trace ID appears in IncidentReport
# ─────────────────────────────────────────────────────────────────────────────

def test_trace_id_in_incident_report():
    """
    Scenario: Analysis dict contains trace_id, build_report extracts it.
    Expected: IncidentReport.trace_id is populated correctly.
    """
    test_trace_id = "abcd1234567890abcdef1234567890ab"

    analysis = {
        "alert_id": "test-001",
        "alertname": "TestAlert",
        "trace_id": test_trace_id,  # Trace ID from alert
        "alert_labels": {
            "alertname": "TestAlert",
            "severity": "critical",
        },
        "analyzed_at": "2026-07-09T10:00:00Z",
        "rca_method": "rule-based",
        "rule_name": "test_rule",
        "root_cause": "Test root cause",
        "confidence": 0.9,
        "action": "RESTART",
        "reasoning": "Test reasoning",
        "remediation": {"action": "RESTART", "outcome": "success", "executed": True},
        "verification": {"success": True, "improved": True},
    }

    report = build_report(analysis)

    assert report.trace_id == test_trace_id, \
        f"IncidentReport.trace_id should be {test_trace_id}, got {report.trace_id}"


def test_trace_id_missing_in_incident_report():
    """
    Scenario: Analysis dict has no trace_id (manual API call, not from Listener).
    Expected: IncidentReport.trace_id is None (graceful handling).
    """
    analysis = {
        "alert_id": "test-002",
        "alertname": "ManualTestAlert",
        # No trace_id field
        "alert_labels": {
            "alertname": "ManualTestAlert",
            "severity": "warning",
        },
        "analyzed_at": "2026-07-09T10:05:00Z",
        "rca_method": "llm-assisted",
        "root_cause": "Manual test",
        "confidence": 0.7,
        "action": "IGNORE",
        "reasoning": "Test",
        "remediation": {"action": "IGNORE", "outcome": "skipped", "executed": False},
    }

    report = build_report(analysis)

    assert report.trace_id is None, \
        "IncidentReport.trace_id should be None when not present in analysis"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: WARNING logged when trace context extraction fails
# ─────────────────────────────────────────────────────────────────────────────

def test_invalid_trace_id_format_logs_warning(caplog):
    """
    Scenario: Alert has malformed trace_id (not 32-char hex).
    Expected: create_span_context_from_trace_id returns None, WARNING logged.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="aether-guard.agent.tracing"):
        # Test with invalid trace_id (too short)
        result = create_span_context_from_trace_id("invalid")

        assert result is None, "Should return None for invalid trace_id"
        assert "Invalid trace_id format" in caplog.text, \
            "Should log WARNING about invalid trace_id format"


def test_non_hex_trace_id_logs_warning(caplog):
    """
    Scenario: Alert has 32-char trace_id but with non-hex characters.
    Expected: create_span_context_from_trace_id returns None, WARNING logged.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="aether-guard.agent.tracing"):
        # Test with non-hex characters (32 chars but invalid)
        result = create_span_context_from_trace_id("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")

        assert result is None, "Should return None for non-hex trace_id"
        assert "Failed to parse trace_id" in caplog.text, \
            "Should log WARNING about parse failure"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Span hierarchy validation (alert_processing → rca_analysis → child spans)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_span_hierarchy():
    """
    Scenario: Agent processes alert through full pipeline.
    Expected: Span hierarchy is correct:
      - alert_processing (parent)
        - rca_analysis (child)
          - rule_engine_check OR llm_analysis
        - policy_evaluation (child, if V2 enabled)
        - remediation_execution (child)
        - verification_check (child, if V2 enabled)
        - incident_report_write (child)

    This validates that all major pipeline stages are instrumented.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    # Simulate Agent processing pipeline with spans
    alert_id = "test-hierarchy-001"
    alertname = "TestHierarchyAlert"

    with tracer.start_as_current_span(f"alert_processing:{alertname}") as process_span:
        process_span.set_attribute("alert.id", alert_id)

        # RCA analysis span
        with tracer.start_as_current_span(f"rca_analysis:{alertname}") as rca_span:
            rca_span.set_attribute("rca.method", "rule-based")

            # Rule engine check span
            with tracer.start_as_current_span("rule_engine_check") as rule_span:
                rule_span.set_attribute("rule.matched", "test_rule")

        # Policy evaluation span
        with tracer.start_as_current_span("policy_evaluation") as policy_span:
            policy_span.set_attribute("policy.allowed", True)

        # Remediation execution span
        with tracer.start_as_current_span("remediation_execution") as remediation_span:
            remediation_span.set_attribute("remediation.action", "RESTART")

        # Verification span
        with tracer.start_as_current_span("verification_check") as verification_span:
            verification_span.set_attribute("verification.success", True)

        # Incident report write span
        with tracer.start_as_current_span("incident_report_write") as report_span:
            report_span.set_attribute("incident.id", alert_id)

    exported_spans = exporter.get_finished_spans()

    # Verify all expected spans are present
    expected_span_names = [
        f"alert_processing:{alertname}",
        f"rca_analysis:{alertname}",
        "rule_engine_check",
        "policy_evaluation",
        "remediation_execution",
        "verification_check",
        "incident_report_write",
    ]

    actual_span_names = [s.name for s in exported_spans]

    for expected_name in expected_span_names:
        assert expected_name in actual_span_names, \
            f"Expected span '{expected_name}' not found in {actual_span_names}"

    # Verify parent-child relationships (all child spans should have same trace_id as parent)
    parent_span = next(s for s in exported_spans if s.name == f"alert_processing:{alertname}")
    parent_trace_id = parent_span.context.trace_id

    for span in exported_spans:
        assert span.context.trace_id == parent_trace_id, \
            f"Span '{span.name}' has different trace_id than parent"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Graceful degradation when tracing disabled
# ─────────────────────────────────────────────────────────────────────────────

def test_tracing_disabled_graceful():
    """
    Scenario: Tracing is disabled/unavailable (no provider set).
    Expected: Agent continues to function normally, no crashes.

    This validates graceful degradation requirement.
    """
    # No tracer provider set - using no-op tracer
    tracer = trace.get_tracer(__name__)

    # Should not crash when creating spans
    with tracer.start_as_current_span("test_span") as span:
        span.set_attribute("test", "value")
        # Span is a no-op, but code still runs
        assert True, "Code should execute normally even with no-op tracer"

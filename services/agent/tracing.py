"""
Aether-Guard Agent — OpenTelemetry Tracing Setup

Initializes distributed tracing with Grafana Tempo backend.
Extracts trace context from Listener alerts and creates child spans for RCA analysis.
"""

import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.trace import SpanContext, TraceFlags, TraceState

log = logging.getLogger("aether-guard.agent.tracing")

# Configuration
TEMPO_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4317")
SERVICE_NAME_VALUE = "aether-guard-agent"


def init_tracing() -> trace.Tracer:
    """
    Initialize OpenTelemetry tracing with OTLP exporter to Tempo.

    Returns:
        Tracer instance for creating manual spans
    """
    # Create resource identifying this service
    resource = Resource(attributes={
        SERVICE_NAME: SERVICE_NAME_VALUE,
        "service.version": "1.1.0",
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    })

    # Set up tracer provider
    provider = TracerProvider(resource=resource)

    # Configure OTLP exporter (gRPC to Tempo)
    otlp_exporter = OTLPSpanExporter(
        endpoint=TEMPO_ENDPOINT,
        insecure=True,  # Use insecure gRPC for local development
    )

    # Add batch span processor (efficient export)
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # Set as global tracer provider
    trace.set_tracer_provider(provider)

    log.info("OpenTelemetry initialized: exporting to %s", TEMPO_ENDPOINT)

    # Return tracer for manual span creation
    return trace.get_tracer(__name__)


def instrument_fastapi(app):
    """
    Auto-instrument FastAPI application.

    Excludes health/metrics endpoints from tracing to reduce noise.
    """
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="/health,/metrics,/ready",
    )
    log.info("FastAPI auto-instrumentation enabled")


def instrument_httpx():
    """
    Auto-instrument httpx client (for Listener polling, Prometheus queries, etc.)
    """
    HTTPXClientInstrumentor().instrument()
    log.info("HTTPx client instrumentation enabled")


def create_span_context_from_trace_id(trace_id_hex: str) -> SpanContext | None:
    """
    Reconstruct a SpanContext from a W3C trace ID (hex string).

    This is used when the Agent receives an alert with a trace_id from the Listener.
    The Agent doesn't have the full span context (no parent span ID), so we create
    a link to the trace using just the trace ID.

    Args:
        trace_id_hex: 32-character hex string (W3C trace ID format)

    Returns:
        SpanContext if valid, None if parsing fails
    """
    if not trace_id_hex or len(trace_id_hex) != 32:
        log.warning("Invalid trace_id format: %s (expected 32-char hex)", trace_id_hex)
        return None

    try:
        import secrets

        # Parse hex string to int (W3C trace IDs are 128-bit integers)
        trace_id = int(trace_id_hex, 16)

        # Create span context with trace ID
        # We don't have the parent span ID from Listener, so generate a random one
        # (the actual child span will have its own unique span_id)
        span_id = secrets.randbits(64)  # Generate random 64-bit span ID

        span_context = SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=True,  # Indicates this came from another service
            trace_flags=TraceFlags(0x01),  # Sampled
            trace_state=TraceState(),
        )

        return span_context

    except (ValueError, TypeError) as exc:
        log.warning("Failed to parse trace_id '%s': %s", trace_id_hex, exc)
        return None

"""
Aether-Guard Listener — OpenTelemetry Tracing Setup

Initializes distributed tracing with Grafana Tempo backend.
Creates root spans for incoming Alertmanager webhooks.
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

log = logging.getLogger("aether-guard.listener.tracing")

# Configuration
TEMPO_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4317")
SERVICE_NAME_VALUE = "aether-guard-listener"


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

    This creates spans automatically for all HTTP requests, but we'll add
    manual spans for specific operations (alert enrichment, etc.)

    Excludes health/metrics endpoints from tracing to reduce noise.
    """
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="/health,/metrics,/ready",  # Skip non-incident endpoints
    )
    log.info("FastAPI auto-instrumentation enabled")


def instrument_httpx():
    """
    Auto-instrument httpx client (for Prometheus queries, etc.)
    """
    HTTPXClientInstrumentor().instrument()
    log.info("HTTPx client instrumentation enabled")

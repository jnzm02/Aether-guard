#!/usr/bin/env python3
"""
Aether-Guard Alert Listener — Phase 2

Responsibilities:
  1. Receive Alertmanager webhook POSTs at POST /webhook
  2. Enrich each alert with:
       - A snapshot of key Prometheus metrics at alert-fire time
       - The last 100 log lines from the target-service container
  3. Store enriched alerts in an in-memory queue
  4. Expose the queue at GET /alerts for the Phase 3 AI Agent to consume

Queue lifecycle:
  pending  → AI Agent picks it up  → processed_by_ai=True  → ACKed via POST /alerts/{id}/ack
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import docker
import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger("aether-guard.listener")

# ─────────────────────────────────────────────────────────────────────────────
# Config (injectable via environment variables)
# ─────────────────────────────────────────────────────────────────────────────
PROMETHEUS_URL     = os.getenv("PROMETHEUS_URL",     "http://prometheus:9090")
TARGET_CONTAINER   = os.getenv("TARGET_CONTAINER",   "target-service")
MAX_QUEUE_SIZE     = int(os.getenv("MAX_QUEUE_SIZE", "500"))

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
_LISTENER_DESCRIPTION = """
## Alertmanager Webhook Receiver

The Listener is the ingestion layer of Aether-Guard. It:

1. Receives Alertmanager webhook payloads at **POST /webhook**
2. Enriches each alert with a **Prometheus metrics snapshot** and the last **100 log lines** from the target container
3. Queues enriched alerts for the AI Agent to poll

### Alert lifecycle
```
firing → enriched → queued → agent polls → ack'd (processed=true)
```
"""

_LISTENER_TAGS = [
    {
        "name": "alertmanager",
        "description": "Alertmanager webhook endpoint — receives firing/resolved alerts.",
    },
    {
        "name": "alerts",
        "description": "Alert queue management — list, retrieve, and acknowledge alerts.",
    },
    {
        "name": "observability",
        "description": "Health checks and Prometheus snapshot diagnostics.",
    },
]

app = FastAPI(
    title="Aether-Guard Alert Listener",
    description=_LISTENER_DESCRIPTION,
    version="1.1.0",
    contact={
        "name": "Aether-Guard on GitHub",
        "url": "https://github.com/jnzm02/Aether-guard",
    },
    license_info={"name": "MIT"},
    openapi_tags=_LISTENER_TAGS,
)

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────

class AlertLabel(BaseModel):
    alertname: str = Field(..., examples=["SLOErrorBudgetBurnCritical"])
    severity: str = Field(..., examples=["critical"])
    slo: str | None = Field(None, examples=["availability"])


class WebhookAlertItem(BaseModel):
    status: str = Field(..., examples=["firing"])
    labels: dict = Field(default_factory=dict)
    annotations: dict = Field(default_factory=dict)
    startsAt: str = Field(..., examples=["2026-04-02T10:00:00Z"])
    endsAt: str = Field("0001-01-01T00:00:00Z", examples=["0001-01-01T00:00:00Z"])
    generatorURL: str = Field("", examples=["http://prometheus:9090/graph"])
    fingerprint: str = Field("", examples=["abc123def456"])


class WebhookPayload(BaseModel):
    version: str = Field("4", examples=["4"])
    groupKey: str = Field("", examples=["{alertname='SLOErrorBudgetBurnCritical'}"])
    receiver: str = Field("", examples=["aether-guard-webhook"])
    status: str = Field("", examples=["firing"])
    alerts: list[WebhookAlertItem] = Field(default_factory=list)
    groupLabels: dict = Field(default_factory=dict)
    commonLabels: dict = Field(default_factory=dict)
    commonAnnotations: dict = Field(default_factory=dict)
    externalURL: str = Field("", examples=["http://alertmanager:9093"])
    truncatedAlerts: int = Field(0, examples=[0])

    model_config = {"extra": "allow"}


class WebhookResponse(BaseModel):
    received: int = Field(..., examples=[1])
    queued: int = Field(..., examples=[1])
    skipped: int = Field(..., examples=[0])


class AckPayload(BaseModel):
    analysis: str = Field(..., examples=["Error ratio 97% caused by chaos/error injection."])
    action: str = Field(..., examples=["RESTART"])
    confidence: float = Field(..., ge=0.0, le=1.0, examples=[0.92])


class AckResponse(BaseModel):
    id: str = Field(..., examples=["abc12345-dead-beef-0000-000000000000"])
    status: str = Field(..., examples=["ack'd"])


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    service: str = Field(..., examples=["aether-guard/listener"])
    version: str = Field(..., examples=["1.1.0"])
    queue_depth: int = Field(..., examples=[3])
    unprocessed: int = Field(..., examples=[1])
    docker_available: bool = Field(..., examples=[True])


# In-memory alert queue.  Phase 3 AI Agent reads from here.
alert_queue: list[dict[str, Any]] = []

# Docker client (used for log fetching). Fails gracefully if socket unavailable.
try:
    _docker_client = docker.from_env()
    _docker_client.ping()
    log.info("Docker socket connected — log fetching enabled")
except Exception as exc:
    _docker_client = None
    log.warning("Docker socket unavailable (%s) — log lines will be empty", exc)

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus enrichment
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: (human-readable key, PromQL expression)
_KEY_METRICS: list[tuple[str, str]] = [
    (
        "error_ratio_5m",
        'sum(rate(aether_guard_http_requests_total{status_code=~"5.."}[5m]))'
        " / sum(rate(aether_guard_http_requests_total[5m]))",
    ),
    (
        "latency_p99_5m_seconds",
        "histogram_quantile(0.99,"
        " sum(rate(aether_guard_http_request_duration_seconds_bucket[5m])) by (le))",
    ),
    (
        "latency_p50_5m_seconds",
        "histogram_quantile(0.50,"
        " sum(rate(aether_guard_http_request_duration_seconds_bucket[5m])) by (le))",
    ),
    (
        "request_rate_5m_rps",
        "sum(rate(aether_guard_http_requests_total[5m]))",
    ),
    (
        "memleak_bytes_allocated",
        "aether_guard_chaos_memleak_bytes_allocated",
    ),
    (
        "chaos_errors_injected_total",
        "sum(aether_guard_chaos_errors_injected_total)",
    ),
]


async def fetch_prometheus_snapshot() -> dict[str, Any]:
    """Query each golden-signal metric from Prometheus and return a snapshot dict."""
    snapshot: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for label, expr in _KEY_METRICS:
            try:
                resp = await client.get(
                    f"{PROMETHEUS_URL}/api/v1/query",
                    params={"query": expr},
                )
                resp.raise_for_status()
                result = resp.json().get("data", {}).get("result", [])
                snapshot[label] = float(result[0]["value"][1]) if result else None
            except Exception as exc:
                log.warning("Prometheus query failed [%s]: %s", label, exc)
                snapshot[label] = None
    return snapshot


# ─────────────────────────────────────────────────────────────────────────────
# Log enrichment
# ─────────────────────────────────────────────────────────────────────────────

def fetch_container_logs(container_name: str, tail: int = 100) -> list[str]:
    """
    Fetch the last `tail` log lines from the named Docker container.
    Returns a list of strings.  Fails gracefully — never raises.
    """
    if _docker_client is None:
        return ["[Docker socket unavailable — log fetching disabled]"]
    try:
        container = _docker_client.containers.get(container_name)
        raw: bytes = container.logs(tail=tail, timestamps=True)
        return raw.decode("utf-8", errors="replace").splitlines()
    except docker.errors.NotFound:
        return [f"[container '{container_name}' not found]"]
    except Exception as exc:
        log.warning("Log fetch failed for container '%s': %s", container_name, exc)
        return [f"[log fetch error: {exc}]"]


# ─────────────────────────────────────────────────────────────────────────────
# Alert enrichment pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def enrich_alert(raw: dict) -> dict:
    """
    Given a raw Alertmanager alert object, produce an enriched record that
    bundles the alert metadata with a live Prometheus snapshot and log tail.
    This is the data payload the Phase 3 AI Agent will reason over.
    """
    # Run Prometheus query and log fetch concurrently.
    metrics_snapshot, log_tail = await asyncio.gather(
        fetch_prometheus_snapshot(),
        asyncio.get_event_loop().run_in_executor(
            None, fetch_container_logs, TARGET_CONTAINER, 100
        ),
    )

    return {
        "id":              str(uuid4()),
        "received_at":     datetime.now(timezone.utc).isoformat(),
        "status":          raw.get("status", "unknown"),   # "firing" | "resolved"
        "labels":          raw.get("labels", {}),
        "annotations":     raw.get("annotations", {}),
        "starts_at":       raw.get("startsAt"),
        "ends_at":         raw.get("endsAt"),
        "generator_url":   raw.get("generatorURL", ""),
        "fingerprint":     raw.get("fingerprint", ""),
        # ── Enrichment ───────────────────────────────────────────────────────
        "metrics_snapshot": metrics_snapshot,
        "log_tail":         log_tail,
        # ── AI Agent state ────────────────────────────────────────────────────
        "processed_by_ai":  False,
        "ai_analysis":      None,
        "action_taken":     None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.post(
    "/webhook",
    status_code=202,
    tags=["alertmanager"],
    summary="Receive Alertmanager webhook payload",
    responses={
        202: {"description": "Alerts received and queued for AI Agent processing"},
        422: {"description": "Invalid payload structure"},
    },
)
async def receive_webhook(request: Request):
    """
    Alertmanager webhook endpoint.
    Payload schema: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config

    Alertmanager sends a single POST per group-interval with a batch of alerts.
    We enrich each alert concurrently and append to the queue.
    """
    payload = await request.json()
    raw_alerts: list[dict] = payload.get("alerts", [])

    log.info(
        "Webhook received  receiver=%s  status=%s  alerts=%d",
        payload.get("receiver"),
        payload.get("status"),
        len(raw_alerts),
    )

    if not raw_alerts:
        return {"status": "accepted", "enqueued": 0}

    # Trim queue if it would exceed the cap (oldest entries dropped).
    overflow = (len(alert_queue) + len(raw_alerts)) - MAX_QUEUE_SIZE
    if overflow > 0:
        del alert_queue[:overflow]
        log.warning("Alert queue trimmed by %d entries to stay within cap", overflow)

    enriched = await asyncio.gather(*[enrich_alert(a) for a in raw_alerts])
    for alert in enriched:
        alert_queue.append(alert)
        log.info(
            "  ↳ enqueued  id=%s  alertname=%s  status=%s",
            alert["id"],
            alert["labels"].get("alertname", "?"),
            alert["status"],
        )

    return {"status": "accepted", "enqueued": len(enriched)}


@app.get(
    "/alerts",
    tags=["alerts"],
    summary="List queued alerts",
    responses={
        200: {
            "description": "All alerts in the queue, optionally filtered to unprocessed only",
            "content": {
                "application/json": {
                    "example": {
                        "alerts": [
                            {
                                "id": "abc12345",
                                "alertname": "SLOErrorBudgetBurnCritical",
                                "status": "firing",
                                "processed": False,
                            }
                        ],
                        "total": 1,
                        "unprocessed": 1,
                    }
                }
            },
        }
    },
)
async def list_alerts(unprocessed_only: bool = False):
    """
    Return all enriched alerts.  Phase 3 AI Agent polls this endpoint.

    Query params:
      unprocessed_only=true — return only alerts not yet handled by the AI Agent
    """
    result = alert_queue if not unprocessed_only else [
        a for a in alert_queue if not a["processed_by_ai"]
    ]
    return {
        "alerts": result,
        "total":  len(alert_queue),
        "pending": sum(1 for a in alert_queue if not a["processed_by_ai"]),
    }


@app.get(
    "/alerts/{alert_id}",
    tags=["alerts"],
    summary="Get a specific alert by ID",
    responses={
        200: {"description": "Full enriched alert record including metrics snapshot and log tail"},
        404: {"description": "Alert not found"},
    },
)
async def get_alert(alert_id: str):
    """Fetch a single enriched alert by its UUID."""
    for alert in alert_queue:
        if alert["id"] == alert_id:
            return alert
    raise HTTPException(status_code=404, detail=f"Alert {alert_id!r} not found")


@app.post(
    "/alerts/{alert_id}/ack",
    tags=["alerts"],
    summary="Acknowledge an alert (mark as processed by agent)",
    response_model=AckResponse,
    responses={
        200: {"description": "Alert marked as processed"},
        404: {"description": "Alert not found"},
    },
)
async def acknowledge_alert(alert_id: str, request: Request):
    """
    Called by the Phase 3 AI Agent once it has processed an alert.
    Accepts a JSON body with the agent's analysis result.
    """
    body = await request.json()
    for alert in alert_queue:
        if alert["id"] == alert_id:
            alert["processed_by_ai"] = True
            alert["ai_analysis"]     = body.get("analysis")
            alert["action_taken"]    = body.get("action")
            log.info("Alert %s acknowledged — action=%s", alert_id, body.get("action"))
            return {"id": alert_id, "status": "ack'd"}
    raise HTTPException(status_code=404, detail=f"Alert {alert_id!r} not found")


@app.get(
    "/metrics-snapshot",
    tags=["observability"],
    summary="Fetch current Prometheus metrics snapshot",
    responses={
        200: {"description": "Latest Prometheus metrics for the target service"},
    },
)
async def current_metrics():
    """
    Convenience endpoint: live Prometheus metric snapshot without needing an alert.
    Useful for the AI Agent to get current state on demand.
    """
    return await fetch_prometheus_snapshot()


@app.get(
    "/health",
    tags=["observability"],
    summary="Listener health check",
    response_model=HealthResponse,
    responses={200: {"description": "Listener is running and ready to receive alerts"}},
)
async def health():
    return {
        "status":           "ok",
        "service":          "aether-guard/listener",
        "version":          "1.1.0",
        "queue_depth":      len(alert_queue),
        "unprocessed":      sum(1 for a in alert_queue if not a["processed_by_ai"]),
        "docker_available": _docker_client is not None,
    }

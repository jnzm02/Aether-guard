"""
Aether-Guard — Alert Enrichment Module (Priority 3)

Shared enrichment logic for adding Prometheus metrics snapshots and container
logs to raw Alertmanager alert payloads.

This module is used by:
  - Agent webhook handler (POST /webhook/alertmanager)
  - [Future] Listener service (to avoid code duplication)

Design:
  - Fetches golden-signal metrics from Prometheus (error rate, latency, throughput, saturation)
  - Fetches last N log lines from target Docker container
  - Fails gracefully — returns partial data if Prometheus/Docker unavailable
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import docker
import httpx

log = logging.getLogger("aether-guard.agent.enrichment")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
TARGET_CONTAINER = os.getenv("TARGET_CONTAINER", "target-service")
MONITORED_JOB = os.getenv("MONITORED_JOB", "target-service")

# ─────────────────────────────────────────────────────────────────────────────
# Docker client (for log fetching)
# ─────────────────────────────────────────────────────────────────────────────

_docker_client: docker.DockerClient | None = None

try:
    _docker_client = docker.from_env()
    _docker_client.ping()
    log.info("Docker socket connected — log fetching enabled")
except Exception as exc:
    _docker_client = None
    log.warning("Docker socket unavailable (%s) — log lines will be empty", exc)

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus golden-signal metrics
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

# Build runtime metrics with configurable job name
def _build_key_metrics():
    """Build metrics list with MONITORED_JOB substituted."""
    base_metrics = [
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
        (
            "runtime_goroutines",
            f'go_goroutines{{job="{MONITORED_JOB}"}}',
        ),
        (
            "runtime_heap_inuse_bytes",
            f'go_memstats_heap_inuse_bytes{{job="{MONITORED_JOB}"}}',
        ),
        (
            "cpu_usage_percent",
            f'rate(process_cpu_seconds_total{{job="{MONITORED_JOB}"}}[5m]) * 100',
        ),
    ]
    return base_metrics

_KEY_METRICS = _build_key_metrics()


async def fetch_prometheus_snapshot() -> dict[str, Any]:
    """
    Query each golden-signal metric from Prometheus and return a snapshot dict.

    Returns:
        Dict mapping metric labels to float values (or None if query failed).
        Example: {"error_ratio_5m": 0.03, "latency_p99_5m_seconds": 0.25, ...}
    """
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


def fetch_container_logs(container_name: str, tail: int = 100) -> list[str]:
    """
    Fetch the last `tail` log lines from the named Docker container.

    Args:
        container_name: Name of the Docker container to query
        tail: Number of recent log lines to fetch

    Returns:
        List of log line strings with timestamps.
        Returns error message strings if Docker unavailable or container not found.
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


async def enrich_alert(raw_alert: dict[str, Any], generate_id: bool = True) -> dict[str, Any]:
    """
    Enrich a raw Alertmanager alert with Prometheus metrics and container logs.

    This is the same enrichment pipeline used by the listener service, extracted
    into a shared module to avoid duplication (Priority 3 requirement).

    Args:
        raw_alert: Raw Alertmanager alert dict with fields:
                   status, labels, annotations, startsAt, endsAt, generatorURL, fingerprint
        generate_id: If True, generate a new UUID for the alert. If False, preserve existing ID.

    Returns:
        Enriched alert dict with additional fields:
          - id: UUID (generated or preserved)
          - received_at: ISO timestamp when enrichment occurred
          - metrics_snapshot: Dict of Prometheus metric values
          - log_tail: List of recent container log lines
          - processed_by_ai: False (initial state)
          - ai_analysis: None (populated later by RCA pipeline)
          - action_taken: None (populated later by remediation)
    """
    # Run Prometheus query and log fetch concurrently
    metrics_snapshot, log_tail = await asyncio.gather(
        fetch_prometheus_snapshot(),
        asyncio.get_event_loop().run_in_executor(
            None, fetch_container_logs, TARGET_CONTAINER, 100
        ),
    )

    # Build enriched alert structure
    enriched = {
        "id": str(uuid4()) if generate_id else raw_alert.get("id", str(uuid4())),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "status": raw_alert.get("status", "unknown"),  # "firing" | "resolved"
        "labels": raw_alert.get("labels", {}),
        "annotations": raw_alert.get("annotations", {}),
        "starts_at": raw_alert.get("startsAt"),
        "ends_at": raw_alert.get("endsAt"),
        "generator_url": raw_alert.get("generatorURL", ""),
        "fingerprint": raw_alert.get("fingerprint", ""),
        # ── Enrichment ───────────────────────────────────────────────────────
        "metrics_snapshot": metrics_snapshot,
        "log_tail": log_tail,
        # ── AI Agent state ────────────────────────────────────────────────────
        "processed_by_ai": False,
        "ai_analysis": None,
        "action_taken": None,
    }

    return enriched

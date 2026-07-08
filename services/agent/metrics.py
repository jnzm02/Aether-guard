"""
Aether-Guard — Prometheus Metrics Exporter (Priority 2)

Exposes trust metrics for monitoring agent reliability:
  - Incidents by matched_pattern and outcome
  - Overrides by matched_pattern

These metrics feed into Grafana dashboards to compute override rates
and identify which RCA patterns need improvement.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

if TYPE_CHECKING:
    from incident_storage import IncidentStorage

log = logging.getLogger("aether-guard.agent.metrics")

# ── Prometheus Counters ───────────────────────────────────────────────────────

# Total incidents by pattern and outcome
# Use case: Track how often each RCA pattern fires and what the outcome was
incidents_total = Counter(
    "aether_guard_incidents_total",
    "Total incidents processed by the agent, labeled by matched pattern and outcome",
    labelnames=["matched_pattern", "outcome"],
)

# Total overrides by pattern
# Use case: Track when humans override/escalate agent decisions, sliced by pattern
overrides_total = Counter(
    "aether_guard_overrides_total",
    "Total human overrides (manual_reversal or manual_escalation), labeled by matched pattern",
    labelnames=["matched_pattern", "override_status"],
)

# ── Metric Population Functions ───────────────────────────────────────────────

def record_incident(matched_pattern: str, outcome: str) -> None:
    """
    Increment incidents_total counter for the given pattern and outcome.

    Called automatically when an incident report is saved to Postgres.
    """
    incidents_total.labels(matched_pattern=matched_pattern, outcome=outcome).inc()
    log.debug("Metrics: incidents_total{%s, %s} += 1", matched_pattern, outcome)


def record_override(matched_pattern: str, override_status: str) -> None:
    """
    Increment overrides_total counter for the given pattern and override type.

    Called when a human records an override via POST /incidents/{id}/override.
    """
    overrides_total.labels(matched_pattern=matched_pattern, override_status=override_status).inc()
    log.debug("Metrics: overrides_total{%s, %s} += 1", matched_pattern, override_status)


async def populate_metrics_from_storage(storage: IncidentStorage) -> None:
    """
    Populate Prometheus counters from Postgres on agent startup.

    This ensures metrics are accurate even if the agent restarts. We replay
    the full incident history from Postgres to rebuild counter state.

    Called once during FastAPI lifespan startup.
    """
    log.info("Populating Prometheus metrics from Postgres...")

    try:
        # Fetch all incidents from Postgres (no limit)
        all_incidents = await storage.get_recent(limit=10000)  # Reasonable cap for history replay

        incident_count = 0
        override_count = 0

        for incident in all_incidents:
            # Increment incidents_total
            incidents_total.labels(
                matched_pattern=incident.matched_pattern,
                outcome=incident.outcome,
            ).inc()
            incident_count += 1

            # Increment overrides_total if override exists
            if incident.override_status != "none":
                overrides_total.labels(
                    matched_pattern=incident.matched_pattern,
                    override_status=incident.override_status,
                ).inc()
                override_count += 1

        log.info(
            "Prometheus metrics populated: %d incidents, %d overrides",
            incident_count,
            override_count,
        )

    except Exception as exc:
        log.error("Failed to populate Prometheus metrics from storage: %s", exc)
        log.warning("Metrics will start from zero (no historical data loaded)")


def get_metrics_text() -> str:
    """
    Return Prometheus metrics in text exposition format.

    This is what Prometheus scrapes at GET /metrics.
    """
    return generate_latest().decode("utf-8")


def get_metrics_content_type() -> str:
    """Return the Content-Type for Prometheus metrics."""
    return CONTENT_TYPE_LATEST

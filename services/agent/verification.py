"""
Verification Engine for Aether-Guard

Validates that remediation actions actually improved metrics.
Implements automatic rollback if SLO breach persists post-action.

This is CRITICAL for production safety — never trust that an action worked.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# Configuration
TARGET_CONTAINER = os.getenv("TARGET_CONTAINER", "target-service")


@dataclass
class MetricsSnapshot:
    """Prometheus metrics at a point in time."""
    timestamp: datetime
    error_rate_5m: Optional[float]
    latency_p99_5m: Optional[float]
    latency_p50_5m: Optional[float]
    request_rate_5m: Optional[float]
    memory_usage_bytes: Optional[float]
    cpu_usage_percent: Optional[float]

    def __repr__(self) -> str:
        return (
            f"MetricsSnapshot(error_rate={self.error_rate_5m:.2%}, "
            f"p99={self.latency_p99_5m:.3f}s, "
            f"rps={self.request_rate_5m:.1f})"
        )


@dataclass
class VerificationResult:
    """Outcome of post-remediation verification."""
    success: bool
    improved: bool  # Did metrics improve?
    metrics_before: MetricsSnapshot
    metrics_after: MetricsSnapshot
    reason: str
    should_rollback: bool  # Should we undo the action?


class VerificationEngine:
    """
    Validates remediation outcomes by comparing metrics before/after.

    Usage:
        verifier = VerificationEngine(prometheus_url="http://localhost:9090")

        # Before remediation
        before = await verifier.capture_snapshot()

        # Execute remediation
        execute_restart(container)

        # Wait for system to stabilize
        await asyncio.sleep(120)

        # Verify outcome
        result = await verifier.verify(before, alert_type="SLOErrorBudgetBurn")
        if result.should_rollback:
            execute_rollback(container)
    """

    # SLO thresholds (from prometheus/rules/slo_alerts.yml)
    _SLO_ERROR_RATE_THRESHOLD = 0.001  # 0.1% error budget
    _SLO_LATENCY_P99_THRESHOLD = 0.5   # 500ms

    # Improvement thresholds (how much better must metrics get?)
    _MIN_ERROR_RATE_IMPROVEMENT = 0.5   # Error rate must drop by ≥50%
    _MIN_LATENCY_IMPROVEMENT = 0.3      # Latency must drop by ≥30%

    def __init__(self, prometheus_url: str = "http://prometheus:9090"):
        self.prometheus_url = prometheus_url
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=10.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    async def capture_snapshot(self) -> MetricsSnapshot:
        """
        Query Prometheus for current metrics.

        Returns:
            MetricsSnapshot with current values (None if query fails)
        """
        if not self._client:
            self._client = httpx.AsyncClient(timeout=10.0)

        metrics = {
            "error_rate_5m": "job:aether_guard_error_ratio:rate5m",
            "latency_p99_5m": "histogram_quantile(0.99, rate(aether_guard_request_duration_seconds_bucket[5m]))",
            "latency_p50_5m": "histogram_quantile(0.50, rate(aether_guard_request_duration_seconds_bucket[5m]))",
            "request_rate_5m": "rate(aether_guard_http_requests_total[5m])",
            "memory_usage_bytes": f"container_memory_usage_bytes{{name='{TARGET_CONTAINER}'}}",
            "cpu_usage_percent": f"rate(container_cpu_usage_seconds_total{{name='{TARGET_CONTAINER}'}}[5m]) * 100",
        }

        values = {}
        for key, query in metrics.items():
            try:
                resp = await self._client.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params={"query": query},
                )
                resp.raise_for_status()
                result = resp.json().get("data", {}).get("result", [])
                values[key] = float(result[0]["value"][1]) if result else None
            except Exception as exc:
                log.warning("Prometheus query failed [%s]: %s", key, exc)
                values[key] = None

        return MetricsSnapshot(timestamp=datetime.now(), **values)

    async def verify(
        self,
        metrics_before: MetricsSnapshot,
        alert_type: str,
        wait_seconds: int = 120,
    ) -> VerificationResult:
        """
        Verify that remediation improved metrics.

        Args:
            metrics_before: Snapshot captured before remediation
            alert_type: Type of alert that triggered remediation (e.g., "SLOErrorBudgetBurn")
            wait_seconds: How long to wait before checking (default 2 minutes)

        Returns:
            VerificationResult with success/failure and rollback recommendation

        Logic:
            1. Wait for system to stabilize (default 2 min)
            2. Capture new metrics snapshot
            3. Compare before/after:
               - Did error rate drop significantly?
               - Did latency improve?
               - Are we back within SLO?
            4. If NO improvement → recommend rollback
        """
        log.info("Waiting %ds for metrics to stabilize post-remediation...", wait_seconds)
        await asyncio.sleep(wait_seconds)

        metrics_after = await self.capture_snapshot()

        # Analyze improvement based on alert type
        if "Error" in alert_type or "Availability" in alert_type:
            return self._verify_error_rate_improvement(metrics_before, metrics_after)
        elif "Latency" in alert_type:
            return self._verify_latency_improvement(metrics_before, metrics_after)
        else:
            # Generic verification: check all metrics
            return self._verify_generic_improvement(metrics_before, metrics_after)

    def _verify_error_rate_improvement(
        self,
        before: MetricsSnapshot,
        after: MetricsSnapshot,
    ) -> VerificationResult:
        """Verify error rate dropped after remediation."""
        # Handle missing data
        if before.error_rate_5m is None or after.error_rate_5m is None:
            return VerificationResult(
                success=False,
                improved=False,
                metrics_before=before,
                metrics_after=after,
                reason="Cannot verify: missing error rate data",
                should_rollback=False,  # Don't rollback on data issues
            )

        # Calculate improvement
        if before.error_rate_5m == 0:
            # Edge case: error rate was already 0 (shouldn't trigger alert, but handle gracefully)
            if after.error_rate_5m == 0:
                improved = True
                reason = "Error rate remained at 0% (no errors)"
            else:
                improved = False
                reason = f"Error rate increased from 0% to {after.error_rate_5m:.2%}"
        else:
            improvement_ratio = (before.error_rate_5m - after.error_rate_5m) / before.error_rate_5m
            improved = improvement_ratio >= self._MIN_ERROR_RATE_IMPROVEMENT

            if improved:
                reason = (
                    f"Error rate improved by {improvement_ratio:.1%} "
                    f"({before.error_rate_5m:.2%} → {after.error_rate_5m:.2%})"
                )
            else:
                reason = (
                    f"Error rate did not improve sufficiently "
                    f"({before.error_rate_5m:.2%} → {after.error_rate_5m:.2%}, "
                    f"needed ≥{self._MIN_ERROR_RATE_IMPROVEMENT:.0%} improvement)"
                )

        # Check if back within SLO
        within_slo = after.error_rate_5m <= self._SLO_ERROR_RATE_THRESHOLD
        if within_slo:
            reason += " — now within SLO"

        # Rollback decision: if error rate got worse or didn't improve
        should_rollback = not improved or after.error_rate_5m > before.error_rate_5m

        return VerificationResult(
            success=improved and within_slo,
            improved=improved,
            metrics_before=before,
            metrics_after=after,
            reason=reason,
            should_rollback=should_rollback,
        )

    def _verify_latency_improvement(
        self,
        before: MetricsSnapshot,
        after: MetricsSnapshot,
    ) -> VerificationResult:
        """Verify latency improved after remediation."""
        if before.latency_p99_5m is None or after.latency_p99_5m is None:
            return VerificationResult(
                success=False,
                improved=False,
                metrics_before=before,
                metrics_after=after,
                reason="Cannot verify: missing latency data",
                should_rollback=False,
            )

        if before.latency_p99_5m == 0:
            improved = after.latency_p99_5m <= self._SLO_LATENCY_P99_THRESHOLD
            reason = f"P99 latency now {after.latency_p99_5m:.3f}s"
        else:
            improvement_ratio = (before.latency_p99_5m - after.latency_p99_5m) / before.latency_p99_5m
            improved = improvement_ratio >= self._MIN_LATENCY_IMPROVEMENT

            reason = (
                f"P99 latency changed by {improvement_ratio:+.1%} "
                f"({before.latency_p99_5m:.3f}s → {after.latency_p99_5m:.3f}s)"
            )

        within_slo = after.latency_p99_5m <= self._SLO_LATENCY_P99_THRESHOLD
        if within_slo:
            reason += " — now within SLO"

        should_rollback = not improved or after.latency_p99_5m > before.latency_p99_5m

        return VerificationResult(
            success=improved and within_slo,
            improved=improved,
            metrics_before=before,
            metrics_after=after,
            reason=reason,
            should_rollback=should_rollback,
        )

    def _verify_generic_improvement(
        self,
        before: MetricsSnapshot,
        after: MetricsSnapshot,
    ) -> VerificationResult:
        """
        Generic verification: check if any key metric improved.

        Used for unknown alert types or multi-dimensional failures.
        """
        improvements = []
        regressions = []

        # Check error rate
        if before.error_rate_5m is not None and after.error_rate_5m is not None:
            if after.error_rate_5m < before.error_rate_5m * (1 - self._MIN_ERROR_RATE_IMPROVEMENT):
                improvements.append("error_rate")
            elif after.error_rate_5m > before.error_rate_5m * 1.1:  # 10% worse
                regressions.append("error_rate")

        # Check latency
        if before.latency_p99_5m is not None and after.latency_p99_5m is not None:
            if after.latency_p99_5m < before.latency_p99_5m * (1 - self._MIN_LATENCY_IMPROVEMENT):
                improvements.append("latency_p99")
            elif after.latency_p99_5m > before.latency_p99_5m * 1.2:  # 20% worse
                regressions.append("latency_p99")

        # Decision logic
        if regressions:
            return VerificationResult(
                success=False,
                improved=False,
                metrics_before=before,
                metrics_after=after,
                reason=f"Metrics regressed: {', '.join(regressions)}",
                should_rollback=True,
            )
        elif improvements:
            return VerificationResult(
                success=True,
                improved=True,
                metrics_before=before,
                metrics_after=after,
                reason=f"Metrics improved: {', '.join(improvements)}",
                should_rollback=False,
            )
        else:
            return VerificationResult(
                success=False,
                improved=False,
                metrics_before=before,
                metrics_after=after,
                reason="No significant metric changes detected",
                should_rollback=False,  # Inconclusive, don't rollback
            )

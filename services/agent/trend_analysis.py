"""
Aether-Guard — Time-Series Trend Analysis Utility

Provides reusable trend detection for distinguishing resource leaks from
load-driven increases. Used by RCA patterns to classify metrics as "rising"
(continuous growth) vs. "stable" (plateau after spike).

Design rationale:
- Real leaks show sustained positive slope over time (no plateau)
- Load-driven increases rise then stabilize once capacity is reached
- Linear regression over sliding window provides simple, robust classification

This solves the static-threshold problem: a metric can be high but stable
(legitimate load) vs. continuously growing (leak).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger("aether-guard.agent.trend_analysis")

# ── Configuration ─────────────────────────────────────────────────────────────

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")


# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class TrendAnalysis:
    """Result of time-series trend analysis."""

    is_rising: bool  # True if metric shows sustained growth
    slope: float  # Rate of change (units per second)
    confidence: float  # 0.0-1.0 (based on data quality and R²)
    data_points: int  # Number of samples analyzed
    window_duration_sec: float  # Time window covered
    start_value: float | None  # First value in window
    end_value: float | None  # Last value in window


# ── Trend Classification ──────────────────────────────────────────────────────


class TrendClassifier:
    """
    Detects if a metric is rising vs. stable over a time window.

    Uses linear regression over recent Prometheus samples to classify:
    - RISING: Positive slope above threshold, sustained over window
    - STABLE: Slope near zero or negative (not growing)

    This solves the "leak vs. load" problem: a real leak shows continuous
    growth; load-driven increases plateau once capacity is reached.
    """

    def __init__(self, prometheus_url: str | None = None):
        """
        Initialize trend classifier.

        Args:
            prometheus_url: Prometheus base URL (defaults to PROMETHEUS_URL env var)
        """
        self.prometheus_url = prometheus_url or PROMETHEUS_URL

    async def analyze_metric(
        self,
        metric_expr: str,
        slope_threshold: float,
        window_minutes: int = 10,
        step_seconds: int = 30,
    ) -> TrendAnalysis:
        """
        Fetch metric history from Prometheus and classify trend.

        Args:
            metric_expr: PromQL query to evaluate (e.g., "go_goroutines{job='target-service'}")
            slope_threshold: Minimum slope to classify as "rising" (units/second)
            window_minutes: Time window to analyze (default: 10 min)
            step_seconds: Sample interval (default: 30s, aligned with recording rules)

        Returns:
            TrendAnalysis with is_rising classification and slope calculation

        Algorithm:
            1. Fetch metric history using Prometheus query_range API
            2. Perform linear regression to compute slope
            3. Calculate R² (goodness of fit) for confidence
            4. Classify as "rising" if slope > threshold AND confidence > 0.7
        """
        import time

        # Calculate time range
        end_time = int(time.time())
        start_time = end_time - (window_minutes * 60)
        window_duration_sec = window_minutes * 60

        # Fetch metric history from Prometheus
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.prometheus_url}/api/v1/query_range",
                    params={
                        "query": metric_expr,
                        "start": start_time,
                        "end": end_time,
                        "step": step_seconds,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            # Extract time series data
            results = data.get("data", {}).get("result", [])
            if not results:
                log.warning("No data returned for metric '%s'", metric_expr)
                return TrendAnalysis(
                    is_rising=False,
                    slope=0.0,
                    confidence=0.0,
                    data_points=0,
                    window_duration_sec=window_duration_sec,
                    start_value=None,
                    end_value=None,
                )

            # Use first time series (most relevant for single-metric queries)
            values = results[0].get("values", [])
            if len(values) < 3:  # Need minimum 3 points for meaningful regression
                log.warning("Insufficient data points (%d) for trend analysis", len(values))
                return TrendAnalysis(
                    is_rising=False,
                    slope=0.0,
                    confidence=0.0,
                    data_points=len(values),
                    window_duration_sec=window_duration_sec,
                    start_value=None,
                    end_value=None,
                )

            # Parse timestamps and values
            timestamps = [float(v[0]) for v in values]
            metric_values = [float(v[1]) for v in values]

            # Perform linear regression
            slope, r_squared = self._linear_regression(timestamps, metric_values)

            # Confidence based on R² (how well data fits linear trend)
            # R² = 1.0 means perfect linear fit, R² = 0.0 means no correlation
            confidence = min(1.0, max(0.0, r_squared))

            # Classify as "rising" if:
            # 1. Slope exceeds threshold (growth rate)
            # 2. Confidence is high enough (R² > 0.7 = reasonably linear trend)
            is_rising = slope > slope_threshold and confidence > 0.7

            log.debug(
                "Trend analysis: slope=%.4f, R²=%.3f, rising=%s (threshold=%.4f)",
                slope,
                r_squared,
                is_rising,
                slope_threshold,
            )

            return TrendAnalysis(
                is_rising=is_rising,
                slope=slope,
                confidence=confidence,
                data_points=len(values),
                window_duration_sec=window_duration_sec,
                start_value=metric_values[0] if metric_values else None,
                end_value=metric_values[-1] if metric_values else None,
            )

        except Exception as exc:
            log.error("Trend analysis failed for '%s': %s", metric_expr, exc)
            return TrendAnalysis(
                is_rising=False,
                slope=0.0,
                confidence=0.0,
                data_points=0,
                window_duration_sec=window_duration_sec,
                start_value=None,
                end_value=None,
            )

    def _linear_regression(
        self, x_values: list[float], y_values: list[float]
    ) -> tuple[float, float]:
        """
        Perform simple linear regression to compute slope and R².

        Args:
            x_values: Independent variable (timestamps)
            y_values: Dependent variable (metric values)

        Returns:
            (slope, r_squared) tuple
                slope: Rate of change (y units per x unit)
                r_squared: Goodness of fit (0.0 = no fit, 1.0 = perfect fit)
        """
        n = len(x_values)
        if n < 2:
            return 0.0, 0.0

        # Calculate means
        mean_x = sum(x_values) / n
        mean_y = sum(y_values) / n

        # Calculate slope (β₁) using least squares formula
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_values, y_values))
        denominator = sum((x - mean_x) ** 2 for x in x_values)

        if denominator == 0:  # All x values are identical (shouldn't happen with timestamps)
            return 0.0, 0.0

        slope = numerator / denominator

        # Calculate R² (coefficient of determination)
        # R² = 1 - (SS_res / SS_tot)
        # SS_res = sum of squared residuals (predicted - actual)²
        # SS_tot = total sum of squares (actual - mean)²
        y_predicted = [slope * (x - mean_x) + mean_y for x in x_values]
        ss_res = sum((y_actual - y_pred) ** 2 for y_actual, y_pred in zip(y_values, y_predicted))
        ss_tot = sum((y - mean_y) ** 2 for y in y_values)

        if ss_tot == 0:  # All y values are identical (flat line)
            r_squared = 1.0 if ss_res == 0 else 0.0
        else:
            r_squared = 1.0 - (ss_res / ss_tot)

        return slope, r_squared
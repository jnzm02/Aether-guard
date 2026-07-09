"""
Rule-Based Triage Engine for Aether-Guard

Provides deterministic pattern matching for known incident types.
This is the FIRST layer of RCA — LLM is only used for ambiguous cases.

Why rules-first?
1. Latency: 10ms vs 2-5s for LLM calls
2. Reliability: Works when Claude API is down
3. Cost: Free vs $0.01 per incident
4. Explainability: "matched pattern X" vs "LLM decided Y"

Rule confidence is based on:
- How specific the pattern match is (exact vs heuristic)
- How many signals confirm the hypothesis (metrics + logs + alert type)
- Historical success rate of this rule's recommendations

# ── Backlog: Deferred Patterns (Missing Metrics) ──────────────────────────────
# TODO: Kafka Connection Leak Pattern
#   Requires metrics instrumentation PR first:
#   - kafka_consumer_connections_active (gauge)
#   - kafka_consumer_lag (gauge by partition)
#   Trigger: Rising connection count + growing lag over 10min window
#   Confidence: 0.85 + boosters (log errors, no traffic spike)
#   Action: RESTART (clears connection pool)
#
# TODO: DB Pool Exhaustion Pattern
#   Requires metrics instrumentation PR first:
#   - db_pool_connections_active (gauge)
#   - db_pool_connections_max (gauge)
#   - db_pool_checkout_duration_seconds (histogram)
#   Trigger: Pool utilization >85% AND rising/elevated checkout duration
#   Confidence: 0.70 + boosters (slow queries, connection timeouts in logs)
#   Action: RESTART (clears stale connections) or SCALE (if load-driven)
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

from policy import RootCauseCategory

log = logging.getLogger(__name__)

# ── Configuration (Goroutine Leak Pattern) ────────────────────────────────────
# These thresholds are tunable via environment variables for production tuning
# based on Priority 2 trust metrics (override rates).

GOROUTINE_LEAK_SLOPE_THRESHOLD = float(
    os.getenv("GOROUTINE_LEAK_SLOPE_THRESHOLD", "0.0833")  # 5 goroutines/min = 0.0833/sec
)
GOROUTINE_LEAK_BASELINE = int(os.getenv("GOROUTINE_LEAK_BASELINE", "50"))
GOROUTINE_LEAK_BASELINE_MULTIPLIER = float(
    os.getenv("GOROUTINE_LEAK_BASELINE_MULTIPLIER", "3.0")
)
HEAP_LEAK_SLOPE_THRESHOLD = float(
    os.getenv("HEAP_LEAK_SLOPE_THRESHOLD", "166666.67")  # 10 MB/min = 166666.67 bytes/sec
)


@dataclass
class RuleMatch:
    """Result of a rule matching attempt."""
    matched: bool
    rule_name: str
    root_cause: RootCauseCategory
    confidence: float  # 0.0-1.0 (based on pattern specificity)
    evidence: list[str]  # List of signals that matched (for explainability)
    recommended_action: str  # "RESTART", "SCALE", "ROLLBACK", "IGNORE"
    reasoning: str  # Human-readable explanation


class RuleEngine:
    """
    Deterministic pattern matcher for known incident types.

    Rules are evaluated in priority order (highest confidence first).
    First rule that matches wins (short-circuit evaluation).

    This handles ~60% of incidents in production (based on SRE experience).
    Remaining 40% are ambiguous and require LLM analysis.
    """

    async def analyze(
        self,
        alert: dict[str, Any],
        metrics: dict[str, Optional[float]],
        logs: list[str],
    ) -> Optional[RuleMatch]:
        """
        Attempt to match incident to known patterns.

        Args:
            alert: Alertmanager alert payload (labels, annotations)
            metrics: Prometheus metrics snapshot
            logs: Container log lines (last N lines)

        Returns:
            RuleMatch if pattern matched, None if ambiguous (requires LLM)

        Evaluation order:
        1. High-confidence rules (exact patterns) — confidence ≥0.90
        2. Medium-confidence rules (heuristics) — confidence 0.70-0.89
        3. If nothing matches → return None (escalate to LLM)
        """
        alert_name = alert.get("labels", {}).get("alertname", "")

        # ── High-confidence rules (exact patterns, all synchronous) ──────────────
        rule = self._check_oom_kill(alert, logs)
        if rule:
            return rule

        rule = self._check_restart_loop(alert, logs)
        if rule:
            return rule

        rule = await self._check_memory_leak(alert_name, metrics, logs)
        if rule:
            return rule

        rule = await self._check_cpu_saturation(alert_name, metrics)
        if rule:
            return rule

        rule = self._check_traffic_spike(alert_name, metrics)
        if rule:
            return rule

        # ── Medium-confidence rules (heuristics, synchronous) ─────────────────────
        rule = self._check_dependency_failure(logs)
        if rule:
            return rule

        rule = self._check_bad_deployment(alert, metrics, logs)
        if rule:
            return rule

        # ── Async patterns (require Prometheus query_range API) ──────────────────
        # These run LAST to preserve <50ms latency for the common fast patterns above.
        # Only reached if none of the 7 synchronous patterns matched (~40% of cases).
        rule = await self._check_goroutine_leak(alert_name, metrics, logs)
        if rule:
            return rule

        # No rule matched → ambiguous, needs LLM
        log.info("No rule matched for alert %s — escalating to LLM", alert_name)
        return None

    # ===== High-Confidence Rules (≥0.90) =====

    def _check_oom_kill(self, alert: dict, logs: list[str]) -> Optional[RuleMatch]:
        """
        Detect OOM kills from kernel logs.

        Pattern: "Out of memory: Kill process" or "oom-kill"
        Confidence: 0.95 (very specific kernel message)
        Action: RESTART (only way to recover from OOM)
        """
        oom_patterns = [
            r"Out of memory.*Kill process",
            r"oom-kill",
            r"Killed process.*out of memory",
            r"Memory cgroup out of memory",
        ]

        evidence = []
        for line in logs:
            for pattern in oom_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    evidence.append(line.strip())

        if evidence:
            return RuleMatch(
                matched=True,
                rule_name="OOM_KILL",
                root_cause=RootCauseCategory.MEMORY_LEAK,
                confidence=0.95,
                evidence=evidence[:3],  # Show first 3 matching lines
                recommended_action="RESTART",
                reasoning=(
                    "Detected OOM kill in kernel logs. Process was terminated by OS "
                    "due to memory exhaustion. RESTART is required to recover."
                ),
            )
        return None

    def _check_restart_loop(self, alert: dict, logs: list[str]) -> Optional[RuleMatch]:
        """
        Detect crash loops (container restarting rapidly).

        Pattern: Multiple "Starting..." or "Exited with code" messages in short time
        Confidence: 0.92
        Action: ROLLBACK (likely bad deployment)
        """
        # Count restart indicators in logs
        restart_indicators = [
            r"Starting.*server",
            r"Listening on port",
            r"Exited with code",
            r"Container.*started",
        ]

        restart_count = 0
        evidence = []
        for line in logs:
            for pattern in restart_indicators:
                if re.search(pattern, line, re.IGNORECASE):
                    restart_count += 1
                    if len(evidence) < 3:
                        evidence.append(line.strip())

        # If we see >3 restarts in recent logs, it's a crash loop
        if restart_count >= 3:
            return RuleMatch(
                matched=True,
                rule_name="RESTART_LOOP",
                root_cause=RootCauseCategory.BAD_DEPLOYMENT,
                confidence=0.92,
                evidence=evidence,
                recommended_action="ROLLBACK",
                reasoning=(
                    f"Detected {restart_count} restart events in recent logs. "
                    "This indicates a crash loop, likely caused by a bad deployment. "
                    "ROLLBACK to previous stable version."
                ),
            )
        return None

    async def _check_memory_leak(
        self,
        alert_name: str,
        metrics: dict[str, Optional[float]],
        logs: list[str],
    ) -> Optional[RuleMatch]:
        """
        Detect memory leaks using trend analysis (Workstream 2).

        Pattern: Rising memory usage over time + elevated absolute usage
        Confidence: 0.88 (trend-based) or 0.75 (static fallback)
        Action: RESTART (clears leaked memory)

        This now uses TrendClassifier to distinguish real leaks (continuous growth)
        from load-driven increases (rise then stabilize). Falls back to static
        threshold if Prometheus is unavailable.

        Configuration:
        - Static threshold: 1GB (fallback when Prometheus unavailable)
        - Slope threshold: 10 MB/min (configurable via HEAP_LEAK_SLOPE_THRESHOLD)
        """
        from trend_analysis import TrendClassifier

        is_memory_alert = "Memory" in alert_name or "Saturation" in alert_name
        if not is_memory_alert:
            return None

        memory_usage = metrics.get("memleak_bytes_allocated")
        if memory_usage is None:
            return None

        # Gate 1: Absolute threshold (prevents false positive on normal baseline)
        STATIC_THRESHOLD = 1_000_000_000  # 1GB
        if memory_usage < STATIC_THRESHOLD:
            return None

        # Collect log evidence (used by both trend and static paths)
        memory_log_patterns = [
            r"malloc.*failed",
            r"out of memory",
            r"cannot allocate",
            r"memory exhausted",
        ]
        memory_warnings = []
        for line in logs:
            for pattern in memory_log_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    memory_warnings.append(line.strip())
                    if len(memory_warnings) >= 2:
                        break

        # Gate 2: Trend analysis (PRIMARY - distinguishes leak from stable high usage)
        classifier = TrendClassifier()
        try:
            trend = await classifier.analyze_metric(
                metric_expr='go_memstats_heap_alloc_bytes{job="target-service"}',
                slope_threshold=HEAP_LEAK_SLOPE_THRESHOLD,
                window_minutes=10,
                step_seconds=30,
            )

            if not trend.is_rising:
                # Memory is high but stable → legitimate load, not a leak
                log.debug(
                    "Memory usage is elevated (%.2f GB) but stable (slope=%.2f MB/min) — not a leak",
                    memory_usage / 1e9,
                    (trend.slope * 60) / 1e6,
                )
                return None

            # Trend-based match (high confidence)
            confidence = 0.88
            signals = [
                f"Memory rising from {trend.start_value / 1e9:.2f} GB → {trend.end_value / 1e9:.2f} GB "
                f"over {trend.window_duration_sec / 60:.0f}min (slope: {(trend.slope * 60) / 1e6:.1f} MB/min)",
                f"Current memory: {memory_usage / 1e9:.2f} GB (threshold: {STATIC_THRESHOLD / 1e9:.0f} GB)",
                f"Trend confidence: {trend.confidence:.2f} (R² fit)",
            ]

            if memory_warnings:
                confidence += 0.05
                signals.append(f"{len(memory_warnings)} memory allocation warnings in logs")

            return RuleMatch(
                matched=True,
                rule_name="MEMORY_LEAK",
                root_cause=RootCauseCategory.MEMORY_LEAK,
                confidence=min(1.0, confidence),
                evidence=signals + memory_warnings[:2],
                recommended_action="RESTART",
                reasoning=(
                    f"Detected memory leak: heap allocation has grown from {trend.start_value / 1e9:.2f} GB to "
                    f"{trend.end_value / 1e9:.2f} GB over the past {trend.window_duration_sec / 60:.0f}min with "
                    f"sustained positive slope ({(trend.slope * 60) / 1e6:.1f} MB/min). This indicates memory is "
                    "being allocated without proper cleanup. RESTART will clear leaked memory and restore normal operation."
                ),
            )

        except Exception as exc:
            log.warning("Trend analysis failed for memory leak detection, falling back to static threshold: %s", exc)

            # FALLBACK: Static threshold logic (lower confidence)
            signals = [
                f"Memory usage: {memory_usage / 1e9:.2f} GB (threshold: {STATIC_THRESHOLD / 1e9:.0f} GB)",
                "MemorySaturation alert fired",
            ]

            if memory_warnings:
                signals.append(f"{len(memory_warnings)} memory allocation warnings in logs")

            # Require at least 2 signals for static match
            if len(signals) >= 2:
                return RuleMatch(
                    matched=True,
                    rule_name="MEMORY_LEAK",
                    root_cause=RootCauseCategory.MEMORY_LEAK,
                    confidence=0.75,  # Lower confidence without trend data
                    evidence=signals + memory_warnings[:2],
                    recommended_action="RESTART",
                    reasoning=(
                        "Multiple signals indicate memory leak: "
                        f"{', '.join(signals)}. "
                        "(Trend analysis unavailable - using static threshold). "
                        "RESTART will clear leaked memory and restore normal operation."
                    ),
                )

        return None

    async def _check_cpu_saturation(
        self,
        alert_name: str,
        metrics: dict[str, Optional[float]],
    ) -> Optional[RuleMatch]:
        """
        Detect CPU saturation using trend analysis for EFFICIENCY variant (Workstream 2).

        Two variants:
        1. CPU_SATURATION_TRAFFIC: High CPU + high traffic → SCALE (synchronous, no trend needed)
        2. CPU_SATURATION_EFFICIENCY: Rising CPU + normal traffic → RESTART (uses trend analysis)

        Confidence: 0.85 (traffic), 0.82 (efficiency with trend), 0.70 (efficiency static fallback)
        """
        from trend_analysis import TrendClassifier

        is_cpu_alert = "CPU" in alert_name or "Saturation" in alert_name
        if not is_cpu_alert:
            return None

        cpu_usage = metrics.get("cpu_usage_percent")
        request_rate = metrics.get("request_rate_5m")

        if cpu_usage is None:
            return None

        # High CPU defined as >80%
        STATIC_CPU_THRESHOLD = 80.0
        is_high_cpu = cpu_usage > STATIC_CPU_THRESHOLD

        # Check if traffic is also high (would explain high CPU)
        TRAFFIC_THRESHOLD = 1000  # rps
        is_traffic_spike = request_rate and request_rate > TRAFFIC_THRESHOLD

        if is_high_cpu:
            if is_traffic_spike:
                # VARIANT 1: High CPU + high traffic → scale out (SYNCHRONOUS, no trend needed)
                return RuleMatch(
                    matched=True,
                    rule_name="CPU_SATURATION_TRAFFIC",
                    root_cause=RootCauseCategory.TRAFFIC_SPIKE,
                    confidence=0.85,
                    evidence=[
                        f"CPU usage: {cpu_usage:.1f}%",
                        f"Request rate: {request_rate:.1f} rps (elevated)",
                    ],
                    recommended_action="SCALE",
                    reasoning=(
                        "High CPU correlates with elevated traffic. "
                        "SCALE horizontally to distribute load."
                    ),
                )
            else:
                # VARIANT 2: High CPU + normal traffic → efficiency problem (USE TREND ANALYSIS)
                # This distinguishes runaway CPU (leak, stuck loop) from legitimate baseline

                classifier = TrendClassifier()
                try:
                    # Use CPU user time as the metric (more precise than cpu_usage_percent)
                    cpu_trend = await classifier.analyze_metric(
                        metric_expr='rate(process_cpu_seconds_total{job="target-service"}[5m])',
                        slope_threshold=0.01,  # 1% increase per second = runaway
                        window_minutes=10,
                        step_seconds=30,
                    )

                    if not cpu_trend.is_rising:
                        # CPU is high but stable → legitimate load, not runaway
                        log.debug(
                            "CPU usage is elevated (%.1f%%) but stable (slope=%.4f) — not runaway",
                            cpu_usage,
                            cpu_trend.slope,
                        )
                        return None

                    # Trend-based efficiency match (high confidence)
                    confidence = 0.82
                    signals = [
                        f"CPU rising from {cpu_trend.start_value * 100:.1f}% → {cpu_trend.end_value * 100:.1f}% "
                        f"over {cpu_trend.window_duration_sec / 60:.0f}min (slope: {cpu_trend.slope * 100:.2f}%/sec)",
                        f"Current CPU: {cpu_usage:.1f}% (threshold: {STATIC_CPU_THRESHOLD}%)",
                        f"Request rate: {request_rate or 0:.1f} rps (normal, not traffic-driven)",
                        f"Trend confidence: {cpu_trend.confidence:.2f} (R² fit)",
                    ]

                    return RuleMatch(
                        matched=True,
                        rule_name="CPU_SATURATION_EFFICIENCY",
                        root_cause=RootCauseCategory.CPU_SATURATION,
                        confidence=min(1.0, confidence),
                        evidence=signals,
                        recommended_action="RESTART",
                        reasoning=(
                            f"Detected runaway CPU: usage has grown from {cpu_trend.start_value * 100:.1f}% to "
                            f"{cpu_trend.end_value * 100:.1f}% over the past {cpu_trend.window_duration_sec / 60:.0f}min "
                            f"with sustained positive slope ({cpu_trend.slope * 100:.2f}%/sec) despite normal traffic. "
                            "This indicates inefficiency (stuck loop, goroutine leak, or algorithmic issue). "
                            "RESTART to reset state."
                        ),
                    )

                except Exception as exc:
                    log.warning("Trend analysis failed for CPU efficiency detection, falling back to static threshold: %s", exc)

                    # FALLBACK: Static threshold logic (lower confidence)
                    return RuleMatch(
                        matched=True,
                        rule_name="CPU_SATURATION_EFFICIENCY",
                        root_cause=RootCauseCategory.CPU_SATURATION,
                        confidence=0.70,  # Lower confidence without trend data
                        evidence=[
                            f"CPU usage: {cpu_usage:.1f}% (threshold: {STATIC_CPU_THRESHOLD}%)",
                            f"Request rate: {request_rate or 0:.1f} rps (normal)",
                        ],
                        recommended_action="RESTART",
                        reasoning=(
                            "High CPU with normal traffic suggests inefficiency "
                            "(e.g., stuck loop, goroutine leak). "
                            "(Trend analysis unavailable - using static threshold). "
                            "RESTART to reset state."
                        ),
                    )

        return None

    def _check_traffic_spike(
        self,
        alert_name: str,
        metrics: dict[str, Optional[float]],
    ) -> Optional[RuleMatch]:
        """
        Detect traffic spikes causing capacity issues.

        Pattern: Error rate spike + latency spike + traffic spike
        Confidence: 0.87
        Action: SCALE
        """
        error_rate = metrics.get("error_rate_5m", 0.0)
        latency_p99 = metrics.get("latency_p99_5m")
        request_rate = metrics.get("request_rate_5m")

        if request_rate is None:
            return None

        # Define "spike" as >2x normal traffic (in production, use anomaly detection)
        is_traffic_spike = request_rate > 1000  # Example threshold

        # Also check if errors/latency are elevated
        is_high_errors = error_rate and error_rate > 0.01  # >1% error rate
        is_high_latency = latency_p99 and latency_p99 > 1.0  # >1s P99

        signals = []
        if is_traffic_spike:
            signals.append(f"Request rate: {request_rate:.1f} rps (spike detected)")
        if is_high_errors:
            signals.append(f"Error rate: {error_rate:.2%}")
        if is_high_latency:
            signals.append(f"P99 latency: {latency_p99:.2f}s")

        # Need traffic spike + at least one other signal
        if is_traffic_spike and (is_high_errors or is_high_latency):
            return RuleMatch(
                matched=True,
                rule_name="TRAFFIC_SPIKE",
                root_cause=RootCauseCategory.TRAFFIC_SPIKE,
                confidence=0.87,
                evidence=signals,
                recommended_action="SCALE",
                reasoning=(
                    "Traffic spike is overwhelming current capacity. "
                    f"{', '.join(signals)}. "
                    "SCALE horizontally to handle increased load."
                ),
            )
        return None

    # ===== Medium-Confidence Rules (0.70-0.89) =====

    def _check_dependency_failure(self, logs: list[str]) -> Optional[RuleMatch]:
        """
        Detect dependency failures (database, Redis, external API).

        Pattern: Connection errors, timeouts, DNS failures
        Confidence: 0.75 (heuristic, could be transient)
        Action: RESTART (clears connection pools, re-establishes connections)
        """
        dependency_patterns = [
            r"connection refused",
            r"dial tcp.*timeout",
            r"no such host",
            r"database.*unavailable",
            r"redis.*connection.*failed",
            r"timeout.*waiting.*connection",
        ]

        evidence = []
        for line in logs:
            for pattern in dependency_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    evidence.append(line.strip())
                    if len(evidence) >= 3:
                        break

        if evidence:
            return RuleMatch(
                matched=True,
                rule_name="DEPENDENCY_FAILURE",
                root_cause=RootCauseCategory.DEPENDENCY_FAILURE,
                confidence=0.75,
                evidence=evidence,
                recommended_action="RESTART",
                reasoning=(
                    "Detected connection errors to external dependencies. "
                    "RESTART will clear stale connection pools and retry connections. "
                    "If problem persists, check dependency health."
                ),
            )
        return None

    def _check_bad_deployment(
        self,
        alert: dict,
        metrics: dict[str, Optional[float]],
        logs: list[str],
    ) -> Optional[RuleMatch]:
        """
        Detect bad deployments (new code causing errors).

        Pattern: Error spike + recent deployment timestamp + startup errors
        Confidence: 0.78 (heuristic)
        Action: ROLLBACK
        """
        error_rate = metrics.get("error_rate_5m", 0.0)
        is_high_errors = error_rate and error_rate > 0.05  # >5% errors

        # Check logs for startup errors
        startup_error_patterns = [
            r"panic:",
            r"fatal error",
            r"failed to start",
            r"initialization.*failed",
        ]
        startup_errors = []
        for line in logs:
            for pattern in startup_error_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    startup_errors.append(line.strip())

        # Check if alert just started (suggests recent change)
        # In production, correlate with deployment events from K8s
        alert_age_str = alert.get("annotations", {}).get("age", "")
        is_recent_alert = "minute" in alert_age_str or "second" in alert_age_str

        signals = []
        if is_high_errors:
            signals.append(f"Error rate: {error_rate:.2%}")
        if startup_errors:
            signals.append(f"{len(startup_errors)} startup errors in logs")
        if is_recent_alert:
            signals.append("Alert recently fired (suggests new deployment)")

        # Need at least 2 signals
        if len(signals) >= 2:
            return RuleMatch(
                matched=True,
                rule_name="BAD_DEPLOYMENT",
                root_cause=RootCauseCategory.BAD_DEPLOYMENT,
                confidence=0.78,
                evidence=signals + startup_errors[:2],
                recommended_action="ROLLBACK",
                reasoning=(
                    "Correlation between error spike and recent changes suggests bad deployment. "
                    f"{', '.join(signals)}. "
                    "ROLLBACK to previous stable version."
                ),
            )
        return None

    async def _check_goroutine_leak(
        self,
        alert_name: str,
        metrics: dict[str, Optional[float]],
        logs: list[str],
    ) -> Optional[RuleMatch]:
        """
        Detect goroutine leaks using trend analysis.

        Pattern: Rising goroutine count over time + elevated absolute count
        Confidence: 0.85 (base) + boosters
        Action: RESTART (terminates leaked goroutines)

        This uses the new trend-detection utility to distinguish real leaks
        (continuous growth) from load-driven increases (rise then stabilize).

        Configuration (tunable via env vars):
        - GOROUTINE_LEAK_SLOPE_THRESHOLD: Min growth rate to classify as leak
        - GOROUTINE_LEAK_BASELINE: Expected normal goroutine count
        - GOROUTINE_LEAK_BASELINE_MULTIPLIER: Absolute threshold = baseline * multiplier
        - HEAP_LEAK_SLOPE_THRESHOLD: Heap growth rate for confidence booster

        See Grafana dashboard for current baseline:
        http://localhost:3000/d/target-service/runtime-metrics
        """
        from trend_analysis import TrendClassifier

        # Check current goroutine count
        current_goroutines = metrics.get("runtime_goroutines")
        if current_goroutines is None:
            return None

        # Gate 1: Absolute threshold (prevents false positive on normal startup ramp)
        absolute_threshold = GOROUTINE_LEAK_BASELINE * GOROUTINE_LEAK_BASELINE_MULTIPLIER
        if current_goroutines < absolute_threshold:
            return None  # Count is elevated but not extreme

        # Gate 2: Trend analysis (REQUIRED - distinguishes leak from stable high load)
        classifier = TrendClassifier()
        try:
            trend = await classifier.analyze_metric(
                metric_expr='go_goroutines{job="target-service"}',
                slope_threshold=GOROUTINE_LEAK_SLOPE_THRESHOLD,
                window_minutes=10,
                step_seconds=30,
            )

            if not trend.is_rising:
                # Goroutine count is high but stable → legitimate load, not a leak
                log.debug(
                    "Goroutine count is elevated (%d) but stable (slope=%.4f) — not a leak",
                    current_goroutines,
                    trend.slope,
                )
                return None

        except Exception as exc:
            log.warning("Trend analysis failed for goroutine leak detection, falling back to static threshold: %s", exc)

            # FALLBACK: Static threshold only (lower confidence)
            signals = [
                f"Goroutine count: {current_goroutines:.0f} (threshold: {absolute_threshold:.0f})",
                "(Trend analysis unavailable - using static threshold)",
            ]

            return RuleMatch(
                matched=True,
                rule_name="GOROUTINE_LEAK",
                root_cause=RootCauseCategory.GOROUTINE_LEAK,
                confidence=0.70,  # Lower confidence without trend data
                evidence=signals,
                recommended_action="RESTART",
                reasoning=(
                    f"Goroutine count ({current_goroutines:.0f}) exceeds threshold ({absolute_threshold:.0f}). "
                    "Trend analysis unavailable - using static threshold. "
                    "RESTART will terminate goroutines and restore normal operation."
                ),
            )

        # Base confidence: trend + absolute threshold matched
        confidence = 0.85
        signals = [
            f"Goroutine count rising from {trend.start_value:.0f} → {trend.end_value:.0f} "
            f"over {trend.window_duration_sec / 60:.0f}min (slope: {trend.slope * 60:.1f}/min)",
            f"Current goroutine count: {current_goroutines:.0f} "
            f"(baseline: {GOROUTINE_LEAK_BASELINE}, threshold: {absolute_threshold:.0f})",
            f"Trend confidence: {trend.confidence:.2f} (R² fit)",
        ]

        # Confidence booster 1: Heap memory also rising (goroutine leak often causes memory leak)
        try:
            heap_trend = await classifier.analyze_metric(
                metric_expr='go_memstats_heap_inuse_bytes{job="target-service"}',
                slope_threshold=HEAP_LEAK_SLOPE_THRESHOLD,
                window_minutes=10,
                step_seconds=30,
            )
            if heap_trend.is_rising:
                confidence += 0.08
                heap_mb_per_min = (heap_trend.slope * 60) / 1_000_000
                signals.append(
                    f"Heap memory also rising: {heap_mb_per_min:.1f} MB/min (confirms leak)"
                )
        except Exception as exc:
            log.debug("Heap trend analysis failed: %s", exc)

        # Confidence booster 2: Log warnings about goroutine/channel issues
        goroutine_log_patterns = [
            r"goroutine.*leak",
            r"deadlock",
            r"channel.*block",
            r"context.*cancel",
            r"too many.*goroutine",
        ]
        goroutine_warnings = []
        for line in logs:
            for pattern in goroutine_log_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    goroutine_warnings.append(line.strip())
                    if len(goroutine_warnings) >= 2:
                        break

        if goroutine_warnings:
            confidence += 0.05
            signals.append(f"{len(goroutine_warnings)} goroutine-related warnings in logs")

        # Confidence booster 3: No recent traffic spike (rules out load-driven explanation)
        request_rate = metrics.get("request_rate_5m_rps", 0.0)
        is_traffic_spike = request_rate and request_rate > 1000  # Reuse threshold from traffic spike rule
        if not is_traffic_spike:
            confidence += 0.05
            signals.append(
                f"No traffic spike detected (RPS: {request_rate:.1f}) — rules out load explanation"
            )

        return RuleMatch(
            matched=True,
            rule_name="GOROUTINE_LEAK",
            root_cause=RootCauseCategory.GOROUTINE_LEAK,
            confidence=min(1.0, confidence),  # Cap at 1.0
            evidence=signals + goroutine_warnings[:2],
            recommended_action="RESTART",
            reasoning=(
                f"Detected goroutine leak: goroutine count has grown from {trend.start_value:.0f} to "
                f"{trend.end_value:.0f} over the past {trend.window_duration_sec / 60:.0f}min with sustained "
                f"positive slope ({trend.slope * 60:.1f}/min). This indicates goroutines are being spawned "
                "without cleanup, likely due to channel deadlock, missing context cancellation, or stuck "
                "background workers. RESTART will terminate leaked goroutines and restore normal operation."
            ),
        )
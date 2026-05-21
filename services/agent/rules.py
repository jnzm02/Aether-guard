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
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from policy import RootCauseCategory

log = logging.getLogger(__name__)


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

    def analyze(
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

        # High-confidence rules (exact patterns)
        rule = self._check_oom_kill(alert, logs)
        if rule:
            return rule

        rule = self._check_restart_loop(alert, logs)
        if rule:
            return rule

        rule = self._check_memory_leak(alert_name, metrics, logs)
        if rule:
            return rule

        rule = self._check_cpu_saturation(alert_name, metrics)
        if rule:
            return rule

        rule = self._check_traffic_spike(alert_name, metrics)
        if rule:
            return rule

        # Medium-confidence rules (heuristics)
        rule = self._check_dependency_failure(logs)
        if rule:
            return rule

        rule = self._check_bad_deployment(alert, metrics, logs)
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

    def _check_memory_leak(
        self,
        alert_name: str,
        metrics: dict[str, Optional[float]],
        logs: list[str],
    ) -> Optional[RuleMatch]:
        """
        Detect memory leaks from metrics + alert type.

        Pattern: MemorySaturation alert + high mem usage + malloc warnings
        Confidence: 0.88
        Action: RESTART (clears leaked memory)
        """
        is_memory_alert = "Memory" in alert_name or "Saturation" in alert_name
        if not is_memory_alert:
            return None

        memory_usage = metrics.get("memleak_bytes_allocated")
        if memory_usage is None:
            return None

        # Check if memory usage is abnormally high (>80% of typical max)
        # In production, this would compare to historical baseline
        is_high_memory = memory_usage > 1_000_000_000  # 1GB threshold (example)

        # Check logs for memory-related warnings
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

        # Need at least 2 signals to confirm memory leak
        signals = []
        if is_memory_alert:
            signals.append("MemorySaturation alert fired")
        if is_high_memory:
            signals.append(f"Memory usage: {memory_usage / 1e9:.2f} GB")
        if memory_warnings:
            signals.append(f"{len(memory_warnings)} memory allocation warnings in logs")

        if len(signals) >= 2:
            return RuleMatch(
                matched=True,
                rule_name="MEMORY_LEAK",
                root_cause=RootCauseCategory.MEMORY_LEAK,
                confidence=0.88,
                evidence=signals + memory_warnings[:2],
                recommended_action="RESTART",
                reasoning=(
                    "Multiple signals indicate memory leak: "
                    f"{', '.join(signals)}. "
                    "RESTART will clear leaked memory and restore normal operation."
                ),
            )
        return None

    def _check_cpu_saturation(
        self,
        alert_name: str,
        metrics: dict[str, Optional[float]],
    ) -> Optional[RuleMatch]:
        """
        Detect CPU saturation (not a spike, but sustained high usage).

        Pattern: CPU alert + high CPU usage but NO traffic spike
        Confidence: 0.85
        Action: RESTART (likely stuck loop/deadlock) or SCALE (if traffic is normal)
        """
        is_cpu_alert = "CPU" in alert_name or "Saturation" in alert_name
        if not is_cpu_alert:
            return None

        cpu_usage = metrics.get("cpu_usage_percent")
        request_rate = metrics.get("request_rate_5m")

        if cpu_usage is None:
            return None

        # High CPU defined as >80%
        is_high_cpu = cpu_usage > 80.0

        # Check if traffic is also high (would explain high CPU)
        # If traffic is normal but CPU is high → likely an efficiency issue (stuck loop)
        is_traffic_spike = request_rate and request_rate > 1000  # Example threshold

        if is_high_cpu:
            if is_traffic_spike:
                # High CPU + high traffic → scale out
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
                # High CPU + normal traffic → efficiency problem
                return RuleMatch(
                    matched=True,
                    rule_name="CPU_SATURATION_EFFICIENCY",
                    root_cause=RootCauseCategory.CPU_SATURATION,
                    confidence=0.82,
                    evidence=[
                        f"CPU usage: {cpu_usage:.1f}%",
                        f"Request rate: {request_rate or 0:.1f} rps (normal)",
                    ],
                    recommended_action="RESTART",
                    reasoning=(
                        "High CPU with normal traffic suggests inefficiency "
                        "(e.g., stuck loop, goroutine leak). RESTART to reset state."
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
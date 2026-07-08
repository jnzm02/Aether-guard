"""
Unit tests for rules.py — Rule-based incident triage engine.

Tests cover all 7 rule patterns:
1. OOM kill detection (confidence 0.95)
2. Restart loop detection (confidence 0.92)
3. Memory leak detection (confidence 0.88)
4. CPU saturation detection (confidence 0.82-0.85)
5. Traffic spike detection (confidence 0.87)
6. Dependency failure detection (confidence 0.75)
7. Bad deployment detection (confidence 0.78)

Plus edge cases, no-match scenarios, and confidence scoring.
"""

import pytest
from rules import RuleEngine
from policy import RootCauseCategory


@pytest.fixture
def engine():
    """Fixture providing a RuleEngine instance."""
    return RuleEngine()


@pytest.fixture
def empty_alert():
    """Minimal empty alert."""
    return {"labels": {}, "annotations": {}}


@pytest.fixture
def empty_metrics():
    """Empty metrics dict."""
    return {}


@pytest.fixture
def empty_logs():
    """Empty logs list."""
    return []


# ── OOM Kill Rule Tests ────────────────────────────────────────────────────────

class TestOOMKillRule:
    """Test OOM kill detection (confidence 0.95)."""

    @pytest.mark.asyncio
    async def test_oom_kill_detected(self, engine, empty_alert, empty_metrics):
        """OOM kill message in logs → matched with 0.95 confidence."""
        logs = [
            "2024-01-15 10:00:00 INFO Application starting",
            "2024-01-15 10:05:00 ERROR Out of memory: Kill process 1234",
            "2024-01-15 10:05:01 ERROR Container terminated",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)

        assert result is not None
        assert result.matched is True
        assert result.rule_name == "OOM_KILL"
        assert result.confidence == 0.95
        assert result.root_cause == RootCauseCategory.MEMORY_LEAK
        assert result.recommended_action == "RESTART"
        assert len(result.evidence) > 0
        assert "Out of memory" in result.evidence[0]

    @pytest.mark.asyncio
    async def test_oom_kill_variant_patterns(self, engine, empty_alert, empty_metrics):
        """Different OOM kill message variants should all match."""
        log_variants = [
            ["oom-kill event detected"],
            ["Killed process 1234 out of memory"],
            ["Memory cgroup out of memory: OOM"],
        ]
        for logs in log_variants:
            result = await engine.analyze(empty_alert, empty_metrics, logs)
            assert result is not None
            assert result.rule_name == "OOM_KILL"
            assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_no_oom_kill(self, engine, empty_alert, empty_metrics):
        """Normal logs without OOM → no match."""
        logs = [
            "2024-01-15 10:00:00 INFO Application starting",
            "2024-01-15 10:05:00 INFO Processing request",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)
        assert result is None  # No rule matched, escalates to LLM


# ── Restart Loop Rule Tests ────────────────────────────────────────────────────

class TestRestartLoopRule:
    """Test restart loop detection (confidence 0.92)."""

    @pytest.mark.asyncio
    async def test_restart_loop_detected(self, engine, empty_alert, empty_metrics):
        """Multiple restart events → matched with 0.92 confidence."""
        logs = [
            "2024-01-15 10:00:00 Starting server on port 8080",
            "2024-01-15 10:00:05 Listening on port 8080",
            "2024-01-15 10:00:10 Exited with code 1",
            "2024-01-15 10:00:15 Starting server on port 8080",
            "2024-01-15 10:00:20 Listening on port 8080",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)

        assert result is not None
        assert result.rule_name == "RESTART_LOOP"
        assert result.confidence == 0.92
        assert result.root_cause == RootCauseCategory.BAD_DEPLOYMENT
        assert result.recommended_action == "ROLLBACK"
        assert "restart" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_restart_loop_threshold(self, engine, empty_alert, empty_metrics):
        """Fewer than 3 restarts → no match."""
        logs = [
            "2024-01-15 10:00:00 Starting server on port 8080",
            "2024-01-15 10:00:05 Listening on port 8080",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)
        assert result is None  # Not enough restarts to trigger rule

    @pytest.mark.asyncio
    async def test_restart_loop_exactly_3_restarts(self, engine, empty_alert, empty_metrics):
        """Exactly 3 restart indicators → match."""
        logs = [
            "Starting server on port 8080",
            "Listening on port 8080",
            "Container started successfully",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)
        assert result is not None
        assert result.rule_name == "RESTART_LOOP"


# ── Memory Leak Rule Tests ─────────────────────────────────────────────────────

class TestMemoryLeakRule:
    """Test memory leak detection (confidence 0.88)."""

    @pytest.mark.asyncio
    async def test_memory_leak_detected(self, engine, empty_logs):
        """Memory alert + high usage + warnings → matched."""
        alert = {
            "labels": {"alertname": "MemorySaturation", "severity": "warning"},
            "annotations": {},
        }
        metrics = {
            "memleak_bytes_allocated": 1_200_000_000,  # 1.2 GB (above threshold)
        }
        logs = [
            "2024-01-15 10:00:00 ERROR malloc failed: out of memory",
            "2024-01-15 10:05:00 WARN memory exhausted, retrying",
        ]
        result = await engine.analyze(alert, metrics, logs)

        assert result is not None
        assert result.rule_name == "MEMORY_LEAK"
        assert result.confidence == 0.88
        assert result.root_cause == RootCauseCategory.MEMORY_LEAK
        assert result.recommended_action == "RESTART"
        assert len(result.evidence) >= 2  # Multiple signals

    @pytest.mark.asyncio
    async def test_memory_leak_insufficient_signals(self, engine, empty_logs):
        """Memory alert alone without other signals → no match."""
        alert = {
            "labels": {"alertname": "MemorySaturation", "severity": "warning"},
            "annotations": {},
        }
        metrics = {}  # No metrics data
        result = await engine.analyze(alert, metrics, empty_logs)
        assert result is None  # Need at least 2 signals

    @pytest.mark.asyncio
    async def test_memory_leak_not_memory_alert(self, engine, empty_logs):
        """High memory usage but wrong alert type → no match."""
        alert = {
            "labels": {"alertname": "LatencyHigh", "severity": "warning"},
            "annotations": {},
        }
        metrics = {
            "memleak_bytes_allocated": 1_200_000_000,
        }
        logs = ["ERROR malloc failed"]
        result = await engine.analyze(alert, metrics, logs)
        assert result is None  # Not a memory alert


# ── CPU Saturation Rule Tests ──────────────────────────────────────────────────

class TestCPUSaturationRule:
    """Test CPU saturation detection (confidence 0.82-0.85)."""

    @pytest.mark.asyncio
    async def test_cpu_saturation_with_traffic_spike(self, engine, empty_logs):
        """High CPU + high traffic → SCALE action."""
        alert = {
            "labels": {"alertname": "CPUSaturation", "severity": "warning"},
            "annotations": {},
        }
        metrics = {
            "cpu_usage_percent": 85.0,  # High CPU
            "request_rate_5m": 1500.0,  # High traffic
        }
        result = await engine.analyze(alert, metrics, empty_logs)

        assert result is not None
        assert result.rule_name == "CPU_SATURATION_TRAFFIC"
        assert result.confidence == 0.85
        assert result.root_cause == RootCauseCategory.TRAFFIC_SPIKE
        assert result.recommended_action == "SCALE"

    @pytest.mark.asyncio
    async def test_cpu_saturation_efficiency_problem(self, engine, empty_logs):
        """High CPU + normal traffic → RESTART action."""
        alert = {
            "labels": {"alertname": "CPUSaturation", "severity": "warning"},
            "annotations": {},
        }
        metrics = {
            "cpu_usage_percent": 85.0,  # High CPU
            "request_rate_5m": 100.0,   # Normal traffic
        }
        result = await engine.analyze(alert, metrics, empty_logs)

        assert result is not None
        assert result.rule_name == "CPU_SATURATION_EFFICIENCY"
        assert result.confidence == 0.82
        assert result.root_cause == RootCauseCategory.CPU_SATURATION
        assert result.recommended_action == "RESTART"

    @pytest.mark.asyncio
    async def test_cpu_saturation_missing_cpu_data(self, engine, empty_logs):
        """CPU alert but no CPU metric → no match."""
        alert = {
            "labels": {"alertname": "CPUSaturation", "severity": "warning"},
            "annotations": {},
        }
        metrics = {}  # No CPU data
        result = await engine.analyze(alert, metrics, empty_logs)
        assert result is None

    @pytest.mark.asyncio
    async def test_cpu_saturation_below_threshold(self, engine, empty_logs):
        """CPU usage below 80% → no match."""
        alert = {
            "labels": {"alertname": "CPUSaturation", "severity": "warning"},
            "annotations": {},
        }
        metrics = {
            "cpu_usage_percent": 70.0,  # Below 80% threshold
        }
        result = await engine.analyze(alert, metrics, empty_logs)
        assert result is None


# ── Traffic Spike Rule Tests ───────────────────────────────────────────────────

class TestTrafficSpikeRule:
    """Test traffic spike detection (confidence 0.87)."""

    @pytest.mark.asyncio
    async def test_traffic_spike_with_errors(self, engine, empty_alert, empty_logs):
        """Traffic spike + high errors → matched."""
        metrics = {
            "request_rate_5m": 1500.0,  # Traffic spike
            "error_rate_5m": 0.05,      # 5% errors
            "latency_p99_5m": 0.5,
        }
        result = await engine.analyze(empty_alert, metrics, empty_logs)

        assert result is not None
        assert result.rule_name == "TRAFFIC_SPIKE"
        assert result.confidence == 0.87
        assert result.root_cause == RootCauseCategory.TRAFFIC_SPIKE
        assert result.recommended_action == "SCALE"

    @pytest.mark.asyncio
    async def test_traffic_spike_with_latency(self, engine, empty_alert, empty_logs):
        """Traffic spike + high latency → matched."""
        metrics = {
            "request_rate_5m": 1500.0,  # Traffic spike
            "error_rate_5m": 0.001,
            "latency_p99_5m": 2.0,      # 2s P99 (high)
        }
        result = await engine.analyze(empty_alert, metrics, empty_logs)

        assert result is not None
        assert result.rule_name == "TRAFFIC_SPIKE"

    @pytest.mark.asyncio
    async def test_traffic_spike_no_symptoms(self, engine, empty_alert, empty_logs):
        """Traffic spike without errors/latency → no match."""
        metrics = {
            "request_rate_5m": 1500.0,  # Traffic spike
            "error_rate_5m": 0.001,     # Low errors
            "latency_p99_5m": 0.2,      # Good latency
        }
        result = await engine.analyze(empty_alert, metrics, empty_logs)
        assert result is None  # Need traffic + symptoms


# ── Dependency Failure Rule Tests ──────────────────────────────────────────────

class TestDependencyFailureRule:
    """Test dependency failure detection (confidence 0.75)."""

    @pytest.mark.asyncio
    async def test_dependency_failure_connection_refused(self, engine, empty_alert, empty_metrics):
        """Connection refused errors in logs → matched."""
        logs = [
            "2024-01-15 10:00:00 ERROR connection refused to database:5432",
            "2024-01-15 10:00:01 WARN retrying connection...",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)

        assert result is not None
        assert result.rule_name == "DEPENDENCY_FAILURE"
        assert result.confidence == 0.75
        assert result.root_cause == RootCauseCategory.DEPENDENCY_FAILURE
        assert result.recommended_action == "RESTART"

    @pytest.mark.asyncio
    async def test_dependency_failure_timeout(self, engine, empty_alert, empty_metrics):
        """Timeout errors → matched."""
        logs = [
            "2024-01-15 10:00:00 ERROR dial tcp 10.0.0.1:5432: timeout",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)
        assert result is not None
        assert result.rule_name == "DEPENDENCY_FAILURE"

    @pytest.mark.asyncio
    async def test_dependency_failure_dns(self, engine, empty_alert, empty_metrics):
        """DNS resolution failures → matched."""
        logs = [
            "2024-01-15 10:00:00 ERROR no such host: database.internal",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)
        assert result is not None
        assert result.rule_name == "DEPENDENCY_FAILURE"

    @pytest.mark.asyncio
    async def test_dependency_failure_redis(self, engine, empty_alert, empty_metrics):
        """Redis connection failures → matched."""
        logs = [
            "2024-01-15 10:00:00 ERROR redis connection failed: ECONNREFUSED",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)
        assert result is not None
        assert result.rule_name == "DEPENDENCY_FAILURE"

    @pytest.mark.asyncio
    async def test_no_dependency_failure(self, engine, empty_alert, empty_metrics):
        """Normal logs without connection errors → no match."""
        logs = [
            "2024-01-15 10:00:00 INFO Processing request",
            "2024-01-15 10:00:01 INFO Request completed successfully",
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)
        assert result is None


# ── Bad Deployment Rule Tests ──────────────────────────────────────────────────

class TestBadDeploymentRule:
    """Test bad deployment detection (confidence 0.78)."""

    @pytest.mark.asyncio
    async def test_bad_deployment_detected(self, engine):
        """High errors + startup failures + recent alert → matched."""
        alert = {
            "labels": {"alertname": "ErrorRateHigh", "severity": "critical"},
            "annotations": {"age": "2 minutes"},
        }
        metrics = {
            "error_rate_5m": 0.10,  # 10% errors (high)
        }
        logs = [
            "2024-01-15 10:00:00 FATAL panic: runtime error",
            "2024-01-15 10:00:01 ERROR failed to start HTTP server",
        ]
        result = await engine.analyze(alert, metrics, logs)

        assert result is not None
        assert result.rule_name == "BAD_DEPLOYMENT"
        assert result.confidence == 0.78
        assert result.root_cause == RootCauseCategory.BAD_DEPLOYMENT
        assert result.recommended_action == "ROLLBACK"

    @pytest.mark.asyncio
    async def test_bad_deployment_insufficient_signals(self, engine, empty_logs):
        """Only high errors, no other signals → no match."""
        alert = {
            "labels": {"alertname": "ErrorRateHigh", "severity": "critical"},
            "annotations": {},
        }
        metrics = {
            "error_rate_5m": 0.10,
        }
        result = await engine.analyze(alert, metrics, empty_logs)
        assert result is None  # Need at least 2 signals

    @pytest.mark.asyncio
    async def test_bad_deployment_startup_errors_only(self, engine, empty_alert):
        """Startup errors + recent alert but low errors → matched."""
        alert = {
            "labels": {"alertname": "ContainerRestarting", "severity": "warning"},
            "annotations": {"age": "1 minute"},
        }
        metrics = {
            "error_rate_5m": 0.001,  # Low errors
        }
        logs = [
            "FATAL initialization failed: config not found",
            "PANIC cannot bind to port 8080",
        ]
        result = await engine.analyze(alert, metrics, logs)
        # Should match because we have 2 signals: startup errors + recent alert
        assert result is not None
        assert result.rule_name == "BAD_DEPLOYMENT"


# ── Priority and Ordering Tests ────────────────────────────────────────────────

class TestRulePriorityOrdering:
    """Test that high-confidence rules are evaluated before low-confidence ones."""

    @pytest.mark.asyncio
    async def test_oom_kill_takes_priority(self, engine):
        """OOM kill should match first (highest confidence)."""
        alert = {
            "labels": {"alertname": "MemorySaturation", "severity": "critical"},
            "annotations": {"age": "1 minute"},
        }
        metrics = {
            "memleak_bytes_allocated": 1_500_000_000,
            "error_rate_5m": 0.10,
        }
        logs = [
            "Out of memory: Kill process 1234",  # OOM kill (0.95 confidence)
            "panic: runtime error",               # Bad deployment signal
        ]
        result = await engine.analyze(alert, metrics, logs)

        # Should match OOM_KILL (0.95) not BAD_DEPLOYMENT (0.78)
        assert result.rule_name == "OOM_KILL"
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_restart_loop_before_bad_deployment(self, engine):
        """Restart loop (0.92) should match before bad deployment (0.78)."""
        alert = {
            "labels": {"alertname": "ContainerRestarting", "severity": "critical"},
            "annotations": {"age": "1 minute"},
        }
        metrics = {
            "error_rate_5m": 0.10,
        }
        logs = [
            "Starting server on port 8080",
            "Listening on port 8080",
            "Exited with code 1",
            "panic: runtime error",  # Bad deployment signal
        ]
        result = await engine.analyze(alert, metrics, logs)
        assert result.rule_name == "RESTART_LOOP"
        assert result.confidence == 0.92


# ── Edge Cases and Integration ─────────────────────────────────────────────────

class TestEdgeCases:
    """Test edge cases and unusual scenarios."""

    @pytest.mark.asyncio
    async def test_empty_inputs(self, engine, empty_alert, empty_metrics, empty_logs):
        """Empty alert/metrics/logs → no match."""
        result = await engine.analyze(empty_alert, empty_metrics, empty_logs)
        assert result is None  # Escalates to LLM

    @pytest.mark.asyncio
    async def test_rule_match_dataclass_fields(self, engine, empty_alert, empty_metrics):
        """RuleMatch should have all required fields."""
        logs = ["Out of memory: Kill process 1234"]
        result = await engine.analyze(empty_alert, empty_metrics, logs)

        assert result is not None
        assert hasattr(result, "matched")
        assert hasattr(result, "rule_name")
        assert hasattr(result, "root_cause")
        assert hasattr(result, "confidence")
        assert hasattr(result, "evidence")
        assert hasattr(result, "recommended_action")
        assert hasattr(result, "reasoning")
        assert result.matched is True
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.evidence, list)
        assert len(result.reasoning) > 0

    @pytest.mark.asyncio
    async def test_multiple_rules_could_match(self, engine):
        """When multiple patterns present, highest confidence wins."""
        alert = {
            "labels": {"alertname": "CPUSaturation", "severity": "warning"},
            "annotations": {},
        }
        metrics = {
            "cpu_usage_percent": 85.0,
            "request_rate_5m": 100.0,
        }
        logs = [
            "connection refused to database:5432",  # Dependency failure (0.75)
        ]
        result = await engine.analyze(alert, metrics, logs)

        # CPU saturation rule (0.82) should match before dependency failure (0.75)
        # because CPU rules are evaluated first in the priority order
        assert result.rule_name == "CPU_SATURATION_EFFICIENCY"

    @pytest.mark.asyncio
    async def test_case_insensitive_pattern_matching(self, engine, empty_alert, empty_metrics):
        """Pattern matching should be case-insensitive."""
        logs = [
            "OUT OF MEMORY: KILL PROCESS 1234",  # All caps
        ]
        result = await engine.analyze(empty_alert, empty_metrics, logs)
        assert result is not None
        assert result.rule_name == "OOM_KILL"

    @pytest.mark.asyncio
    async def test_confidence_ranges(self, engine):
        """All rule confidences should be in valid ranges."""
        # High-confidence rules (≥0.90)
        logs_oom = ["Out of memory: Kill process 1234"]
        result = await engine.analyze({}, {}, logs_oom)
        assert result.confidence >= 0.90

        # Medium-confidence rules (0.70-0.89)
        logs_dep = ["connection refused to database"]
        result = await engine.analyze({}, {}, logs_dep)
        assert 0.70 <= result.confidence < 0.90


# ── Parameterized Tests ────────────────────────────────────────────────────────

@pytest.mark.parametrize("log_pattern,expected_rule", [
    ("Out of memory: Kill process", "OOM_KILL"),
    ("oom-kill event", "OOM_KILL"),
    ("Starting server on port 8080\nListening on port 8080\nExited with code 1", "RESTART_LOOP"),
    ("connection refused", "DEPENDENCY_FAILURE"),
    ("dial tcp timeout", "DEPENDENCY_FAILURE"),
    ("no such host", "DEPENDENCY_FAILURE"),
])
@pytest.mark.asyncio
async def test_log_pattern_matching(log_pattern, expected_rule):
    """Test various log patterns match correct rules."""
    engine = RuleEngine()
    logs = log_pattern.split("\n")
    result = await engine.analyze({}, {}, logs)
    assert result is not None
    assert result.rule_name == expected_rule


@pytest.mark.parametrize("cpu,traffic,expected_action", [
    (85, 1500, "SCALE"),    # High CPU + high traffic → SCALE
    (85, 100, "RESTART"),   # High CPU + normal traffic → RESTART
    (70, 1500, None),       # Normal CPU + high traffic → no match
])
@pytest.mark.asyncio
async def test_cpu_saturation_action_routing(cpu, traffic, expected_action):
    """Test CPU saturation rule routes to correct action."""
    engine = RuleEngine()
    alert = {"labels": {"alertname": "CPUSaturation"}, "annotations": {}}
    metrics = {"cpu_usage_percent": cpu, "request_rate_5m": traffic}
    result = await engine.analyze(alert, metrics, [])

    if expected_action is None:
        assert result is None
    else:
        assert result is not None
        assert result.recommended_action == expected_action
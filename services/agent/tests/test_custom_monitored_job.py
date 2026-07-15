"""
Test for custom MONITORED_JOB configuration.

Verifies that Aether-Guard can be configured to monitor any Prometheus-instrumented
service by setting the MONITORED_JOB environment variable, which replaces hardcoded
"target-service" references in PromQL queries.
"""

import os
from unittest import mock


def test_custom_monitored_job_substitution():
    """
    Test that custom MONITORED_JOB value is correctly substituted into PromQL queries.

    This test verifies that when MONITORED_JOB="my-custom-service", the rule engine
    generates PromQL queries with job="my-custom-service" instead of hardcoded
    job="target-service".

    We test the goroutine leak rule as it's representative of all trend-based rules
    that use the job label.
    """
    # Set custom job name before importing rules module
    custom_job = "my-custom-api-service"
    with mock.patch.dict(os.environ, {"MONITORED_JOB": custom_job}):
        # Import after setting env var to ensure config picks up custom value
        # Note: This uses importlib.reload to force re-evaluation of module-level config
        import importlib
        import rules
        importlib.reload(rules)

        # Verify the config was loaded correctly
        assert rules.MONITORED_JOB == custom_job, f"Expected MONITORED_JOB={custom_job}, got {rules.MONITORED_JOB}"

        # Create a mock TrendClassifier to capture the PromQL query
        from unittest.mock import MagicMock

        captured_queries = []

        class MockTrendClassifier:
            async def analyze_metric(self, metric_expr, **kwargs):
                # Capture the query for inspection
                captured_queries.append(metric_expr)

                # Return a mock trend that doesn't match (is_rising=False)
                # This prevents the rule from matching, but we still verify the query
                mock_trend = MagicMock()
                mock_trend.is_rising = False
                mock_trend.slope = 0.001
                return mock_trend

        # Patch TrendClassifier in trend_analysis module (where it's defined)
        with mock.patch('trend_analysis.TrendClassifier', return_value=MockTrendClassifier()):
            engine = rules.RuleEngine()

            # Trigger goroutine leak detection (will query Prometheus for goroutine count)
            metrics = {
                "runtime_goroutines": 200,  # Above threshold to trigger trend analysis
            }
            logs = []

            # This should trigger the goroutine leak rule, which will call TrendClassifier
            import asyncio
            asyncio.run(engine._check_goroutine_leak("HighGoroutineCount", metrics, logs))

            # Verify PromQL query contains custom job name
            assert len(captured_queries) >= 1, "Expected at least one Prometheus query"

            # Check the goroutine query
            goroutine_query = captured_queries[0]
            assert f'job="{custom_job}"' in goroutine_query, \
                f"Expected PromQL query to contain job=\"{custom_job}\", got: {goroutine_query}"
            assert 'job="target-service"' not in goroutine_query, \
                f"PromQL query should not contain hardcoded 'target-service', got: {goroutine_query}"

            # Verify the query is for goroutines metric
            assert "go_goroutines" in goroutine_query, \
                f"Expected query for go_goroutines metric, got: {goroutine_query}"


def test_default_monitored_job_still_works():
    """
    Test that default MONITORED_JOB="target-service" still works (backward compatibility).

    This ensures existing tests and deployments continue to work without env var changes.
    """
    # Don't set MONITORED_JOB, use default
    with mock.patch.dict(os.environ, {}, clear=False):
        # Remove MONITORED_JOB if it exists
        os.environ.pop("MONITORED_JOB", None)

        import importlib
        import rules
        importlib.reload(rules)

        # Verify default value
        assert rules.MONITORED_JOB == "target-service", \
            f"Expected default MONITORED_JOB='target-service', got {rules.MONITORED_JOB}"


def test_enrichment_module_uses_monitored_job():
    """
    Test that enrichment.py also respects MONITORED_JOB for runtime metrics.

    The enrichment module fetches go_goroutines and go_memstats_heap_inuse_bytes
    metrics, which should use the configured job label.
    """
    custom_job = "my-service"
    with mock.patch.dict(os.environ, {"MONITORED_JOB": custom_job}):
        import importlib
        import enrichment
        importlib.reload(enrichment)

        # Verify MONITORED_JOB config
        assert enrichment.MONITORED_JOB == custom_job

        # Check that _KEY_METRICS contains the custom job
        metrics_dict = {key: expr for key, expr in enrichment._KEY_METRICS}

        # Verify runtime_goroutines query uses custom job
        goroutine_expr = metrics_dict.get("runtime_goroutines", "")
        assert f'job="{custom_job}"' in goroutine_expr, \
            f"Expected runtime_goroutines query to use job=\"{custom_job}\", got: {goroutine_expr}"

        # Verify runtime_heap_inuse_bytes query uses custom job
        heap_expr = metrics_dict.get("runtime_heap_inuse_bytes", "")
        assert f'job="{custom_job}"' in heap_expr, \
            f"Expected runtime_heap_inuse_bytes query to use job=\"{custom_job}\", got: {heap_expr}"

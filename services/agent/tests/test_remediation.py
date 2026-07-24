"""
Tests for remediation.execute_action — specifically the three safety gates
that protect production systems from incorrect automated actions.

All tests run with DRY_RUN=true (set in conftest.py) so no Docker calls
are made even if a real Docker socket is present.
"""
import sys
import os
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force dry-run and a known container name before importing remediation.
os.environ["DRY_RUN"] = "true"
os.environ["TARGET_CONTAINER"] = "test-container"
os.environ["REMEDIATION_COOLDOWN_S"] = "300"

import remediation  # noqa: E402
from remediation import execute_action, RemediationResult, THRESHOLDS  # noqa: E402


def make_analysis(action: str = "RESTART", confidence: float = 0.95) -> dict:
    return {
        "action":     action,
        "confidence": confidence,
        "alertname":  "TestAlert",
    }


# ─────────────────────────────────────────────────────────────────────────────
# RemediationResult dataclass
# ─────────────────────────────────────────────────────────────────────────────

class TestRemediationResult:
    def test_as_dict_contains_required_keys(self):
        r = RemediationResult(
            action="RESTART", executed=True, outcome="success",
            reason="ok", container="c",
        )
        d = r.as_dict()
        for key in ("action", "executed", "outcome", "reason", "container", "executed_at", "details"):
            assert key in d, f"missing key: {key}"

    def test_as_dict_executed_at_is_iso_string(self):
        r = RemediationResult(
            action="IGNORE", executed=False, outcome="no_op",
            reason="ignore", container="c",
        )
        assert "T" in r.as_dict()["executed_at"]  # ISO 8601 contains T

    def test_details_defaults_to_empty_dict(self):
        r = RemediationResult(
            action="SCALE", executed=False, outcome="dry_run",
            reason="dry", container="c",
        )
        assert r.details == {}


# ─────────────────────────────────────────────────────────────────────────────
# Gate 1 — confidence threshold
# ─────────────────────────────────────────────────────────────────────────────

class TestConfidenceGate:
    def setup_method(self):
        # Clear cooldown state before each test.
        remediation._last_action_ts.clear()

    @pytest.mark.parametrize("action,threshold_key", [
        ("RESTART",  "RESTART"),
        ("SCALE",    "SCALE"),
        ("ROLLBACK", "ROLLBACK"),
    ])
    def test_below_threshold_returns_skipped(self, action, threshold_key):
        low_confidence = THRESHOLDS[threshold_key] - 0.01
        result = execute_action(action, make_analysis(action, low_confidence))
        assert result.outcome == "skipped"
        assert result.executed is False

    def test_at_threshold_proceeds_to_dry_run(self):
        # Confidence exactly at threshold should pass gate 1 and reach gate 3.
        confidence = THRESHOLDS["RESTART"]
        result = execute_action("RESTART", make_analysis("RESTART", confidence))
        # DRY_RUN=true → should get dry_run, not skipped.
        assert result.outcome == "dry_run"

    def test_ignore_has_zero_threshold(self):
        result = execute_action("IGNORE", make_analysis("IGNORE", 0.0))
        # IGNORE passes all gates — outcome is no_op (from _ignore handler)
        # but DRY_RUN intercepts first → dry_run
        assert result.outcome in ("dry_run", "no_op")


# ─────────────────────────────────────────────────────────────────────────────
# Gate 2 — cooldown
# ─────────────────────────────────────────────────────────────────────────────

class TestCooldownGate:
    def setup_method(self):
        remediation._last_action_ts.clear()
        # Temporarily disable dry-run so we can hit gate 2.
        os.environ["DRY_RUN"] = "false"
        remediation.DRY_RUN = False
        # Disable Redis to test in-memory fallback (avoid Redis connection attempts)
        self._original_redis_client = remediation._redis_client
        remediation._redis_client = None

    def teardown_method(self):
        # Restore dry-run after each test.
        os.environ["DRY_RUN"] = "true"
        remediation.DRY_RUN = True
        remediation._last_action_ts.clear()
        # Restore Redis client
        remediation._redis_client = self._original_redis_client

    def test_second_action_within_cooldown_is_skipped(self):
        container = remediation.TARGET_CONTAINER
        # Simulate a recent action by setting timestamp to now.
        remediation._last_action_ts[container] = time.monotonic()

        result = execute_action("RESTART", make_analysis("RESTART", 0.95))
        assert result.outcome == "skipped"
        assert "Cooldown" in result.reason

    def test_action_after_cooldown_is_not_blocked(self):
        container = remediation.TARGET_CONTAINER
        # Simulate an old action (well past cooldown).
        remediation._last_action_ts[container] = time.monotonic() - (remediation.COOLDOWN_SECONDS + 1)

        result = execute_action("RESTART", make_analysis("RESTART", 0.95))
        # Should NOT be skipped by cooldown gate (Docker unavailable → failed or success).
        assert result.outcome != "skipped" or "Cooldown" not in result.reason

    def test_ignore_bypasses_cooldown(self):
        container = remediation.TARGET_CONTAINER
        remediation._last_action_ts[container] = time.monotonic()  # fresh cooldown

        # IGNORE should never be blocked by cooldown.
        result = execute_action("IGNORE", make_analysis("IGNORE", 0.95))
        assert result.outcome != "skipped"

    def test_redis_miss_falls_back_to_in_memory(self):
        """
        Scenario: Redis client exists but key doesn't exist in Redis (TTL=-2).
        In-memory dict has recent timestamp.
        Expected: Cooldown check should fall through to in-memory dict and block the action.

        This tests the bug fix: previously, line 115 returned (False, inf) when Redis
        key didn't exist, bypassing the in-memory fallback entirely.
        """
        from unittest.mock import MagicMock

        container = remediation.TARGET_CONTAINER

        # Restore Redis client (if it was None from setup_method)
        if remediation._redis_client is None:
            # Create a mock Redis client that returns TTL=-2 (key doesn't exist)
            mock_redis = MagicMock()
            mock_redis.ttl.return_value = -2  # Key doesn't exist in Redis
            remediation._redis_client = mock_redis

        # Set in-memory cooldown (recent action)
        remediation._last_action_ts[container] = time.monotonic()

        # Execute action - should be blocked by in-memory cooldown
        result = execute_action("RESTART", make_analysis("RESTART", 0.95))
        assert result.outcome == "skipped", (
            f"Expected cooldown to block action (in-memory fallback), "
            f"but got outcome={result.outcome!r}"
        )
        assert "Cooldown" in result.reason

    def test_redis_cooldown_active_blocks_action(self):
        """
        Regression test for remediation.py:121-127 (cooldown ACTIVE path, ttl > 0).

        Scenario: Redis client exists and key exists with positive TTL (cooldown active).
        Expected: Action should be BLOCKED, elapsed time calculated from TTL.

        This tests the case where Redis IS authoritative (cooldown active in Redis).
        """
        from unittest.mock import MagicMock

        container = remediation.TARGET_CONTAINER

        # Create mock Redis client that returns TTL > 0 (cooldown active)
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = 150  # 150 seconds remaining in cooldown
        remediation._redis_client = mock_redis

        # Execute action - should be blocked by Redis cooldown
        result = execute_action("RESTART", make_analysis("RESTART", 0.95))
        assert result.outcome == "skipped", (
            f"Expected Redis cooldown to block action (ttl=150 > 0), "
            f"but got outcome={result.outcome!r}"
        )
        assert "Cooldown" in result.reason
        # Verify that Redis ttl() was actually called
        mock_redis.ttl.assert_called_once_with(f"cooldown:{container}")

    def test_redis_connection_failure_falls_back_gracefully(self):
        """
        Regression test for remediation.py:126-127 (Redis exception path).

        Scenario: Redis client exists but Redis query raises exception.
        Expected: System should FAIL OPEN — fall back to in-memory dict.

        This tests the behavior: Redis errors do NOT block execution entirely,
        but degrade gracefully to in-memory state.
        """
        from unittest.mock import MagicMock

        # Create mock Redis client that raises exception on ttl()
        mock_redis = MagicMock()
        mock_redis.ttl.side_effect = Exception("Redis connection timeout")
        remediation._redis_client = mock_redis

        # Clear in-memory state (no cooldown active in fallback)
        remediation._last_action_ts.clear()

        # Execute action - should succeed via in-memory fallback (no cooldown)
        result = execute_action("RESTART", make_analysis("RESTART", 0.95))
        # Actual behavior: should NOT be skipped (falls back to in-memory dict which is empty)
        # If in-memory also had a cooldown, it would be blocked
        assert result.outcome in ("dry_run", "failed"), (
            f"Expected fallback to proceed (Redis failed, in-memory clear), "
            f"but got outcome={result.outcome!r}"
        )

    def test_redis_ttl_zero_does_not_block_action(self):
        """
        Regression test for remediation.py:119 (ttl > 0 boundary).

        Scenario: Redis returns ttl=0 (key exists but expired or no expiration).
        Expected: Action should NOT be blocked (cooldown is not active).
        """
        from unittest.mock import MagicMock

        container = remediation.TARGET_CONTAINER

        # Create mock Redis client that returns ttl=0 (key expired/no expiration)
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = 0
        remediation._redis_client = mock_redis

        # Clear in-memory state to ensure we're testing Redis path only
        remediation._last_action_ts.clear()

        # Execute action - should NOT be blocked (ttl=0 means expired/no cooldown)
        result = execute_action("RESTART", make_analysis("RESTART", 0.95))
        assert result.outcome in ("dry_run", "failed"), (
            f"Expected action to proceed when ttl=0 (no active cooldown), "
            f"but got outcome={result.outcome!r}"
        )
        mock_redis.ttl.assert_called_once_with(f"cooldown:{container}")

    def test_redis_ttl_one_blocks_action(self):
        """
        Regression test for remediation.py:119 (ttl > 0 boundary).

        Scenario: Redis returns ttl=1 (key has 1 second remaining).
        Expected: Action SHOULD be blocked (cooldown is still active).

        This catches the mutation from "if ttl > 0:" to "if ttl > 1:"
        which would incorrectly allow actions when ttl==1.
        """
        from unittest.mock import MagicMock

        container = remediation.TARGET_CONTAINER

        # Create mock Redis client that returns ttl=1 (1 second remaining)
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = 1
        remediation._redis_client = mock_redis

        # Clear in-memory state to ensure we're testing Redis path only
        remediation._last_action_ts.clear()

        # Execute action - should BE blocked (ttl=1 means cooldown still active)
        result = execute_action("RESTART", make_analysis("RESTART", 0.95))
        assert result.outcome == "skipped", (
            f"Expected action skipped by cooldown when ttl=1, "
            f"but got outcome={result.outcome!r}"
        )
        mock_redis.ttl.assert_called_once_with(f"cooldown:{container}")


# ─────────────────────────────────────────────────────────────────────────────
# Gate 3 — dry-run
# ─────────────────────────────────────────────────────────────────────────────

class TestDryRunGate:
    def setup_method(self):
        remediation._last_action_ts.clear()
        os.environ["DRY_RUN"] = "true"
        remediation.DRY_RUN = True

    @pytest.mark.parametrize("action", ["RESTART", "SCALE", "ROLLBACK"])
    def test_dry_run_returns_dry_run_outcome(self, action):
        result = execute_action(action, make_analysis(action, 0.95))
        assert result.outcome == "dry_run"
        assert result.executed is False

    def test_dry_run_reason_mentions_action(self):
        result = execute_action("RESTART", make_analysis("RESTART", 0.95))
        assert "RESTART" in result.reason

    def test_dry_run_does_not_update_cooldown_timestamp(self):
        container = remediation.TARGET_CONTAINER
        before = remediation._last_action_ts.get(container, 0.0)

        execute_action("RESTART", make_analysis("RESTART", 0.95))

        after = remediation._last_action_ts.get(container, 0.0)
        assert after == before, "dry-run should not update cooldown timestamp"

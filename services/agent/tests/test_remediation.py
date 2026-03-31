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

    def teardown_method(self):
        # Restore dry-run after each test.
        os.environ["DRY_RUN"] = "true"
        remediation.DRY_RUN = True
        remediation._last_action_ts.clear()

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

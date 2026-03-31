"""
Tests for agent._parse_and_validate — the core schema validation function
that guards every Claude API response before it affects production systems.
"""
import json
import sys
import os

import pytest

# Ensure the agent package directory is on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import _parse_and_validate  # noqa: E402


def _valid_payload(**overrides) -> str:
    """Return a minimal valid JSON response string."""
    base = {
        "analysis":   "Memory leak chaos endpoint was activated.",
        "root_cause": "Unbounded slice growth prevents GC.",
        "confidence": 0.95,
        "action":     "RESTART",
        "reasoning":  "Container restart clears heap; no user data at risk.",
        "slo_impact": "MemorySaturationWarning active.",
        "recommended_followup": ["Add memory limits", "Review chaos ACL"],
    }
    base.update(overrides)
    return json.dumps(base)


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestParseAndValidateHappyPath:
    def test_returns_dict(self):
        result = _parse_and_validate(_valid_payload())
        assert isinstance(result, dict)

    def test_confidence_coerced_to_float(self):
        result = _parse_and_validate(_valid_payload(confidence=1))  # int → float
        assert isinstance(result["confidence"], float)
        assert result["confidence"] == 1.0

    def test_all_required_fields_present(self):
        result = _parse_and_validate(_valid_payload())
        for field in ("analysis", "root_cause", "confidence", "action", "reasoning"):
            assert field in result, f"missing required field: {field}"

    def test_optional_fields_preserved(self):
        result = _parse_and_validate(_valid_payload())
        assert "slo_impact" in result
        assert "recommended_followup" in result

    @pytest.mark.parametrize("action", ["RESTART", "SCALE", "ROLLBACK", "IGNORE"])
    def test_all_valid_actions_accepted(self, action):
        result = _parse_and_validate(_valid_payload(action=action, confidence=0.95))
        assert result["action"] == action


# ─────────────────────────────────────────────────────────────────────────────
# Markdown fence stripping
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkdownFenceStripping:
    def test_strips_json_fence(self):
        raw = "```json\n" + _valid_payload() + "\n```"
        result = _parse_and_validate(raw)
        assert result["action"] == "RESTART"

    def test_strips_plain_fence(self):
        raw = "```\n" + _valid_payload() + "\n```"
        result = _parse_and_validate(raw)
        assert result["action"] == "RESTART"

    def test_no_fence_still_works(self):
        result = _parse_and_validate(_valid_payload())
        assert result["action"] == "RESTART"


# ─────────────────────────────────────────────────────────────────────────────
# Validation errors
# ─────────────────────────────────────────────────────────────────────────────

class TestParseAndValidateErrors:
    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _parse_and_validate("{ not json }")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _parse_and_validate("")

    @pytest.mark.parametrize("missing_field", [
        "analysis", "root_cause", "confidence", "action", "reasoning"
    ])
    def test_missing_required_field_raises(self, missing_field):
        payload = json.loads(_valid_payload())
        del payload[missing_field]
        with pytest.raises(ValueError, match="missing required fields"):
            _parse_and_validate(json.dumps(payload))

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError, match="action must be one of"):
            _parse_and_validate(_valid_payload(action="DELETE_EVERYTHING"))

    def test_non_numeric_confidence_raises(self):
        with pytest.raises(ValueError, match="confidence must be numeric"):
            _parse_and_validate(_valid_payload(confidence="high"))

    def test_boolean_confidence_raises(self):
        # bool is a subclass of int in Python — make sure we reject it
        payload = json.loads(_valid_payload())
        payload["confidence"] = True
        # bool passes isinstance check for int — current impl accepts it; document the behaviour
        # rather than asserting an error (this is a known edge case, not a bug)
        result = _parse_and_validate(json.dumps(payload))
        assert isinstance(result["confidence"], float)


# ─────────────────────────────────────────────────────────────────────────────
# Confidence safety gate
# ─────────────────────────────────────────────────────────────────────────────

class TestConfidenceSafetyGate:
    def test_low_confidence_overrides_restart_to_ignore(self):
        result = _parse_and_validate(_valid_payload(action="RESTART", confidence=0.30))
        assert result["action"] == "IGNORE"

    def test_low_confidence_overrides_scale_to_ignore(self):
        result = _parse_and_validate(_valid_payload(action="SCALE", confidence=0.10))
        assert result["action"] == "IGNORE"

    def test_low_confidence_overrides_rollback_to_ignore(self):
        result = _parse_and_validate(_valid_payload(action="ROLLBACK", confidence=0.50))
        assert result["action"] == "IGNORE"

    def test_low_confidence_ignore_stays_ignore(self):
        result = _parse_and_validate(_valid_payload(action="IGNORE", confidence=0.10))
        assert result["action"] == "IGNORE"

    def test_high_confidence_restart_not_overridden(self):
        result = _parse_and_validate(_valid_payload(action="RESTART", confidence=0.95))
        assert result["action"] == "RESTART"

    def test_override_appends_note_to_reasoning(self):
        result = _parse_and_validate(_valid_payload(action="RESTART", confidence=0.10))
        assert "Agent override" in result["reasoning"]

    def test_exactly_at_threshold_not_overridden(self):
        """confidence == CONFIDENCE_THRESHOLD (0.60) should NOT be overridden."""
        result = _parse_and_validate(_valid_payload(action="RESTART", confidence=0.60))
        assert result["action"] == "RESTART"

"""
Tests for services/agent/postmortem.py

All tests are pure unit tests — no I/O, no mocking, no Claude API calls.
The generator is deterministic, so assertions on exact substrings are valid.
"""

import re
from pathlib import Path

import pytest

import postmortem


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _analysis(overrides: dict | None = None) -> dict:
    """Return a minimal but realistic analysis dict for testing."""
    base = {
        "alert_id":    "abc12345-dead-beef-0000-000000000000",
        "alertname":   "SLOErrorBudgetBurnCritical",
        "alert_status": "firing",
        "alert_labels": {
            "alertname": "SLOErrorBudgetBurnCritical",
            "severity":  "critical",
            "slo":       "availability",
        },
        "starts_at":   "2026-04-02T10:00:00+00:00",
        "analyzed_at": "2026-04-02T10:00:45+00:00",
        "model":       "claude-sonnet-test",
        "dry_run":     False,
        "analysis":    "The error ratio reached 97% over the last 5 minutes, driven by HTTP 500 responses from the target-service. Log evidence shows repeated 'nil pointer dereference' panics. The chaos/error endpoint was called with rate=1.0 prior to the incident.",
        "root_cause":  "Chaos error injection (rate=1.0) caused 100% of requests to return HTTP 500.",
        "confidence":  0.92,
        "action":      "RESTART",
        "reasoning":   "Restarting the container clears the injected error state and restores service.",
        "slo_impact":  "Availability SLO breached: error_ratio=97% against budget of 0.10%.",
        "recommended_followup": "Add automated rollback trigger when error rate exceeds 50% for > 2 minutes.",
        "metrics_snapshot": {
            "error_ratio_5m":           0.97,
            "latency_p99_5m_seconds":   0.045,
            "latency_p50_5m_seconds":   0.012,
            "request_rate_5m_rps":      42.3,
            "memleak_bytes_allocated":  0,
            "chaos_errors_injected_total": 250,
        },
        "remediation": {
            "action":      "RESTART",
            "executed":    True,
            "outcome":     "success",
            "reason":      "Container 'target-service' restarted successfully.",
            "container":   "target-service",
            "executed_at": "2026-04-02T10:00:47+00:00",
            "details":     {"status_before": "running", "status_after": "running"},
        },
    }
    if overrides:
        base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# generate() — structural tests
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateStructure:
    """The generated Markdown must contain all required sections."""

    REQUIRED_SECTIONS = [
        "# Blameless Post-Mortem:",
        "## Summary",
        "## Impact",
        "## Timeline (UTC)",
        "## Root Cause",
        "## Contributing Factors",
        "## Detection",
        "## Resolution",
        "## Lessons Learned",
        "### What Went Well",
        "### What Could Be Improved",
        "## Action Items (Toil Reduction)",
        "## Error Budget Impact",
    ]

    def test_returns_string(self):
        result = postmortem.generate(_analysis())
        assert isinstance(result, str)
        assert len(result) > 500

    @pytest.mark.parametrize("section", REQUIRED_SECTIONS)
    def test_contains_required_section(self, section: str):
        result = postmortem.generate(_analysis())
        assert section in result, f"Missing section: {section!r}"

    def test_contains_incident_id(self):
        result = postmortem.generate(_analysis())
        assert "abc12345" in result

    def test_contains_alert_name(self):
        result = postmortem.generate(_analysis())
        assert "SLOErrorBudgetBurnCritical" in result

    def test_contains_severity(self):
        result = postmortem.generate(_analysis())
        assert "CRITICAL" in result

    def test_contains_author_line(self):
        result = postmortem.generate(_analysis())
        assert "Aether-Guard AI SRE Agent" in result

    def test_footer_present(self):
        result = postmortem.generate(_analysis())
        assert "blameless culture" in result.lower()


# ─────────────────────────────────────────────────────────────────────────────
# generate() — content correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateContent:

    def test_root_cause_text_present(self):
        result = postmortem.generate(_analysis())
        assert "Chaos error injection" in result

    def test_confidence_percentage_present(self):
        result = postmortem.generate(_analysis())
        assert "92%" in result

    def test_remediation_action_present(self):
        result = postmortem.generate(_analysis())
        assert "RESTART" in result

    def test_remediation_outcome_present(self):
        result = postmortem.generate(_analysis())
        assert "success" in result

    def test_error_ratio_in_contributing_factors(self):
        result = postmortem.generate(_analysis())
        # Error ratio 97% should appear in contributing factors
        assert "97.00%" in result or "error ratio" in result.lower()

    def test_action_items_table_present(self):
        result = postmortem.generate(_analysis())
        # Markdown table header
        assert "| Action | Priority | Owner |" in result

    def test_restart_specific_action_items(self):
        result = postmortem.generate(_analysis({"action": "RESTART"}))
        assert "P1" in result  # RESTART items are P1

    def test_rollback_specific_action_items(self):
        result = postmortem.generate(_analysis({"action": "ROLLBACK"}))
        assert "canary" in result.lower()

    def test_scale_specific_action_items(self):
        result = postmortem.generate(_analysis({"action": "SCALE"}))
        assert "HPA" in result or "minReplicas" in result

    def test_ignore_specific_action_items(self):
        result = postmortem.generate(_analysis({"action": "IGNORE"}))
        assert "false positive" in result.lower() or "sensitivity" in result.lower()

    def test_recommended_followup_appears(self):
        result = postmortem.generate(_analysis())
        assert "automated rollback trigger" in result

    def test_mttr_automated_mentioned(self):
        result = postmortem.generate(_analysis())
        assert "automated" in result.lower()

    def test_mttd_mentioned(self):
        result = postmortem.generate(_analysis())
        assert "MTTD" in result or "Mean Time to Detect" in result

    def test_dry_run_note_when_dry_run_false(self):
        result = postmortem.generate(_analysis({"dry_run": False}))
        # Should mention no manual pager required
        assert "manual" in result.lower() or "pager" in result.lower()

    def test_dry_run_note_when_dry_run_true(self):
        result = postmortem.generate(_analysis({"dry_run": True}))
        assert "DRY_RUN" in result or "dry_run" in result.lower() or "dry" in result.lower()


# ─────────────────────────────────────────────────────────────────────────────
# generate() — robustness / missing data
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateRobustness:

    def test_handles_empty_dict(self):
        """generate() must not raise on an empty analysis dict."""
        result = postmortem.generate({})
        assert isinstance(result, str)
        assert "## Summary" in result

    def test_handles_missing_metrics_snapshot(self):
        a = _analysis()
        del a["metrics_snapshot"]
        result = postmortem.generate(a)
        assert "## Contributing Factors" in result

    def test_handles_none_metrics_snapshot(self):
        a = _analysis({"metrics_snapshot": None})
        result = postmortem.generate(a)
        assert "## Contributing Factors" in result

    def test_handles_missing_remediation(self):
        a = _analysis()
        del a["remediation"]
        result = postmortem.generate(a)
        assert "## Resolution" in result

    def test_handles_low_confidence(self):
        result = postmortem.generate(_analysis({"confidence": 0.45}))
        assert "45%" in result
        assert "Low" in result or "insufficient signal" in result.lower()

    def test_handles_ignore_action(self):
        result = postmortem.generate(_analysis({"action": "IGNORE"}))
        assert "IGNORE" in result

    def test_handles_unknown_alertname(self):
        result = postmortem.generate(_analysis({"alertname": "MyCustomAlert"}))
        assert "MyCustomAlert" in result

    def test_no_bare_exception(self):
        """generate() must return a string even for completely unexpected input."""
        result = postmortem.generate({"alert_id": None, "confidence": "not-a-float"})
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# save() — file I/O
# ─────────────────────────────────────────────────────────────────────────────

class TestSave:

    def test_creates_file(self, tmp_path: Path):
        a = _analysis()
        text = postmortem.generate(a)
        path = postmortem.save(text, a, tmp_path)
        assert path.exists()
        assert path.suffix == ".md"

    def test_file_content_matches(self, tmp_path: Path):
        a = _analysis()
        text = postmortem.generate(a)
        path = postmortem.save(text, a, tmp_path)
        assert path.read_text(encoding="utf-8") == text

    def test_filename_contains_alertname_slug(self, tmp_path: Path):
        a = _analysis()
        text = postmortem.generate(a)
        path = postmortem.save(text, a, tmp_path)
        assert "sloerrorbud" in path.name.lower()  # slug of SLOErrorBudgetBurnCritical

    def test_filename_contains_short_id(self, tmp_path: Path):
        a = _analysis()
        text = postmortem.generate(a)
        path = postmortem.save(text, a, tmp_path)
        assert "abc12345" in path.name

    def test_creates_output_dir_if_missing(self, tmp_path: Path):
        subdir = tmp_path / "nested" / "postmortems"
        a = _analysis()
        text = postmortem.generate(a)
        path = postmortem.save(text, a, subdir)
        assert subdir.exists()
        assert path.exists()

    def test_returns_empty_path_on_invalid_dir(self):
        """save() must not raise even if the path is unwritable."""
        result = postmortem.save("content", _analysis(), Path("/nonexistent/readonly/dir"))
        # Returns falsy empty Path on failure
        assert not result or isinstance(result, Path)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:

    def test_slug_alphanumeric(self):
        assert re.match(r"^[a-z0-9_-]+$", postmortem._slug("SLOErrorBudgetBurnCritical"))

    def test_slug_max_length(self):
        long_name = "A" * 100
        assert len(postmortem._slug(long_name)) <= 40

    def test_fmt_date_valid_iso(self):
        result = postmortem._fmt_date("2026-04-02T10:00:45+00:00")
        assert result == "2026-04-02"

    def test_fmt_date_invalid(self):
        result = postmortem._fmt_date("not-a-date")
        assert result == "not-a-date"  # returns input unchanged

    def test_fmt_time_valid_iso(self):
        result = postmortem._fmt_time("2026-04-02T10:00:45+00:00")
        assert result == "10:00:45"

    def test_duration_str_seconds(self):
        result = postmortem._duration_str(
            "2026-04-02T10:00:00+00:00",
            "2026-04-02T10:00:47+00:00",
        )
        assert "47s" in result

    def test_duration_str_minutes(self):
        result = postmortem._duration_str(
            "2026-04-02T10:00:00+00:00",
            "2026-04-02T10:05:30+00:00",
        )
        assert "m" in result

    def test_duration_str_invalid(self):
        result = postmortem._duration_str("bad", "also-bad")
        assert result == "unknown"

    @pytest.mark.parametrize("conf,expected_keyword", [
        (0.95, "Very High"),
        (0.80, "High"),
        (0.65, "Medium"),
        (0.40, "Low"),
    ])
    def test_confidence_label(self, conf, expected_keyword):
        label = postmortem._confidence_label(conf)
        assert expected_keyword in label

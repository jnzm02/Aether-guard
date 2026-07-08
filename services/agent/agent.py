#!/usr/bin/env python3
"""
Aether-Guard AI SRE Agent — Phase 3

Architecture:
  - Background polling loop: GET /alerts?unprocessed_only=true from listener
  - For each pending alert: build context prompt → call Claude → validate JSON
  - POST /alerts/{id}/ack back to listener with analysis result
  - Write analysis to JSONL file (feeds Phase 4 post-mortem generation)
  - FastAPI interface for health, history, and manual trigger

Environment variables (all have defaults except ANTHROPIC_API_KEY):
  ANTHROPIC_API_KEY    required  Claude API key
  LISTENER_URL         http://listener:8081
  CLAUDE_MODEL         claude-3-5-sonnet-20241022
  POLL_INTERVAL        10   (seconds between listener polls)
  CONFIDENCE_THRESHOLD 0.60 (below this → override action to IGNORE)
  ANALYSIS_LOG_PATH    /app/data/analyses.jsonl
  DRY_RUN              false (set true to skip ACK + skip file write)
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from starlette.responses import Response

from prompt import SYSTEM_PROMPT, build_user_prompt
from postmortem import generate as generate_postmortem, save as save_postmortem
from remediation import execute_action
from incident_report import build_report
from incident_storage import get_storage
import metrics
import enrichment

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic response models (used by FastAPI for Swagger schema generation)
# ─────────────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    service: str = Field(..., examples=["aether-guard/agent"])
    version: str = Field(..., examples=["1.1.0"])
    model: str = Field(..., examples=["claude-sonnet-4-5-20250929"])
    listener_url: str = Field(..., examples=["http://listener:8081"])
    poll_interval_s: int = Field(..., examples=[10])
    dry_run: bool = Field(..., examples=[False])
    api_key_set: bool = Field(..., examples=[True])
    analyses_total: int = Field(..., examples=[42])
    polls: int = Field(..., examples=[100])
    alerts_processed: int = Field(..., examples=[7])
    api_errors: int = Field(..., examples=[0])
    started_at: str = Field(..., examples=["2026-04-02T08:00:00+00:00"])


class StatsResponse(BaseModel):
    polls: int = Field(..., examples=[100])
    alerts_processed: int = Field(..., examples=[7])
    api_errors: int = Field(..., examples=[0])
    started_at: str = Field(..., examples=["2026-04-02T08:00:00+00:00"])
    analyses_total: int = Field(..., examples=[7])


class PostmortemFile(BaseModel):
    filename: str = Field(..., examples=["20260402-100045-sloerrorbud-abc12345.md"])
    path: str = Field(..., examples=["/app/data/postmortems/20260402-100045-sloerrorbud-abc12345.md"])
    size_bytes: int = Field(..., examples=[4096])
    created_at: str = Field(..., examples=["2026-04-02T10:00:45+00:00"])


class PostmortemListResponse(BaseModel):
    postmortems: list[PostmortemFile]
    total: int = Field(..., examples=[3])


class PostmortemContentResponse(BaseModel):
    filename: str | None = Field(None, examples=["20260402-100045-sloerrorbud-abc12345.md"])
    path: str | None = Field(None, examples=["/app/data/postmortems/20260402-100045-sloerrorbud-abc12345.md"])
    content: str = Field(..., description="Full Markdown content of the post-mortem document.")


class AckRequest(BaseModel):
    analysis: str = Field(..., examples=["Error ratio reached 97% driven by chaos/error endpoint."])
    action: str = Field(..., examples=["RESTART"])
    confidence: float = Field(..., ge=0.0, le=1.0, examples=[0.92])


class IncidentReportResponse(BaseModel):
    incident_id: str = Field(..., examples=["abc123def456"])
    detected_at: str = Field(..., examples=["2026-07-07T10:23:45+00:00"])
    resolved_at: str = Field(..., examples=["2026-07-07T10:25:18+00:00"])
    duration_ms: int = Field(..., examples=[93000])
    trigger: str = Field(..., examples=["SLOErrorBudgetBurnCritical"])
    matched_pattern: str = Field(..., examples=["rule:oom_kill"])
    confidence: float = Field(..., examples=[0.95])
    action_taken: str = Field(..., examples=["RESTART"])
    outcome: str = Field(..., examples=["auto_resolved"])
    severity: str = Field(..., examples=["critical"])


class IncidentListResponse(BaseModel):
    incidents: list[IncidentReportResponse]
    total: int = Field(..., examples=[42])


class OverrideRequest(BaseModel):
    override_status: str = Field(..., examples=["manual_reversal"], description="manual_reversal | manual_escalation")
    override_reason: str = Field(..., examples=["Agent restarted the wrong pod, manually rolled back"], description="Human explanation for the override")
    override_by: str = Field(default="api:unknown", examples=["api:sre_username", "slack:U12345"], description="Source:identifier tracking who triggered the override")


class AlertmanagerWebhookPayload(BaseModel):
    """Alertmanager webhook payload schema (Priority 3 — direct webhook integration)"""
    receiver: str = Field(default="", examples=["aether-guard"])
    status: str = Field(..., examples=["firing", "resolved"])
    alerts: list[dict[str, Any]] = Field(..., description="Array of alerts from Alertmanager")
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str = Field(default="", examples=["http://alertmanager:9093"])
    version: str = Field(default="4")
    groupKey: str = Field(default="")


# ─────────────────────────────────────────────────────────────────────────────
# Logging (must be before V2 imports)
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger("aether-guard.agent")

# ─────────────────────────────────────────────────────────────────────────────
# V2 Architecture: Load hybrid RCA components
# ─────────────────────────────────────────────────────────────────────────────
V2_ENABLED = False
_rule_engine = None
_incident_recorder = None

try:
    from rules import RuleEngine
    from policy import check_policy
    from verification import VerificationEngine
    V2_ENABLED = True
    _rule_engine = RuleEngine()
    log.info("✨ V2 components loaded: Hybrid RCA + Policy + Verification + Replay")
except ImportError as e:
    log.warning("⚠️  V2 components not available: %s — falling back to V1 behavior", e)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
LISTENER_URL         = os.getenv("LISTENER_URL",         "http://listener:8081")
CLAUDE_MODEL         = os.getenv("CLAUDE_MODEL",         "claude-sonnet-4-5-20250929")
POLL_INTERVAL        = int(os.getenv("POLL_INTERVAL",    "10"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.60"))
ANALYSIS_LOG_PATH    = Path(os.getenv("ANALYSIS_LOG_PATH", "/app/data/analyses.jsonl"))
POSTMORTEM_DIR       = Path(os.getenv("POSTMORTEM_DIR",    "/app/data/postmortems"))
DRY_RUN              = os.getenv("DRY_RUN", "false").lower() == "true"

VALID_ACTIONS = {"RESTART", "SCALE", "ROLLBACK", "IGNORE"}

# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────
analyses: list[dict[str, Any]] = []   # in-memory history (Phase 4 reads this)

# Priority 3: Webhook deduplication cache
# Maps fingerprint -> timestamp of last processing (for 5min dedup window)
# LIMITATION: In-memory cache resets on agent restart. This is acceptable for single-instance
# deployments. For multi-instance setups, consider migrating to Redis with TTL or using
# Postgres incident_id lookup with DISTINCT ON fingerprint + recent timestamp filter.
_webhook_dedup_cache: dict[str, datetime] = {}
_webhook_stats = {
    "webhooks_received": 0,
    "alerts_from_webhook": 0,
    "alerts_deduplicated": 0,
    "resolved_alerts_received": 0,
}

_stats = {
    "polls":           0,
    "alerts_processed": 0,
    "api_errors":      0,
    "started_at":      datetime.now(timezone.utc).isoformat(),
}

# ─────────────────────────────────────────────────────────────────────────────
# Claude client  (lazy init after startup validation)
# ─────────────────────────────────────────────────────────────────────────────
_claude: anthropic.AsyncAnthropic | None = None


def get_claude() -> anthropic.AsyncAnthropic:
    global _claude
    if _claude is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. "
                "Export it before starting the agent."
            )
        _claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _claude


# ─────────────────────────────────────────────────────────────────────────────
# Claude interaction
# ─────────────────────────────────────────────────────────────────────────────

async def call_claude(user_prompt: str, attempt: int = 1) -> dict[str, Any]:
    """
    Call the Claude API and return the parsed JSON analysis.

    Retry strategy:
      Attempt 1: normal call
      Attempt 2: add explicit "return ONLY JSON" reminder (handles minor hallucinations)
      Attempt 3: raise → alert will be skipped this poll cycle

    Returns a validated dict matching the agent output schema.
    """
    client = get_claude()

    messages = [{"role": "user", "content": user_prompt}]
    if attempt == 2:
        messages.append({
            "role": "assistant",
            "content": "{"          # prime the JSON object open brace
        })

    log.info("Calling Claude  model=%s  attempt=%d", CLAUDE_MODEL, attempt)
    t0 = time.monotonic()

    response = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    elapsed = time.monotonic() - t0
    raw_text = response.content[0].text.strip()

    # If we primed with "{", prepend it back
    if attempt == 2 and not raw_text.startswith("{"):
        raw_text = "{" + raw_text

    log.info(
        "Claude responded  tokens_in=%d  tokens_out=%d  elapsed=%.2fs",
        response.usage.input_tokens,
        response.usage.output_tokens,
        elapsed,
    )

    return _parse_and_validate(raw_text)


def _parse_and_validate(raw: str) -> dict[str, Any]:
    """
    Parse Claude's response as JSON and validate the required schema fields.
    Raises ValueError with a descriptive message on any failure.
    """
    # Strip markdown fences if present (defensive)
    if "```" in raw:
        lines = raw.splitlines()
        raw = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Response is not valid JSON: {exc}\n---\n{raw[:500]}") from exc

    required = ["analysis", "root_cause", "confidence", "action", "reasoning"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"Response missing required fields: {missing}")

    if not isinstance(data["confidence"], (int, float)):
        raise ValueError(f"confidence must be numeric, got {type(data['confidence'])}")
    data["confidence"] = float(data["confidence"])

    if data["action"] not in VALID_ACTIONS:
        raise ValueError(
            f"action must be one of {VALID_ACTIONS}, got {data['action']!r}"
        )

    # Safety gate: low confidence → force IGNORE regardless of model output
    if data["confidence"] < CONFIDENCE_THRESHOLD and data["action"] != "IGNORE":
        log.warning(
            "Confidence %.2f below threshold %.2f — overriding action %s → IGNORE",
            data["confidence"], CONFIDENCE_THRESHOLD, data["action"],
        )
        data["action"] = "IGNORE"
        data["reasoning"] += (
            f"  [Agent override: confidence {data['confidence']:.2f} < "
            f"threshold {CONFIDENCE_THRESHOLD:.2f} — action downgraded to IGNORE]"
        )

    return data


async def analyze_alert(alert: dict) -> dict[str, Any]:
    """
    V2 Hybrid RCA pipeline for a single alert:
      1. Try rule-based triage first (fast, deterministic)
      2. If no rule matches or confidence <0.85 → LLM analysis
      3. Assemble enriched analysis record

    V1 compatibility: Falls back to pure LLM if V2 components not available.
    """
    alert_id   = alert["id"]
    alertname  = alert.get("labels", {}).get("alertname", "unknown")
    log.info("Analyzing alert  id=%s  alertname=%s", alert_id, alertname)

    raw_analysis: dict[str, Any] | None = None
    rca_method = "unknown"

    # ────────────────────────────────────────────────────────────────────────
    # V2: Try rule-based triage first
    # ────────────────────────────────────────────────────────────────────────
    if V2_ENABLED and _rule_engine:
        try:
            metrics = alert.get("prometheus_snapshot", {})
            logs = alert.get("logs", [])

            rule_match = await _rule_engine.analyze(alert, metrics, logs)

            if rule_match and rule_match.confidence >= 0.85:
                log.info(
                    "✓ Rule matched: %s (confidence %.2f) → %s",
                    rule_match.rule_name,
                    rule_match.confidence,
                    rule_match.recommended_action,
                )
                raw_analysis = {
                    "analysis": f"Pattern matched: {rule_match.rule_name}",
                    "root_cause": rule_match.root_cause.value,
                    "confidence": rule_match.confidence,
                    "action": rule_match.recommended_action,
                    "reasoning": rule_match.reasoning,
                    "slo_impact": alert.get("annotations", {}).get("description", ""),
                    "recommended_followup": "Monitor metrics post-remediation",
                    "evidence": rule_match.evidence,
                    "rule_name": rule_match.rule_name,
                }
                rca_method = "rule-based"
            elif rule_match:
                log.info(
                    "⚠ Rule matched but confidence %.2f < 0.85: %s — escalating to LLM",
                    rule_match.confidence,
                    rule_match.rule_name,
                )
        except Exception as exc:
            log.warning("Rule engine failed: %s — falling back to LLM", exc)

    # ────────────────────────────────────────────────────────────────────────
    # V1/V2: LLM analysis (fallback or primary path)
    # ────────────────────────────────────────────────────────────────────────
    if raw_analysis is None:
        if V2_ENABLED:
            log.info("⚡ No high-confidence rule match — using LLM analysis")
        user_prompt = build_user_prompt(alert)

        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                raw_analysis = await call_claude(user_prompt, attempt=attempt)
                rca_method = "llm-assisted"
                break
            except ValueError as exc:
                last_error = exc
                log.warning("Parse attempt %d failed: %s", attempt, exc)
                await asyncio.sleep(1)
            except anthropic.RateLimitError as exc:
                last_error = exc
                wait = 30
                log.warning("Rate limited — waiting %ds", wait)
                await asyncio.sleep(wait)
            except anthropic.APIError as exc:
                last_error = exc
                log.error("Claude API error (attempt %d): %s", attempt, exc)
                _stats["api_errors"] += 1
                await asyncio.sleep(5)

        if raw_analysis is None:
            # All attempts failed — produce a safe fallback record
            log.error("All Claude attempts failed for alert %s: %s", alert_id, last_error)
            raw_analysis = {
                "analysis":             f"Agent failed to produce analysis after 3 attempts: {last_error}",
                "root_cause":           "Unknown — analysis failed",
                "confidence":           0.0,
                "action":               "IGNORE",
                "reasoning":            "Defaulting to IGNORE due to analysis failure.",
                "slo_impact":           "unknown",
                "recommended_followup": "Investigate manually — agent could not complete RCA.",
            }
            rca_method = "fallback-error"

    return {
        **raw_analysis,
        "alert_id":      alert_id,
        "alertname":     alertname,
        "alert_status":  alert.get("status"),
        "alert_labels":  alert.get("labels", {}),
        "analyzed_at":   datetime.now(timezone.utc).isoformat(),
        "model":         CLAUDE_MODEL,
        "dry_run":       DRY_RUN,
        "rca_method":    rca_method,  # NEW: Track which path was used
        "v2_enabled":    V2_ENABLED,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Listener API client
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_pending_alerts(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(
        f"{LISTENER_URL}/alerts",
        params={"unprocessed_only": "true"},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json().get("alerts", [])


async def ack_alert(
    client: httpx.AsyncClient,
    alert_id: str,
    analysis: dict,
) -> None:
    payload = {
        "analysis": analysis.get("analysis"),
        "action":   analysis.get("action"),
        "confidence": analysis.get("confidence"),
    }
    resp = await client.post(
        f"{LISTENER_URL}/alerts/{alert_id}/ack",
        json=payload,
        timeout=10.0,
    )
    resp.raise_for_status()
    log.info("ACKed alert  id=%s  action=%s  confidence=%.2f",
             alert_id, analysis["action"], analysis["confidence"])


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

def persist_analysis(analysis: dict) -> None:
    """
    [DEPRECATED] Append analysis to JSONL file for Phase 4 post-mortem generation.

    This is kept as a belt-and-suspenders fallback in case Postgres write path fails.
    Will be removed once Postgres storage proves stable (Priority 1 implementation).
    """
    if DRY_RUN:
        return
    try:
        ANALYSIS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ANALYSIS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(analysis, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("Could not write analysis to %s: %s", ANALYSIS_LOG_PATH, exc)


def load_analyses_from_disk() -> list[dict]:
    """Reload persisted analyses from JSONL on startup."""
    if not ANALYSIS_LOG_PATH.exists():
        return []
    records = []
    try:
        with ANALYSIS_LOG_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        log.info("Loaded %d analyses from %s", len(records), ANALYSIS_LOG_PATH)
    except Exception as exc:
        log.warning("Could not load analyses from disk: %s", exc)
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Alert processing pipeline (shared by polling and webhook)
# ─────────────────────────────────────────────────────────────────────────────

async def _process_single_alert(alert: dict[str, Any], source: str = "poll") -> None:
    """
    Process a single alert through the full RCA → safety pipeline → incident report flow.

    This function is used by both:
      - Polling loop (source="poll")
      - Webhook handler (source="webhook")

    Args:
        alert: Enriched alert dict with metrics_snapshot, log_tail, etc.
        source: "poll" or "webhook" (for logging purposes)
    """
    try:
        analysis = await analyze_alert(alert)

        # ────────────────────────────────────────────────────────────
        # V2: Policy Gate - Check if action is allowed
        # ────────────────────────────────────────────────────────────
        if V2_ENABLED and analysis["action"] != "IGNORE":
            try:
                severity = alert.get("labels", {}).get("severity", "warning")
                root_cause = analysis.get("root_cause", "unknown")

                policy_decision = check_policy(
                    action=analysis["action"],
                    alert_severity=severity,
                    root_cause=root_cause,
                )

                if not policy_decision.allowed:
                    log.warning(
                        "❌ Policy violation: %s — overriding %s → IGNORE",
                        policy_decision.reason,
                        analysis["action"],
                    )
                    analysis["action"] = "IGNORE"
                    analysis["reasoning"] += f"\n[Policy blocked: {policy_decision.reason}]"
                    analysis["policy_blocked"] = True

                elif policy_decision.requires_approval:
                    log.warning(
                        "⏸️  High-risk action %s requires approval (%s) — proceeding in 5s for demo",
                        analysis["action"],
                        policy_decision.risk_level.name,
                    )
                    await asyncio.sleep(5)
                    analysis["approval_required"] = True
                    analysis["approval_status"] = "auto-approved-demo"

                analysis["policy_decision"] = {
                    "allowed": policy_decision.allowed,
                    "risk_level": policy_decision.risk_level.name,
                    "requires_approval": policy_decision.requires_approval,
                    "reason": policy_decision.reason,
                }

            except Exception as policy_exc:
                log.warning("Policy engine failed: %s — allowing action", policy_exc)

        # ────────────────────────────────────────────────────────────
        # V2: Capture metrics BEFORE remediation (for verification)
        # ────────────────────────────────────────────────────────────
        metrics_before = None
        if V2_ENABLED and analysis["action"] != "IGNORE":
            try:
                async with VerificationEngine() as verifier:
                    metrics_before = await verifier.capture_snapshot()
                    log.info("📊 Captured metrics snapshot before remediation")
            except Exception as verify_exc:
                log.warning("Metric capture failed: %s — proceeding without verification", verify_exc)

        # ────────────────────────────────────────────────────────────
        # Execute remediation action
        # ────────────────────────────────────────────────────────────
        remediation = execute_action(analysis["action"], analysis)
        analysis["remediation"] = remediation.as_dict()
        log.info(
            "Remediation  action=%s  outcome=%s  container=%s",
            remediation.action, remediation.outcome, remediation.container,
        )

        # ────────────────────────────────────────────────────────────
        # V2: Verify remediation outcome (did metrics improve?)
        # ────────────────────────────────────────────────────────────
        if V2_ENABLED and metrics_before and remediation.executed:
            try:
                async with VerificationEngine() as verifier:
                    alert_type = alert.get("labels", {}).get("alertname", "")
                    verification = await verifier.verify(
                        metrics_before=metrics_before,
                        alert_type=alert_type,
                        wait_seconds=120,
                    )

                    analysis["verification"] = {
                        "success": verification.success,
                        "improved": verification.improved,
                        "reason": verification.reason,
                        "should_rollback": verification.should_rollback,
                        "metrics_before": {
                            "error_rate": metrics_before.error_rate_5m,
                            "p99_latency": metrics_before.latency_p99_5m,
                        },
                        "metrics_after": {
                            "error_rate": verification.metrics_after.error_rate_5m,
                            "p99_latency": verification.metrics_after.latency_p99_5m,
                        },
                    }

                    if verification.should_rollback:
                        log.error(
                            "❌ Verification failed: %s — executing auto-rollback",
                            verification.reason,
                        )
                        rollback_result = execute_action("ROLLBACK", analysis)
                        analysis["auto_rollback"] = rollback_result.as_dict()
                        log.info("🔄 Auto-rollback executed: %s", rollback_result.outcome)
                    else:
                        log.info("✅ Verification successful: %s", verification.reason)

            except Exception as verify_exc:
                log.error("Verification failed: %s", verify_exc)
                analysis["verification_error"] = str(verify_exc)

        analyses.append(analysis)
        persist_analysis(analysis)

        # ────────────────────────────────────────────────────────────
        # Priority 1: Store structured incident report
        # ────────────────────────────────────────────────────────────
        if not DRY_RUN:
            try:
                incident_report = build_report(analysis)
                async with get_storage() as storage:
                    await storage.save(incident_report)
                log.info("📊 Incident report stored: %s → %s",
                        incident_report.incident_id[:12],
                        incident_report.outcome)
            except Exception as storage_exc:
                log.warning("Incident report storage failed: %s", storage_exc)

        # ────────────────────────────────────────────────────────────
        # Auto-generate blameless post-mortem
        # ────────────────────────────────────────────────────────────
        try:
            pm_text = generate_postmortem(analysis)
            pm_path = save_postmortem(pm_text, analysis, POSTMORTEM_DIR)
            if pm_path:
                log.info("📄 Post-mortem written: %s", pm_path)
                analysis["postmortem_path"] = str(pm_path)
        except Exception as pm_exc:
            log.warning("Post-mortem generation failed: %s", pm_exc)

        _stats["alerts_processed"] += 1

        log.info(
            "✅ Analysis complete  source=%s  alertname=%s  action=%s  confidence=%.2f  rca_method=%s",
            source,
            analysis["alertname"],
            analysis["action"],
            analysis["confidence"],
            analysis.get("rca_method", "unknown"),
        )

    except Exception as exc:
        log.error("Failed to process alert %s from %s: %s", alert.get("id"), source, exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Polling loop
# ─────────────────────────────────────────────────────────────────────────────

async def _poll_once(client: httpx.AsyncClient) -> int:
    """Single poll iteration. Returns number of alerts processed."""
    pending = await fetch_pending_alerts(client)
    if not pending:
        return 0

    log.info("Found %d pending alert(s)", len(pending))
    processed = 0

    for alert in pending:
        try:
            # Process alert through the shared pipeline
            await _process_single_alert(alert, source="poll")
            processed += 1

            # ACK the alert back to listener
            if not DRY_RUN:
                await ack_alert(client, alert["id"], analyses[-1])

            # Brief pause between API calls to be kind to rate limits
            await asyncio.sleep(2)

        except Exception as exc:
            log.error("Failed to process alert %s: %s", alert.get("id"), exc)

    return processed


async def polling_loop() -> None:
    """
    Background daemon: polls the listener every POLL_INTERVAL seconds.
    Designed to run forever; errors are logged and the loop continues.
    """
    log.info(
        "Polling loop started  listener=%s  interval=%ds  model=%s  dry_run=%s",
        LISTENER_URL, POLL_INTERVAL, CLAUDE_MODEL, DRY_RUN,
    )
    async with httpx.AsyncClient() as client:
        while True:
            _stats["polls"] += 1
            try:
                count = await _poll_once(client)
                if count:
                    log.info("Poll #%d: processed %d alert(s)", _stats["polls"], count)
            except httpx.ConnectError:
                log.warning(
                    "Poll #%d: listener unreachable at %s — will retry",
                    _stats["polls"], LISTENER_URL,
                )
            except Exception as exc:
                log.error("Poll #%d: unexpected error: %s", _stats["polls"], exc)
            finally:
                await asyncio.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not ANTHROPIC_API_KEY:
        log.warning(
            "⚠️  ANTHROPIC_API_KEY is not set — agent will log errors on each poll. "
            "Set the env var and restart."
        )
    else:
        log.info("ANTHROPIC_API_KEY detected — Claude client will initialise on first call")

    if DRY_RUN:
        log.info("🧪 DRY_RUN=true — alerts will be analyzed but NOT ACKed or persisted")

    # Priority 2: Populate Prometheus metrics from Postgres on startup
    try:
        async with get_storage() as storage:
            await metrics.populate_metrics_from_storage(storage)
    except Exception as exc:
        log.error("Failed to populate Prometheus metrics on startup: %s", exc)

    asyncio.create_task(polling_loop())
    yield

_AGENT_DESCRIPTION = """
## Autonomous SRE AI Agent

Aether-Guard continuously monitors `aether-guard/target-service`, performs
AI-powered Root Cause Analysis using **Claude**, executes automated remediations
(restart / scale / rollback), and generates **blameless post-mortems** — all
without human intervention.

### Flow
```
Alertmanager webhook → Listener → Agent polls → Claude RCA → Remediation → Post-Mortem
```

### Golden Signals monitored
| Signal | Metric |
|--------|--------|
| Latency | `aether_guard_http_request_duration_seconds` p99 |
| Traffic | `aether_guard_http_requests_total` |
| Errors | error ratio (5m window) |
| Saturation | heap bytes, goroutine count |

### Safety gates
1. **Confidence threshold** — action only executes if confidence ≥ per-action minimum
2. **Cooldown** — same container cannot be actioned more than once per 5 minutes
3. **Dry-run** — `DRY_RUN=true` logs intent without touching Docker
"""

_TAGS_METADATA = [
    {
        "name": "observability",
        "description": "Health checks and runtime statistics for the agent itself.",
    },
    {
        "name": "analyses",
        "description": "AI Root Cause Analysis records produced by Claude for each alert.",
    },
    {
        "name": "incidents",
        "description": (
            "Structured incident reports with queryable outcomes, patterns, and verification results. "
            "Stored in Postgres for long-term analytics and trust metrics (Priority 1)."
        ),
    },
    {
        "name": "postmortems",
        "description": (
            "Auto-generated blameless post-mortem Markdown documents. "
            "Each remediation automatically triggers a post-mortem."
        ),
    },
    {
        "name": "admin",
        "description": "Manual triggers and operational controls.",
    },
]

app = FastAPI(
    title="Aether-Guard AI SRE Agent",
    description=_AGENT_DESCRIPTION,
    version="1.1.0",
    contact={
        "name": "Aether-Guard on GitHub",
        "url": "https://github.com/jnzm02/Aether-guard",
    },
    license_info={"name": "MIT"},
    openapi_tags=_TAGS_METADATA,
    lifespan=lifespan,
)


@app.get(
    "/health",
    tags=["observability"],
    summary="Agent health check",
    response_model=HealthResponse,
    responses={200: {"description": "Agent is running and configuration is valid"}},
)
async def health():
    return {
        "status":           "ok",
        "service":          "aether-guard/agent",
        "version":          "1.1.0",
        "model":            CLAUDE_MODEL,
        "listener_url":     LISTENER_URL,
        "poll_interval_s":  POLL_INTERVAL,
        "dry_run":          DRY_RUN,
        "api_key_set":      bool(ANTHROPIC_API_KEY),
        "analyses_total":   len(analyses),
        **_stats,
    }


@app.get(
    "/analyses",
    tags=["analyses"],
    summary="List recent RCA analyses",
    responses={
        200: {
            "description": "Array of analysis records produced by Claude, newest last",
            "content": {
                "application/json": {
                    "example": {
                        "analyses": [
                            {
                                "alert_id": "abc12345",
                                "alertname": "SLOErrorBudgetBurnCritical",
                                "action": "RESTART",
                                "confidence": 0.92,
                                "analyzed_at": "2026-04-02T10:00:45+00:00",
                            }
                        ],
                        "total": 1,
                    }
                }
            },
        }
    },
)
async def list_analyses(limit: int = 50):
    """Return the most recent `limit` analyses produced by this agent."""
    return {
        "analyses": analyses[-limit:],
        "total":    len(analyses),
    }


@app.get(
    "/analyses/{alert_id}",
    tags=["analyses"],
    summary="Get analysis by alert ID",
    responses={
        200: {"description": "Full analysis record for the given alert"},
        404: {"description": "No analysis found for this alert ID"},
    },
)
async def get_analysis(alert_id: str):
    """Fetch the analysis for a specific alert ID."""
    for a in reversed(analyses):
        if a.get("alert_id") == alert_id:
            return a
    raise HTTPException(status_code=404, detail=f"No analysis found for alert {alert_id!r}")


@app.post(
    "/analyze/{alert_id}",
    tags=["admin"],
    summary="Manually trigger RCA for a specific alert",
    responses={
        200: {"description": "Analysis result including remediation outcome"},
        404: {"description": "Alert not found in listener queue"},
    },
)
async def manually_trigger(alert_id: str, background_tasks: BackgroundTasks):
    """
    Manually trigger analysis of a specific alert from the listener queue.
    Useful for testing individual alerts without waiting for the poll cycle.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{LISTENER_URL}/alerts/{alert_id}", timeout=10.0)
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Alert {alert_id!r} not in listener queue")
        resp.raise_for_status()
        alert = resp.json()

    analysis = await analyze_alert(alert)
    analyses.append(analysis)
    persist_analysis(analysis)
    _stats["alerts_processed"] += 1

    if not DRY_RUN:
        async with httpx.AsyncClient() as client:
            await ack_alert(client, alert_id, analysis)

    return analysis


@app.get(
    "/incidents",
    tags=["incidents"],
    summary="List recent incident reports",
    response_model=IncidentListResponse,
    responses={
        200: {
            "description": "Array of structured incident reports, ordered by detected_at DESC",
            "content": {
                "application/json": {
                    "example": {
                        "incidents": [
                            {
                                "incident_id": "abc123def456",
                                "detected_at": "2026-07-07T10:23:45+00:00",
                                "resolved_at": "2026-07-07T10:25:18+00:00",
                                "duration_ms": 93000,
                                "trigger": "SLOErrorBudgetBurnCritical",
                                "matched_pattern": "rule:oom_kill",
                                "confidence": 0.95,
                                "action_taken": "RESTART",
                                "outcome": "auto_resolved",
                                "severity": "critical",
                            }
                        ],
                        "total": 42,
                    }
                }
            },
        },
        503: {"description": "Postgres unavailable — incident storage not accessible"},
    },
)
async def list_incidents(limit: int = 50):
    """
    Return the most recent `limit` incident reports from Postgres.

    Incidents are ordered by detected_at descending (most recent first).
    Each incident includes outcome taxonomy, matched pattern, and verification result.
    """
    try:
        async with get_storage() as storage:
            reports = await storage.get_recent(limit=limit)
            return IncidentListResponse(
                incidents=[
                    IncidentReportResponse(
                        incident_id=r.incident_id,
                        detected_at=r.detected_at,
                        resolved_at=r.resolved_at,
                        duration_ms=r.duration_ms,
                        trigger=r.trigger,
                        matched_pattern=r.matched_pattern,
                        confidence=r.confidence,
                        action_taken=r.action_taken,
                        outcome=r.outcome,
                        severity=r.severity,
                    )
                    for r in reports
                ],
                total=len(reports),
            )
    except Exception as exc:
        log.error("Failed to retrieve incidents from storage: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Incident storage unavailable: {exc}",
        )


@app.get(
    "/incidents/{incident_id}",
    tags=["incidents"],
    summary="Get incident report by ID",
    responses={
        200: {"description": "Full incident report with all fields including full_analysis"},
        404: {"description": "No incident found for this ID"},
        503: {"description": "Postgres unavailable — incident storage not accessible"},
    },
)
async def get_incident(incident_id: str):
    """
    Fetch the full incident report for a specific incident ID.

    Returns the complete IncidentReport including full_analysis dict for deep inspection.
    """
    try:
        async with get_storage() as storage:
            report = await storage.get_by_id(incident_id)
            if not report:
                raise HTTPException(
                    status_code=404,
                    detail=f"No incident found for ID {incident_id!r}",
                )
            return report.as_dict()
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Failed to retrieve incident %s from storage: %s", incident_id, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Incident storage unavailable: {exc}",
        )


@app.post(
    "/incidents/{incident_id}/override",
    tags=["incidents"],
    summary="Record a human override for an incident",
    responses={
        200: {"description": "Override recorded successfully"},
        400: {"description": "Invalid override_status value"},
        404: {"description": "Incident not found"},
        409: {"description": "Incident already has an override — cannot overwrite"},
        503: {"description": "Postgres unavailable — cannot record override"},
    },
)
async def record_override(incident_id: str, request: OverrideRequest):
    """
    Record that a human operator overrode or escalated the agent's decision.

    This is used for Priority 2 trust metrics: tracking when humans intervene
    to reverse the agent's action (manual_reversal) or escalate even after
    the agent reported success (manual_escalation).

    **override_status values:**
    - `manual_reversal`: Human reversed the agent's action (e.g., agent restarted pod, human rolled it back)
    - `manual_escalation`: Human escalated after agent auto-resolved (e.g., agent said "fixed", human disagrees)

    **override_by format:** Unstructured "source:identifier" string (e.g. "slack:U12345", "api:username")

    **Note:** This endpoint rejects duplicate overrides (409 Conflict) to preserve audit trail.
    Query the incident first if you need to see the existing override.

    # TODO: Add authentication before any public exposure (currently assumes local/trusted access)
    """
    # Validate override_status
    valid_statuses = {"manual_reversal", "manual_escalation"}
    if request.override_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid override_status {request.override_status!r}. Must be one of: {valid_statuses}",
        )

    try:
        async with get_storage() as storage:
            # Check if incident exists
            report = await storage.get_by_id(incident_id)
            if not report:
                raise HTTPException(
                    status_code=404,
                    detail=f"Incident {incident_id!r} not found — cannot record override",
                )

            # Prevent overwriting existing overrides (preserves audit trail)
            if report.override_status != "none":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Incident {incident_id!r} already has override_status={report.override_status!r} "
                        f"by {report.override_by!r}. Cannot overwrite existing override. "
                        f"Query GET /incidents/{incident_id} to see current override."
                    ),
                )

            # Update override fields
            success = await storage.update_override(
                incident_id=incident_id,
                override_status=request.override_status,
                override_reason=request.override_reason,
                override_by=request.override_by,
                matched_pattern=report.matched_pattern,  # For Prometheus metrics
            )

            if not success:
                raise HTTPException(
                    status_code=503,
                    detail=f"Failed to record override for incident {incident_id!r}",
                )

            return {
                "incident_id": incident_id,
                "override_status": request.override_status,
                "override_by": request.override_by,
                "message": "Override recorded successfully",
            }
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Failed to record override for %s: %s", incident_id, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Incident storage unavailable: {exc}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Priority 3: Alertmanager Webhook Integration
# ─────────────────────────────────────────────────────────────────────────────

def _should_deduplicate(fingerprint: str) -> bool:
    """
    Check if an alert should be deduplicated based on fingerprint.

    Returns True if the alert was processed within the last 5 minutes.
    """
    if not fingerprint:
        return False

    now = datetime.now(timezone.utc)
    last_processed = _webhook_dedup_cache.get(fingerprint)

    if last_processed:
        elapsed = (now - last_processed).total_seconds()
        if elapsed < 300:  # 5 minutes
            return True

    # Update cache with current timestamp
    _webhook_dedup_cache[fingerprint] = now
    return False


async def _handle_resolved_alert(alert: dict[str, Any]) -> None:
    """
    Handle a resolved alert (Priority 3 design: three scenarios).

    Scenarios:
      1. Alert resolves BEFORE remediation executes: Mark incident as ignored with remediation_outcome="alert_auto_resolved"
      2. Alert resolves AFTER remediation started but BEFORE verification completes: Let verification continue
         (we still need to know if our action helped/hurt, independent of the alert resolving)
      3. Alert resolves AFTER full pipeline completes: Treat as confirmation, no action needed
         (incident already has final outcome)

    Implementation:
      - Lookup incident by fingerprint
      - If incident found and outcome not yet final (not auto_resolved/rolled_back), mark as alert_auto_resolved
      - If incident already completed, log as confirmation but don't modify
      - If incident not found, log and continue (alert may have been deduplicated)
    """
    fingerprint = alert.get("fingerprint")
    if not fingerprint:
        log.warning("Resolved alert missing fingerprint — cannot match to incident")
        return

    alertname = alert.get("labels", {}).get("alertname", "unknown")
    resolved_at = alert.get("endsAt", datetime.now(timezone.utc).isoformat())

    try:
        async with get_storage() as storage:
            # Lookup incident by fingerprint
            incident = await storage.get_by_fingerprint(fingerprint)

            if not incident:
                log.info(
                    "Resolved alert received but no matching incident found  fingerprint=%s  alertname=%s "
                    "(may have been deduplicated or not yet created)",
                    fingerprint[:12],
                    alertname,
                )
                return

            # Scenario 3: Incident already completed (auto_resolved or rolled_back)
            if incident.outcome in ("auto_resolved", "rolled_back"):
                log.info(
                    "✅ Resolved alert confirms completed incident  incident=%s  outcome=%s  alertname=%s",
                    incident.incident_id[:12],
                    incident.outcome,
                    alertname,
                )
                return

            # Scenario 1: Alert resolved before remediation completed
            # Mark incident as alert_auto_resolved (don't execute unnecessary remediation)
            success = await storage.mark_alert_resolved(
                incident_id=incident.incident_id,
                resolved_at=resolved_at,
            )

            if success:
                log.info(
                    "⏭️  Alert auto-resolved before remediation completed  incident=%s  alertname=%s "
                    "→ outcome=ignored, remediation_outcome=alert_auto_resolved",
                    incident.incident_id[:12],
                    alertname,
                )
            else:
                # Scenario 2: Update failed because incident already completed during this function call
                # (race condition: verification finished between get_by_fingerprint and mark_alert_resolved)
                log.debug(
                    "Alert resolved update skipped for %s (incident completed concurrently)",
                    incident.incident_id[:12],
                )

    except Exception as exc:
        log.error("Failed to handle resolved alert %s: %s", fingerprint, exc)


@app.post(
    "/webhook/alertmanager",
    status_code=202,
    tags=["alertmanager"],
    summary="Receive Alertmanager webhook payload",
    responses={
        202: {"description": "Alerts received and processing started"},
        422: {"description": "Invalid payload structure"},
    },
)
async def receive_alertmanager_webhook(
    payload: AlertmanagerWebhookPayload,
    background_tasks: BackgroundTasks,
):
    """
    Alertmanager direct webhook endpoint (Priority 3).

    Receives Alertmanager webhook payloads and processes alerts through the full
    RCA → safety pipeline → incident report flow, bypassing the polling loop.

    **Benefits over polling:**
    - Zero latency (instant response to alert fires)
    - No 0-10s polling delay
    - Webhook path and polling path coexist (both funnel into same pipeline)

    **Deduplication:** 5-minute fingerprint-based cache prevents duplicate processing
    if the same alert arrives via both webhook and polling.

    **Resolved alerts:** Alerts with status="resolved" are handled per Priority 3 design:
    - If incident in-flight: mark as resolved, don't execute remediation
    - If incident complete: log and ignore

    **Payload schema:** https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
    """
    _webhook_stats["webhooks_received"] += 1

    raw_alerts = payload.alerts
    if not raw_alerts:
        log.info("Webhook received with no alerts — returning 202")
        return {"status": "accepted", "enqueued": 0, "deduplicated": 0}

    log.info(
        "Webhook received  receiver=%s  status=%s  alerts=%d",
        payload.receiver,
        payload.status,
        len(raw_alerts),
    )

    enqueued = 0
    deduplicated = 0

    for raw_alert in raw_alerts:
        fingerprint = raw_alert.get("fingerprint", "")
        alert_status = raw_alert.get("status", "unknown")
        alertname = raw_alert.get("labels", {}).get("alertname", "unknown")

        # Handle resolved alerts
        if alert_status == "resolved":
            _webhook_stats["resolved_alerts_received"] += 1
            await _handle_resolved_alert(raw_alert)
            continue

        # Check for duplicates (5min window)
        if _should_deduplicate(fingerprint):
            log.info(
                "  ↳ deduplicated  alertname=%s  fingerprint=%s (processed within last 5min)",
                alertname,
                fingerprint[:12],
            )
            _webhook_stats["alerts_deduplicated"] += 1
            deduplicated += 1
            continue

        # Enrich alert and process in background
        async def process_webhook_alert(alert: dict[str, Any]):
            try:
                enriched = await enrichment.enrich_alert(alert, generate_id=True)
                await _process_single_alert(enriched, source="webhook")
            except Exception as exc:
                log.error("Failed to process webhook alert %s: %s", alertname, exc)

        background_tasks.add_task(process_webhook_alert, raw_alert)
        enqueued += 1
        _webhook_stats["alerts_from_webhook"] += 1

        log.info(
            "  ↳ enqueued  alertname=%s  fingerprint=%s  status=%s",
            alertname,
            fingerprint[:12] if fingerprint else "none",
            alert_status,
        )

    return {
        "status": "accepted",
        "enqueued": enqueued,
        "deduplicated": deduplicated,
        "resolved": _webhook_stats["resolved_alerts_received"],
    }


@app.get(
    "/stats",
    tags=["observability"],
    summary="Agent runtime statistics",
    response_model=StatsResponse,
)
async def get_stats():
    return {**_stats, "analyses_total": len(analyses)}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Post-Mortem endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/postmortems",
    tags=["postmortems"],
    summary="List all saved post-mortem documents",
    response_model=PostmortemListResponse,
    responses={200: {"description": "List of post-mortem Markdown files, newest first"}},
)
async def list_postmortems():
    """List all saved post-mortem files, newest first."""
    if not POSTMORTEM_DIR.exists():
        return {"postmortems": [], "total": 0}
    files = sorted(POSTMORTEM_DIR.glob("*.md"), reverse=True)
    return {
        "postmortems": [
            {
                "filename": f.name,
                "path":     str(f),
                "size_bytes": f.stat().st_size,
                "created_at": datetime.fromtimestamp(f.stat().st_ctime, tz=timezone.utc).isoformat(),
            }
            for f in files
        ],
        "total": len(files),
    }


@app.get(
    "/postmortems/{filename}",
    tags=["postmortems"],
    summary="Read a post-mortem document",
    response_model=PostmortemContentResponse,
    responses={
        200: {"description": "Post-mortem Markdown content"},
        404: {"description": "File not found"},
    },
)
async def get_postmortem(filename: str):
    """Read a saved post-mortem file by filename."""
    # Prevent path traversal
    safe = Path(filename).name
    path = POSTMORTEM_DIR / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Post-mortem {safe!r} not found.")
    return {"filename": safe, "content": path.read_text(encoding="utf-8")}


@app.post(
    "/postmortems/generate/{alert_id}",
    tags=["postmortems"],
    summary="(Re-)generate post-mortem for an alert",
    response_model=PostmortemContentResponse,
    responses={
        200: {"description": "Freshly generated post-mortem content"},
        404: {"description": "No analysis found for this alert ID"},
    },
)
async def generate_postmortem_endpoint(alert_id: str):
    """
    (Re-)generate a blameless post-mortem for a specific alert ID.
    Uses the deterministic generator — no extra LLM call required.
    """
    incident = [a for a in analyses if a.get("alert_id") == alert_id]
    if not incident:
        raise HTTPException(status_code=404, detail=f"No analysis found for alert {alert_id!r}")

    analysis = incident[-1]
    pm_text  = generate_postmortem(analysis)
    pm_path  = save_postmortem(pm_text, analysis, POSTMORTEM_DIR)

    return {
        "filename": pm_path.name if pm_path else None,
        "path":     str(pm_path) if pm_path else None,
        "content":  pm_text,
    }


@app.get(
    "/postmortems/latest/raw",
    tags=["postmortems"],
    summary="Get the most recent post-mortem",
    responses={
        200: {"description": "Post-mortem content for the most recent analysis"},
        404: {"description": "No analyses processed yet"},
    },
)
async def latest_postmortem_raw():
    """Return the most recently generated post-mortem, or generate one on demand."""
    if not analyses:
        raise HTTPException(status_code=404, detail="No analyses available yet.")
    analysis = analyses[-1]
    pm_text  = generate_postmortem(analysis)
    return {"content": pm_text, "alert_id": analysis.get("alert_id")}


@app.get(
    "/metrics",
    tags=["observability"],
    summary="Prometheus metrics endpoint",
    responses={
        200: {
            "description": "Prometheus metrics in text exposition format",
            "content": {"text/plain; version=0.0.4": {}},
        }
    },
)
async def prometheus_metrics():
    """
    Expose Prometheus metrics for scraping by Prometheus server.

    Provides trust metrics for agent reliability:
      - aether_guard_incidents_total{matched_pattern, outcome}
      - aether_guard_overrides_total{matched_pattern, override_status}

    These metrics are used to compute override rates and identify which RCA
    patterns need improvement (Priority 2 trust metrics).
    """
    return Response(
        content=metrics.get_metrics_text(),
        media_type=metrics.get_metrics_content_type(),
    )

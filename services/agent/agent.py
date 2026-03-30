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
from fastapi.responses import JSONResponse

from prompt import SYSTEM_PROMPT, build_user_prompt
from remediation import execute_action, RemediationResult

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger("aether-guard.agent")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
LISTENER_URL         = os.getenv("LISTENER_URL",         "http://listener:8081")
CLAUDE_MODEL         = os.getenv("CLAUDE_MODEL",         "claude-sonnet-4-5-20250929")
POLL_INTERVAL        = int(os.getenv("POLL_INTERVAL",    "10"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.60"))
ANALYSIS_LOG_PATH    = Path(os.getenv("ANALYSIS_LOG_PATH", "/app/data/analyses.jsonl"))
DRY_RUN              = os.getenv("DRY_RUN", "false").lower() == "true"

VALID_ACTIONS = {"RESTART", "SCALE", "ROLLBACK", "IGNORE"}

# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────
analyses: list[dict[str, Any]] = []   # in-memory history (Phase 4 reads this)

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
            l for l in lines
            if not l.strip().startswith("```")
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
    Full analysis pipeline for a single alert:
      1. Build context prompt
      2. Call Claude (with retry)
      3. Assemble enriched analysis record
    """
    alert_id   = alert["id"]
    alertname  = alert.get("labels", {}).get("alertname", "unknown")
    log.info("Analyzing alert  id=%s  alertname=%s", alert_id, alertname)

    user_prompt = build_user_prompt(alert)

    raw_analysis: dict[str, Any] | None = None
    last_error: Exception | None = None

    for attempt in range(1, 4):
        try:
            raw_analysis = await call_claude(user_prompt, attempt=attempt)
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

    return {
        **raw_analysis,
        "alert_id":      alert_id,
        "alertname":     alertname,
        "alert_status":  alert.get("status"),
        "alert_labels":  alert.get("labels", {}),
        "analyzed_at":   datetime.now(timezone.utc).isoformat(),
        "model":         CLAUDE_MODEL,
        "dry_run":       DRY_RUN,
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
    """Append analysis to JSONL file for Phase 4 post-mortem generation."""
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
            analysis = await analyze_alert(alert)

            # ── Phase 4: Execute remediation action ───────────────────────
            remediation = execute_action(analysis["action"], analysis)
            analysis["remediation"] = remediation.as_dict()
            log.info(
                "Remediation  action=%s  outcome=%s  container=%s",
                remediation.action, remediation.outcome, remediation.container,
            )

            analyses.append(analysis)
            persist_analysis(analysis)
            _stats["alerts_processed"] += 1
            processed += 1

            log.info(
                "✅ Analysis complete  alertname=%s  action=%s  confidence=%.2f",
                analysis["alertname"],
                analysis["action"],
                analysis["confidence"],
            )

            if not DRY_RUN:
                await ack_alert(client, alert["id"], analysis)

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

    asyncio.create_task(polling_loop())
    yield
app = FastAPI(
    title="Aether-Guard AI SRE Agent",
    description="Phase 3 — autonomous alert analysis with Claude",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {
        "status":           "ok",
        "service":          "aether-guard/agent",
        "version":          "1.0.0",
        "model":            CLAUDE_MODEL,
        "listener_url":     LISTENER_URL,
        "poll_interval_s":  POLL_INTERVAL,
        "dry_run":          DRY_RUN,
        "api_key_set":      bool(ANTHROPIC_API_KEY),
        "analyses_total":   len(analyses),
        **_stats,
    }


@app.get("/analyses")
async def list_analyses(limit: int = 50):
    """Return the most recent `limit` analyses produced by this agent."""
    return {
        "analyses": analyses[-limit:],
        "total":    len(analyses),
    }


@app.get("/analyses/{alert_id}")
async def get_analysis(alert_id: str):
    """Fetch the analysis for a specific alert ID."""
    for a in reversed(analyses):
        if a.get("alert_id") == alert_id:
            return a
    raise HTTPException(status_code=404, detail=f"No analysis found for alert {alert_id!r}")


@app.post("/analyze/{alert_id}")
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


@app.get("/stats")
async def get_stats():
    return {**_stats, "analyses_total": len(analyses)}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Post-Mortem generation
# ─────────────────────────────────────────────────────────────────────────────

_PM_SYSTEM = """\
You are a Google SRE writing a blameless post-mortem for a production incident.
Your audience is senior engineers doing a weekly post-mortem review.

Rules:
- Blameless: focus on systems and processes, never individuals.
- Specific: cite exact metric values, timestamps, and log evidence.
- Actionable: every lesson must translate to a concrete action item.
- Concise: each section should be 2–5 sentences unless a timeline or table.

Output ONLY the Markdown document — no preamble, no code fences.\
"""

def _build_pm_prompt(incident_analyses: list[dict]) -> str:
    """Serialize incident analyses into a structured prompt for PM generation."""
    first  = incident_analyses[0]
    last   = incident_analyses[-1]
    labels = first.get("alert_labels", {})

    metric_lines = []
    snap = first.get("metrics_snapshot") or {}
    for k, v in snap.items():
        metric_lines.append(f"  {k}: {v}")

    remediation = first.get("remediation", {})
    action_line = (
        f"{remediation.get('action','N/A')} → {remediation.get('outcome','N/A')}: "
        f"{remediation.get('reason','')}"
        if remediation else "No remediation record"
    )

    return f"""\
## Incident Data

Alert Name   : {labels.get('alertname','unknown')}
Severity     : {labels.get('severity','unknown')}
SLO Impacted : {labels.get('slo','unknown')}
Service      : {labels.get('service','target-service')}
Started At   : {first.get('starts_at','unknown')}
Analyzed At  : {first.get('analyzed_at','unknown')}

## AI RCA Summary

Root Cause   : {first.get('root_cause','N/A')}
Confidence   : {first.get('confidence',0):.0%}
Recommended Action: {first.get('action','N/A')}
Reasoning    : {first.get('reasoning','N/A')}
SLO Impact   : {first.get('slo_impact','N/A')}

## Prometheus Metrics at Alert Time

{chr(10).join(metric_lines) if metric_lines else '  (no snapshot)'}

## Remediation Executed

{action_line}

## Log Evidence (excerpt)

{chr(10).join((first.get('log_tail') or [])[-20:])}

## Full Analysis

{first.get('analysis','N/A')}

## Task

Write a complete blameless post-mortem with these sections:
# Blameless Post-Mortem: [descriptive incident title]
**Date:** [extracted from analyzed_at]
**Status:** Complete — Closed
**Severity:** [from alert]
**Author:** Aether-Guard AI SRE Agent

## Summary
## Impact
## Timeline (UTC)
## Root Cause
## Contributing Factors
## Resolution
## Lessons Learned
### What Went Well
### What Could Be Improved
## Action Items (Toil Reduction)
| Action | Priority | Owner |
## Error Budget Impact
"""


async def _generate_postmortem_text(incident_analyses: list[dict]) -> str:
    """Call Claude to write the post-mortem narrative."""
    client = get_claude()
    prompt = _build_pm_prompt(incident_analyses)
    response = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=_PM_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


@app.post("/postmortem/{alert_id}")
async def generate_postmortem(alert_id: str):
    """
    Generate a blameless post-mortem Markdown document for a specific alert.
    Finds the analysis in the in-memory queue, calls Claude, writes file + returns text.
    """
    incident = [a for a in analyses if a.get("alert_id") == alert_id]
    if not incident:
        raise HTTPException(status_code=404, detail=f"No analysis found for alert {alert_id!r}")

    pm_text  = await _generate_postmortem_text(incident)
    alertname = incident[0].get("alertname", "incident")
    ts        = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename  = f"{ts}-{alertname}.md"

    # Write to local file (persisted via volume in Docker).
    pm_dir = Path("/app/data/postmortems")
    pm_dir.mkdir(parents=True, exist_ok=True)
    outpath = pm_dir / filename
    outpath.write_text(pm_text, encoding="utf-8")
    log.info("Post-mortem written: %s", outpath)

    return {"filename": filename, "path": str(outpath), "content": pm_text}


@app.get("/postmortem/latest")
async def latest_postmortem():
    """Generate a post-mortem for the most recently analyzed alert."""
    if not analyses:
        raise HTTPException(status_code=404, detail="No analyses available yet.")
    latest = analyses[-1]
    return await generate_postmortem(latest["alert_id"])

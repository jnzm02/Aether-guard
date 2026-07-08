"""
Aether-Guard — Incident Report Storage Layer

Dual storage strategy:
  - Redis: 48h TTL for recent incidents (fast dashboard queries)
  - Postgres: Long-term history for analytics and trust metrics

Design:
  - Redis: Simple key-value, JSON serialization
  - Postgres: Full schema with indexes for matched_pattern, outcome, detected_at
  - Auto-creates Postgres table on first connect
  - Graceful degradation if either storage unavailable
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import redis.asyncio as aioredis
import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from incident_report import IncidentReport
import metrics

log = logging.getLogger("aether-guard.agent.storage")

# Environment variables
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
POSTGRES_URL = os.environ.get("POSTGRES_URL", "postgresql://aether_guard:local_dev_password@postgres:5432/aether_guard")

# Redis TTL: 48 hours (172800 seconds)
REDIS_TTL_SECONDS = 172800

# ── Postgres Schema ───────────────────────────────────────────────────────────

_SCHEMA_DDL = """
-- Incident Reports Table
-- Optimized for querying by matched_pattern, outcome, detected_at (trust metrics)

CREATE TABLE IF NOT EXISTS incident_reports (
    -- Primary key
    incident_id VARCHAR(255) PRIMARY KEY,

    -- Timestamps (indexed for time-series queries)
    detected_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL,
    duration_ms INTEGER NOT NULL,

    -- RCA (matched_pattern indexed for GROUP BY queries in Priority 2)
    trigger VARCHAR(255) NOT NULL,
    matched_pattern VARCHAR(255) NOT NULL,
    confidence FLOAT NOT NULL,
    root_cause TEXT NOT NULL,
    reasoning TEXT NOT NULL,

    -- Action
    action_taken VARCHAR(50) NOT NULL,
    remediation_outcome VARCHAR(100) NOT NULL,

    -- Verification
    verification_performed BOOLEAN NOT NULL,
    verification_result VARCHAR(50),
    auto_rollback_triggered BOOLEAN NOT NULL,

    -- Outcome (indexed for trust metrics)
    outcome VARCHAR(100) NOT NULL,

    -- Metadata
    rca_method VARCHAR(50) NOT NULL,
    policy_blocked BOOLEAN NOT NULL,
    approval_required BOOLEAN NOT NULL,
    severity VARCHAR(50) NOT NULL,

    -- Override tracking (Priority 2 — trust metrics)
    override_status VARCHAR(50) DEFAULT 'none' NOT NULL,
    override_reason TEXT,
    override_at TIMESTAMPTZ,
    override_by VARCHAR(255),

    -- Full analysis (JSONB for deep queries)
    full_analysis JSONB NOT NULL,

    -- Audit timestamp
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for Priority 2 trust metrics queries
CREATE INDEX IF NOT EXISTS idx_detected_at ON incident_reports (detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_matched_pattern ON incident_reports (matched_pattern);
CREATE INDEX IF NOT EXISTS idx_outcome ON incident_reports (outcome);
CREATE INDEX IF NOT EXISTS idx_matched_pattern_outcome ON incident_reports (matched_pattern, outcome);
CREATE INDEX IF NOT EXISTS idx_detected_at_pattern ON incident_reports (detected_at DESC, matched_pattern);

-- Partial index for override queries (only indexes overridden incidents for efficiency)
CREATE INDEX IF NOT EXISTS idx_override_status ON incident_reports (override_status)
    WHERE override_status != 'none';
"""


# ── Storage Engine ────────────────────────────────────────────────────────────

class IncidentStorage:
    """
    Dual-storage engine for incident reports.

    Usage:
        async with IncidentStorage() as storage:
            await storage.save(report)
            recent = await storage.get_recent(limit=50)
    """

    def __init__(self):
        self._redis: aioredis.Redis | None = None
        self._postgres: AsyncConnection | None = None
        self._redis_available = False
        self._postgres_available = False

    async def __aenter__(self) -> IncidentStorage:
        """Connect to Redis and Postgres, initialize schema."""
        # Connect to Redis
        try:
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            await self._redis.ping()
            self._redis_available = True
            log.info("Redis connected: %s", REDIS_URL)
        except Exception as exc:
            log.warning("Redis unavailable: %s — incident reports will only persist to Postgres", exc)
            self._redis_available = False

        # Connect to Postgres
        try:
            self._postgres = await psycopg.AsyncConnection.connect(
                POSTGRES_URL,
                row_factory=dict_row,
                autocommit=True,
            )
            await self._init_schema()
            self._postgres_available = True
            log.info("Postgres connected and schema initialized")
        except Exception as exc:
            log.warning("Postgres unavailable: %s — incident reports will only persist to Redis", exc)
            self._postgres_available = False

        if not self._redis_available and not self._postgres_available:
            log.error("Both Redis and Postgres unavailable — incident reports will NOT be persisted!")

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close connections."""
        if self._redis:
            await self._redis.aclose()
        if self._postgres:
            await self._postgres.close()

    async def _init_schema(self):
        """Create tables and indexes if they don't exist."""
        if not self._postgres:
            return
        async with self._postgres.cursor() as cur:
            await cur.execute(_SCHEMA_DDL)
        log.info("Postgres schema initialized")

    async def save(self, report: IncidentReport) -> None:
        """
        Save incident report to both Redis (48h TTL) and Postgres (permanent).

        Graceful degradation: Logs warning if one storage fails, but continues.
        """
        report_dict = report.as_dict()
        incident_id = report.incident_id

        # Save to Redis (48h TTL)
        if self._redis_available and self._redis:
            try:
                key = f"incident:report:{incident_id}"
                await self._redis.setex(key, REDIS_TTL_SECONDS, json.dumps(report_dict))
                log.debug("Incident %s saved to Redis (TTL=%ds)", incident_id, REDIS_TTL_SECONDS)
            except Exception as exc:
                log.warning("Failed to save incident %s to Redis: %s", incident_id, exc)

        # Save to Postgres (permanent)
        if self._postgres_available and self._postgres:
            try:
                await self._insert_postgres(report)
                log.debug("Incident %s saved to Postgres", incident_id)

                # Priority 2: Record Prometheus metrics
                metrics.record_incident(report.matched_pattern, report.outcome)
            except Exception as exc:
                log.warning("Failed to save incident %s to Postgres: %s", incident_id, exc)

    async def _insert_postgres(self, report: IncidentReport) -> None:
        """Insert incident report into Postgres (upsert on conflict)."""
        if not self._postgres:
            return

        async with self._postgres.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO incident_reports (
                    incident_id, detected_at, resolved_at, duration_ms,
                    trigger, matched_pattern, confidence, root_cause, reasoning,
                    action_taken, remediation_outcome,
                    verification_performed, verification_result, auto_rollback_triggered,
                    outcome, rca_method, policy_blocked, approval_required, severity,
                    override_status, override_reason, override_at, override_by,
                    full_analysis
                ) VALUES (
                    %(incident_id)s, %(detected_at)s, %(resolved_at)s, %(duration_ms)s,
                    %(trigger)s, %(matched_pattern)s, %(confidence)s, %(root_cause)s, %(reasoning)s,
                    %(action_taken)s, %(remediation_outcome)s,
                    %(verification_performed)s, %(verification_result)s, %(auto_rollback_triggered)s,
                    %(outcome)s, %(rca_method)s, %(policy_blocked)s, %(approval_required)s, %(severity)s,
                    %(override_status)s, %(override_reason)s, %(override_at)s, %(override_by)s,
                    %(full_analysis)s::jsonb
                )
                ON CONFLICT (incident_id) DO UPDATE SET
                    resolved_at = EXCLUDED.resolved_at,
                    duration_ms = EXCLUDED.duration_ms,
                    outcome = EXCLUDED.outcome,
                    verification_performed = EXCLUDED.verification_performed,
                    verification_result = EXCLUDED.verification_result,
                    auto_rollback_triggered = EXCLUDED.auto_rollback_triggered,
                    override_status = EXCLUDED.override_status,
                    override_reason = EXCLUDED.override_reason,
                    override_at = EXCLUDED.override_at,
                    override_by = EXCLUDED.override_by,
                    full_analysis = EXCLUDED.full_analysis
                """,
                {
                    "incident_id": report.incident_id,
                    "detected_at": report.detected_at,
                    "resolved_at": report.resolved_at,
                    "duration_ms": report.duration_ms,
                    "trigger": report.trigger,
                    "matched_pattern": report.matched_pattern,
                    "confidence": report.confidence,
                    "root_cause": report.root_cause,
                    "reasoning": report.reasoning,
                    "action_taken": report.action_taken,
                    "remediation_outcome": report.remediation_outcome,
                    "verification_performed": report.verification_performed,
                    "verification_result": report.verification_result,
                    "auto_rollback_triggered": report.auto_rollback_triggered,
                    "outcome": report.outcome,
                    "rca_method": report.rca_method,
                    "policy_blocked": report.policy_blocked,
                    "approval_required": report.approval_required,
                    "severity": report.severity,
                    "override_status": report.override_status,
                    "override_reason": report.override_reason,
                    "override_at": report.override_at,
                    "override_by": report.override_by,
                    "full_analysis": json.dumps(report.full_analysis),
                },
            )

    async def get_by_id(self, incident_id: str) -> IncidentReport | None:
        """
        Retrieve incident report by ID.

        Tries Redis first (fast), falls back to Postgres if not found.
        """
        # Try Redis first
        if self._redis_available and self._redis:
            try:
                key = f"incident:report:{incident_id}"
                data = await self._redis.get(key)
                if data:
                    report_dict = json.loads(data)
                    return IncidentReport(**report_dict)
            except Exception as exc:
                log.warning("Redis get failed for %s: %s", incident_id, exc)

        # Fall back to Postgres
        if self._postgres_available and self._postgres:
            try:
                async with self._postgres.cursor() as cur:
                    await cur.execute(
                        "SELECT * FROM incident_reports WHERE incident_id = %s",
                        (incident_id,),
                    )
                    row = await cur.fetchone()
                    if row:
                        # Convert dict_row to IncidentReport
                        # Note: full_analysis is already a dict (JSONB)
                        return IncidentReport(
                            incident_id=row["incident_id"],
                            detected_at=row["detected_at"].isoformat(),
                            resolved_at=row["resolved_at"].isoformat(),
                            duration_ms=row["duration_ms"],
                            trigger=row["trigger"],
                            matched_pattern=row["matched_pattern"],
                            confidence=row["confidence"],
                            root_cause=row["root_cause"],
                            reasoning=row["reasoning"],
                            action_taken=row["action_taken"],
                            remediation_outcome=row["remediation_outcome"],
                            verification_performed=row["verification_performed"],
                            verification_result=row["verification_result"],
                            auto_rollback_triggered=row["auto_rollback_triggered"],
                            outcome=row["outcome"],
                            rca_method=row["rca_method"],
                            policy_blocked=row["policy_blocked"],
                            approval_required=row["approval_required"],
                            severity=row["severity"],
                            override_status=row["override_status"],
                            override_reason=row["override_reason"],
                            override_at=row["override_at"].isoformat() if row["override_at"] else None,
                            override_by=row["override_by"],
                            full_analysis=row["full_analysis"],
                        )
            except Exception as exc:
                log.warning("Postgres get failed for %s: %s", incident_id, exc)

        return None

    async def get_recent(self, limit: int = 50) -> list[IncidentReport]:
        """
        Get recent incidents ordered by detected_at DESC.

        Only queries Postgres (Redis is key-value, can't do ordered range scans).
        """
        if not self._postgres_available or not self._postgres:
            log.warning("Postgres unavailable — cannot retrieve recent incidents")
            return []

        try:
            async with self._postgres.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM incident_reports
                    ORDER BY detected_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()
                return [
                    IncidentReport(
                        incident_id=row["incident_id"],
                        detected_at=row["detected_at"].isoformat(),
                        resolved_at=row["resolved_at"].isoformat(),
                        duration_ms=row["duration_ms"],
                        trigger=row["trigger"],
                        matched_pattern=row["matched_pattern"],
                        confidence=row["confidence"],
                        root_cause=row["root_cause"],
                        reasoning=row["reasoning"],
                        action_taken=row["action_taken"],
                        remediation_outcome=row["remediation_outcome"],
                        verification_performed=row["verification_performed"],
                        verification_result=row["verification_result"],
                        auto_rollback_triggered=row["auto_rollback_triggered"],
                        outcome=row["outcome"],
                        rca_method=row["rca_method"],
                        policy_blocked=row["policy_blocked"],
                        approval_required=row["approval_required"],
                        severity=row["severity"],
                        override_status=row["override_status"],
                        override_reason=row["override_reason"],
                        override_at=row["override_at"].isoformat() if row["override_at"] else None,
                        override_by=row["override_by"],
                        full_analysis=row["full_analysis"],
                    )
                    for row in rows
                ]
        except Exception as exc:
            log.error("Failed to query recent incidents: %s", exc)
            return []

    async def get_by_pattern(self, matched_pattern: str, limit: int = 100) -> list[IncidentReport]:
        """
        Get incidents matching a specific pattern (for trust metrics analysis).

        Example: matched_pattern="rule:oom_kill" to analyze OOM kill pattern reliability.
        """
        if not self._postgres_available or not self._postgres:
            log.warning("Postgres unavailable — cannot query by pattern")
            return []

        try:
            async with self._postgres.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM incident_reports
                    WHERE matched_pattern = %s
                    ORDER BY detected_at DESC
                    LIMIT %s
                    """,
                    (matched_pattern, limit),
                )
                rows = await cur.fetchall()
                return [
                    IncidentReport(
                        incident_id=row["incident_id"],
                        detected_at=row["detected_at"].isoformat(),
                        resolved_at=row["resolved_at"].isoformat(),
                        duration_ms=row["duration_ms"],
                        trigger=row["trigger"],
                        matched_pattern=row["matched_pattern"],
                        confidence=row["confidence"],
                        root_cause=row["root_cause"],
                        reasoning=row["reasoning"],
                        action_taken=row["action_taken"],
                        remediation_outcome=row["remediation_outcome"],
                        verification_performed=row["verification_performed"],
                        verification_result=row["verification_result"],
                        auto_rollback_triggered=row["auto_rollback_triggered"],
                        outcome=row["outcome"],
                        rca_method=row["rca_method"],
                        policy_blocked=row["policy_blocked"],
                        approval_required=row["approval_required"],
                        severity=row["severity"],
                        override_status=row["override_status"],
                        override_reason=row["override_reason"],
                        override_at=row["override_at"].isoformat() if row["override_at"] else None,
                        override_by=row["override_by"],
                        full_analysis=row["full_analysis"],
                    )
                    for row in rows
                ]
        except Exception as exc:
            log.error("Failed to query incidents by pattern %s: %s", matched_pattern, exc)
            return []

    async def get_by_fingerprint(self, fingerprint: str) -> IncidentReport | None:
        """
        Retrieve an incident by Alertmanager fingerprint (Priority 3 — resolved alert handling).

        This is used to match resolved alerts to their corresponding incidents.

        Args:
            fingerprint: Alertmanager fingerprint from the alert payload

        Returns:
            IncidentReport if found, None otherwise
        """
        # Try Redis first (fast path)
        if self._redis_available and self._redis:
            try:
                # Scan for keys matching pattern (not ideal, but Redis is ephemeral cache)
                cursor = 0
                while True:
                    cursor, keys = await self._redis.scan(cursor, match="incident:report:*", count=100)
                    for key in keys:
                        data = await self._redis.get(key)
                        if data:
                            report_dict = json.loads(data)
                            # Check if fingerprint matches in full_analysis
                            if report_dict.get("full_analysis", {}).get("fingerprint") == fingerprint:
                                return IncidentReport(**report_dict)
                    if cursor == 0:
                        break
            except Exception as exc:
                log.warning("Failed to search Redis for fingerprint %s: %s", fingerprint, exc)

        # Fall back to Postgres (source of truth)
        if not self._postgres_available or not self._postgres:
            return None

        try:
            async with self._postgres.cursor() as cur:
                # Query using JSONB path for fingerprint
                await cur.execute(
                    """
                    SELECT * FROM incident_reports
                    WHERE full_analysis->>'fingerprint' = %s
                    ORDER BY detected_at DESC
                    LIMIT 1
                    """,
                    (fingerprint,)
                )
                row = await cur.fetchone()
                if row:
                    return self._row_to_report(row)
                return None
        except Exception as exc:
            log.error("Failed to retrieve incident by fingerprint %s: %s", fingerprint, exc)
            return None

    async def update_override(
        self,
        incident_id: str,
        override_status: str,
        override_reason: str,
        override_by: str,
        matched_pattern: str,  # Added for Prometheus metrics
    ) -> bool:
        """
        Update the override fields for an existing incident (Priority 2 — trust metrics).

        Args:
            incident_id: The incident to update
            override_status: "manual_reversal" | "manual_escalation"
            override_reason: Human-provided explanation
            override_by: Source:identifier (e.g. "api:username", "slack:U12345")
            matched_pattern: RCA pattern (for Prometheus label)

        Returns:
            True if update succeeded, False otherwise
        """
        override_at = datetime.now(timezone.utc).isoformat()

        # Update Redis cache if available
        if self._redis_available and self._redis:
            try:
                key = f"incident:report:{incident_id}"
                data = await self._redis.get(key)
                if data:
                    report_dict = json.loads(data)
                    report_dict["override_status"] = override_status
                    report_dict["override_reason"] = override_reason
                    report_dict["override_at"] = override_at
                    report_dict["override_by"] = override_by
                    await self._redis.setex(key, REDIS_TTL_SECONDS, json.dumps(report_dict))
                    log.debug("Override recorded in Redis for %s", incident_id)
            except Exception as exc:
                log.warning("Failed to update override in Redis for %s: %s", incident_id, exc)

        # Update Postgres (source of truth)
        if not self._postgres_available or not self._postgres:
            log.error("Postgres unavailable — cannot record override for %s", incident_id)
            return False

        try:
            async with self._postgres.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE incident_reports
                    SET override_status = %s,
                        override_reason = %s,
                        override_at = %s,
                        override_by = %s
                    WHERE incident_id = %s
                    """,
                    (override_status, override_reason, override_at, override_by, incident_id),
                )
                if cur.rowcount == 0:
                    log.warning("Override update failed: incident %s not found", incident_id)
                    return False
                log.info("Override recorded for incident %s by %s: %s", incident_id, override_by, override_status)

                # Priority 2: Record Prometheus metrics
                metrics.record_override(matched_pattern, override_status)

                return True
        except Exception as exc:
            log.error("Failed to record override in Postgres for %s: %s", incident_id, exc)
            return False

    async def mark_alert_resolved(
        self,
        incident_id: str,
        resolved_at: str,
    ) -> bool:
        """
        Mark an incident as resolved due to alert auto-resolution (Priority 3).

        This is called when a resolved webhook arrives for an alert that hasn't
        completed the full incident pipeline yet. Updates the incident to:
          - outcome="ignored"
          - remediation_outcome="alert_auto_resolved"
          - Add resolved_at timestamp to full_analysis

        Args:
            incident_id: The incident to update
            resolved_at: ISO timestamp when alert resolved

        Returns:
            True if update succeeded, False otherwise
        """
        # Update Redis cache if available
        if self._redis_available and self._redis:
            try:
                key = f"incident:report:{incident_id}"
                data = await self._redis.get(key)
                if data:
                    report_dict = json.loads(data)
                    report_dict["outcome"] = "ignored"
                    report_dict["remediation_outcome"] = "alert_auto_resolved"
                    report_dict["full_analysis"]["alert_resolved_at"] = resolved_at
                    await self._redis.setex(key, REDIS_TTL_SECONDS, json.dumps(report_dict))
                    log.debug("Alert auto-resolved marked in Redis for %s", incident_id)
            except Exception as exc:
                log.warning("Failed to update resolved status in Redis for %s: %s", incident_id, exc)

        # Update Postgres (source of truth)
        if not self._postgres_available or not self._postgres:
            log.error("Postgres unavailable — cannot mark alert resolved for %s", incident_id)
            return False

        try:
            async with self._postgres.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE incident_reports
                    SET outcome = 'ignored',
                        remediation_outcome = 'alert_auto_resolved',
                        full_analysis = jsonb_set(
                            full_analysis,
                            '{alert_resolved_at}',
                            to_jsonb(%s::text)
                        )
                    WHERE incident_id = %s
                      AND outcome NOT IN ('auto_resolved', 'rolled_back')
                    """,
                    (resolved_at, incident_id),
                )
                if cur.rowcount == 0:
                    log.debug("Alert resolved update skipped for %s (already completed or not found)", incident_id)
                    return False
                log.info("Incident %s marked as alert_auto_resolved", incident_id)
                return True
        except Exception as exc:
            log.error("Failed to mark alert resolved in Postgres for %s: %s", incident_id, exc)
            return False

    async def get_trust_metrics(self, matched_pattern: str | None = None) -> dict[str, Any]:
        """
        Compute trust metrics for agent reliability (Priority 2).

        Equivalent to SQL GROUP BY queries on incidents and overrides.
        Used to identify which RCA patterns need improvement.

        Args:
            matched_pattern: Optional filter to compute metrics for a specific pattern.
                           If None, returns metrics aggregated across all patterns.

        Returns:
            Dict with structure:
            {
                "by_pattern": {
                    "rule:oom_kill": {
                        "total_incidents": 42,
                        "total_overrides": 3,
                        "override_rate": 0.071,
                        "by_outcome": {
                            "auto_resolved": 30,
                            "escalated_low_confidence": 5,
                            "escalated_verification_failed": 4,
                            "rolled_back": 2,
                            "ignored": 1
                        },
                        "by_override_status": {
                            "manual_reversal": 2,
                            "manual_escalation": 1
                        }
                    },
                    "llm_fallback": { ... },
                    ...
                },
                "overall": {
                    "total_incidents": 150,
                    "total_overrides": 12,
                    "override_rate": 0.08
                }
            }
        """
        if not self._postgres_available or not self._postgres:
            log.error("Postgres unavailable — cannot compute trust metrics")
            return {"error": "Postgres unavailable"}

        try:
            async with self._postgres.cursor() as cur:
                # Build WHERE clause
                where_clause = ""
                params: tuple = ()
                if matched_pattern:
                    where_clause = "WHERE matched_pattern = %s"
                    params = (matched_pattern,)

                # Query 1: Incidents by pattern and outcome
                await cur.execute(
                    f"""
                    SELECT matched_pattern, outcome, COUNT(*) as count
                    FROM incident_reports
                    {where_clause}
                    GROUP BY matched_pattern, outcome
                    ORDER BY matched_pattern, outcome
                    """,
                    params,
                )
                incident_rows = await cur.fetchall()

                # Query 2: Overrides by pattern and override_status
                override_where = "WHERE override_status != 'none'"
                if matched_pattern:
                    override_where += " AND matched_pattern = %s"
                await cur.execute(
                    f"""
                    SELECT matched_pattern, override_status, COUNT(*) as count
                    FROM incident_reports
                    {override_where}
                    GROUP BY matched_pattern, override_status
                    ORDER BY matched_pattern, override_status
                    """,
                    params,
                )
                override_rows = await cur.fetchall()

                # Query 3: Total incidents per pattern
                await cur.execute(
                    f"""
                    SELECT matched_pattern, COUNT(*) as total
                    FROM incident_reports
                    {where_clause}
                    GROUP BY matched_pattern
                    ORDER BY matched_pattern
                    """,
                    params,
                )
                total_rows = await cur.fetchall()

            # Aggregate results
            by_pattern: dict[str, Any] = {}

            # Initialize pattern entries from total_rows
            for row in total_rows:
                pattern = row["matched_pattern"]
                by_pattern[pattern] = {
                    "total_incidents": row["total"],
                    "total_overrides": 0,
                    "override_rate": 0.0,
                    "by_outcome": {},
                    "by_override_status": {},
                }

            # Populate by_outcome
            for row in incident_rows:
                pattern = row["matched_pattern"]
                outcome = row["outcome"]
                count = row["count"]
                if pattern in by_pattern:
                    by_pattern[pattern]["by_outcome"][outcome] = count

            # Populate by_override_status and total_overrides
            for row in override_rows:
                pattern = row["matched_pattern"]
                override_status = row["override_status"]
                count = row["count"]
                if pattern in by_pattern:
                    by_pattern[pattern]["by_override_status"][override_status] = count
                    by_pattern[pattern]["total_overrides"] += count

            # Compute override_rate for each pattern
            for pattern, metrics in by_pattern.items():
                total = metrics["total_incidents"]
                overrides = metrics["total_overrides"]
                metrics["override_rate"] = round(overrides / total, 3) if total > 0 else 0.0

            # Compute overall metrics
            overall_total = sum(m["total_incidents"] for m in by_pattern.values())
            overall_overrides = sum(m["total_overrides"] for m in by_pattern.values())
            overall_rate = round(overall_overrides / overall_total, 3) if overall_total > 0 else 0.0

            return {
                "by_pattern": by_pattern,
                "overall": {
                    "total_incidents": overall_total,
                    "total_overrides": overall_overrides,
                    "override_rate": overall_rate,
                },
            }

        except Exception as exc:
            log.error("Failed to compute trust metrics: %s", exc)
            return {"error": str(exc)}


# ── Convenience factory ───────────────────────────────────────────────────────

@asynccontextmanager
async def get_storage() -> AsyncIterator[IncidentStorage]:
    """
    Convenience async context manager for getting a storage instance.

    Usage:
        async with get_storage() as storage:
            await storage.save(report)
    """
    async with IncidentStorage() as storage:
        yield storage
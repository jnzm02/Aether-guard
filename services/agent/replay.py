"""
Incident Replay System for Aether-Guard

Enables deterministic testing of RCA and remediation logic by:
1. Recording real incidents (alerts + metrics + logs + actions + outcomes)
2. Replaying them through updated agent logic
3. Comparing outputs to ground truth (human-validated actions)

This is CRITICAL for:
- Validating prompt changes don't degrade accuracy
- A/B testing different LLM models
- Measuring MTTR improvements over time
- Building evaluation datasets for ML training
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass
class IncidentRecord:
    """
    Complete record of an incident for replay.

    This is the "ground truth" dataset for evaluation.
    """
    # Identity
    incident_id: str  # Unique ID (e.g., "2026-04-29T15:30:00Z-memleak-critical")
    timestamp: str    # ISO8601 timestamp when alert fired

    # Alert metadata (from Alertmanager)
    alert_name: str
    alert_severity: str  # "warning" or "critical"
    alert_labels: dict[str, str]
    alert_annotations: dict[str, str]

    # Prometheus metrics snapshot (at time of alert)
    metrics: dict[str, Optional[float]]  # e.g., {"error_rate_5m": 0.12, ...}

    # Container logs (last N lines before alert)
    logs: list[str]

    # Ground truth (validated by human SRE)
    ground_truth_action: str        # "RESTART", "SCALE", "ROLLBACK", "IGNORE"
    ground_truth_root_cause: str    # e.g., "memory_leak", "cpu_saturation"
    ground_truth_reasoning: str     # Human SRE's explanation

    # Agent's decision (recorded at time of incident)
    agent_action: Optional[str] = None
    agent_confidence: Optional[float] = None
    agent_root_cause: Optional[str] = None
    agent_reasoning: Optional[str] = None

    # Outcome (did the action work?)
    outcome: Optional[str] = None  # "success", "failed", "made_worse"
    metrics_after_2min: Optional[dict[str, Optional[float]]] = None
    metrics_after_10min: Optional[dict[str, Optional[float]]] = None

    # Metadata
    was_automatically_remediated: bool = False
    human_intervention_required: bool = False
    mttr_seconds: Optional[int] = None  # Time to resolve

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IncidentRecord":
        """Deserialize from dict."""
        return cls(**data)


class IncidentRecorder:
    """
    Records incidents to JSONL file for later replay.

    Usage:
        recorder = IncidentRecorder(storage_path="/app/data/incidents.jsonl")

        # When alert fires
        incident = IncidentRecord(
            incident_id=f"{timestamp}-{alert_name}",
            timestamp=timestamp,
            alert_name=alert_name,
            metrics=metrics_snapshot,
            logs=container_logs,
            # ... populate all fields
        )
        recorder.record(incident)

        # Later, after remediation completes
        recorder.update_outcome(incident_id, outcome="success", metrics_after=...)
    """

    def __init__(self, storage_path: str = "/app/data/incidents.jsonl"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, incident: IncidentRecord) -> None:
        """Append incident to JSONL file."""
        try:
            with self.storage_path.open("a") as f:
                f.write(json.dumps(incident.to_dict()) + "\n")
            log.info("Recorded incident %s to %s", incident.incident_id, self.storage_path)
        except Exception as exc:
            log.error("Failed to record incident %s: %s", incident.incident_id, exc)

    def update_outcome(
        self,
        incident_id: str,
        outcome: str,
        metrics_after_2min: Optional[dict] = None,
        metrics_after_10min: Optional[dict] = None,
        mttr_seconds: Optional[int] = None,
    ) -> None:
        """
        Update incident with outcome data.

        This is called 2-10 minutes after remediation to record results.

        NOTE: This implementation appends a new record with updated fields.
        In production, use a proper database (PostgreSQL) with UPDATE queries.
        """
        # Read all incidents
        incidents = list(self.load_all())

        # Find and update the target incident
        updated = False
        for inc in incidents:
            if inc.incident_id == incident_id:
                inc.outcome = outcome
                inc.metrics_after_2min = metrics_after_2min
                inc.metrics_after_10min = metrics_after_10min
                inc.mttr_seconds = mttr_seconds
                updated = True
                break

        if not updated:
            log.warning("Incident %s not found for outcome update", incident_id)
            return

        # Rewrite entire file (inefficient but simple for JSONL)
        # Production: use database with UPDATE
        try:
            with self.storage_path.open("w") as f:
                for inc in incidents:
                    f.write(json.dumps(inc.to_dict()) + "\n")
            log.info("Updated outcome for incident %s", incident_id)
        except Exception as exc:
            log.error("Failed to update outcome for %s: %s", incident_id, exc)

    def load_all(self) -> list[IncidentRecord]:
        """Load all recorded incidents from JSONL file."""
        if not self.storage_path.exists():
            return []

        incidents = []
        try:
            with self.storage_path.open("r") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        incidents.append(IncidentRecord.from_dict(data))
        except Exception as exc:
            log.error("Failed to load incidents from %s: %s", self.storage_path, exc)

        return incidents

    def load_by_id(self, incident_id: str) -> Optional[IncidentRecord]:
        """Load specific incident by ID."""
        for inc in self.load_all():
            if inc.incident_id == incident_id:
                return inc
        return None


class IncidentReplayer:
    """
    Replays recorded incidents through agent logic.

    Usage:
        replayer = IncidentReplayer(
            agent=agent_instance,
            recorder=incident_recorder,
        )

        # Replay single incident
        result = await replayer.replay_one("2026-04-29T15:30:00Z-memleak")

        # Replay all incidents and compute accuracy
        results = await replayer.replay_all()
        accuracy = replayer.compute_accuracy(results)
        print(f"Agent accuracy: {accuracy:.1%}")

        # A/B test prompt changes
        results_baseline = await replayer.replay_all()
        # ... modify prompt ...
        results_variant = await replayer.replay_all()
        print(f"Baseline: {compute_accuracy(results_baseline):.1%}")
        print(f"Variant:  {compute_accuracy(results_variant):.1%}")
    """

    def __init__(self, recorder: IncidentRecorder):
        self.recorder = recorder

    async def replay_one(
        self,
        incident_id: str,
        agent_analyze_func,  # Callable that takes (alert, metrics, logs) → analysis dict
    ) -> dict[str, Any]:
        """
        Replay a single incident through agent logic.

        Args:
            incident_id: Incident to replay
            agent_analyze_func: Async function that performs RCA
                               (normally this would be agent._analyze_alert)

        Returns:
            Dict with comparison results:
            {
                "incident_id": "...",
                "ground_truth_action": "RESTART",
                "agent_action": "RESTART",
                "match": True,
                "agent_confidence": 0.85,
                "ground_truth_root_cause": "memory_leak",
                "agent_root_cause": "memory_leak",
                "root_cause_match": True,
            }
        """
        incident = self.recorder.load_by_id(incident_id)
        if not incident:
            raise ValueError(f"Incident {incident_id} not found")

        log.info("Replaying incident %s...", incident_id)

        # Reconstruct alert payload (as if it just came from Alertmanager)
        alert_payload = {
            "labels": incident.alert_labels,
            "annotations": incident.alert_annotations,
            "startsAt": incident.timestamp,
            "status": "firing",
        }

        # Call agent's RCA function with historical data
        try:
            analysis = await agent_analyze_func(
                alert=alert_payload,
                metrics=incident.metrics,
                logs=incident.logs,
            )
        except Exception as exc:
            log.error("Replay failed for %s: %s", incident_id, exc)
            return {
                "incident_id": incident_id,
                "error": str(exc),
                "match": False,
            }

        # Compare agent output to ground truth
        action_match = analysis["action"] == incident.ground_truth_action
        root_cause_match = (
            analysis.get("root_cause", "").lower()
            == incident.ground_truth_root_cause.lower()
        )

        return {
            "incident_id": incident_id,
            "timestamp": incident.timestamp,
            "ground_truth_action": incident.ground_truth_action,
            "agent_action": analysis["action"],
            "action_match": action_match,
            "ground_truth_root_cause": incident.ground_truth_root_cause,
            "agent_root_cause": analysis.get("root_cause", ""),
            "root_cause_match": root_cause_match,
            "agent_confidence": analysis.get("confidence", 0.0),
            "agent_reasoning": analysis.get("reasoning", ""),
            "ground_truth_reasoning": incident.ground_truth_reasoning,
        }

    async def replay_all(
        self,
        agent_analyze_func,
        filter_severity: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Replay all recorded incidents.

        Args:
            agent_analyze_func: RCA function to test
            filter_severity: Only replay incidents with this severity (optional)

        Returns:
            List of replay results (one per incident)
        """
        incidents = self.recorder.load_all()

        if filter_severity:
            incidents = [
                inc for inc in incidents
                if inc.alert_severity == filter_severity
            ]

        log.info("Replaying %d incidents...", len(incidents))

        results = []
        for incident in incidents:
            try:
                result = await self.replay_one(incident.incident_id, agent_analyze_func)
                results.append(result)
            except Exception as exc:
                log.error("Replay failed for %s: %s", incident.incident_id, exc)
                results.append({
                    "incident_id": incident.incident_id,
                    "error": str(exc),
                    "action_match": False,
                })

        return results

    def compute_metrics(self, results: list[dict[str, Any]]) -> dict[str, float]:
        """
        Compute evaluation metrics from replay results.

        Returns:
            {
                "action_accuracy": 0.85,  # % where agent action == ground truth
                "root_cause_accuracy": 0.78,
                "mean_confidence": 0.82,
                "total_incidents": 50,
                "action_breakdown": {"RESTART": 25, "SCALE": 15, ...},
            }
        """
        if not results:
            return {
                "action_accuracy": 0.0,
                "root_cause_accuracy": 0.0,
                "mean_confidence": 0.0,
                "total_incidents": 0,
            }

        action_matches = sum(1 for r in results if r.get("action_match", False))
        root_cause_matches = sum(1 for r in results if r.get("root_cause_match", False))
        confidences = [r["agent_confidence"] for r in results if "agent_confidence" in r]

        action_breakdown = {}
        for r in results:
            action = r.get("agent_action")
            if action:
                action_breakdown[action] = action_breakdown.get(action, 0) + 1

        return {
            "action_accuracy": action_matches / len(results),
            "root_cause_accuracy": root_cause_matches / len(results),
            "mean_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
            "total_incidents": len(results),
            "action_breakdown": action_breakdown,
        }

    def print_report(self, results: list[dict[str, Any]]) -> None:
        """Print human-readable evaluation report."""
        metrics = self.compute_metrics(results)

        print("\n" + "="*60)
        print("INCIDENT REPLAY EVALUATION REPORT")
        print("="*60)
        print(f"Total Incidents:        {metrics['total_incidents']}")
        print(f"Action Accuracy:        {metrics['action_accuracy']:.1%}")
        print(f"Root Cause Accuracy:    {metrics['root_cause_accuracy']:.1%}")
        print(f"Mean Confidence:        {metrics['mean_confidence']:.2f}")
        print()
        print("Action Breakdown:")
        for action, count in sorted(metrics["action_breakdown"].items()):
            print(f"  {action:12s} {count:3d} ({count/metrics['total_incidents']:.1%})")
        print("="*60)

        # Show failures
        failures = [r for r in results if not r.get("action_match", False)]
        if failures:
            print(f"\n{len(failures)} Incorrect Predictions:")
            for r in failures[:10]:  # Show first 10
                print(f"  [{r['incident_id']}]")
                print(f"    Expected: {r['ground_truth_action']}")
                print(f"    Got:      {r['agent_action']} (confidence {r.get('agent_confidence', 0):.2f})")
                print(f"    Reasoning: {r.get('agent_reasoning', 'N/A')[:100]}...")
                print()


# Example usage for CLI
async def main():
    """
    CLI for incident replay.

    Usage:
        python -m agent.replay --replay-all
        python -m agent.replay --replay-one 2026-04-29T15:30:00Z-memleak
        python -m agent.replay --stats
    """
    import argparse

    parser = argparse.ArgumentParser(description="Replay incidents for evaluation")
    parser.add_argument("--replay-all", action="store_true", help="Replay all incidents")
    parser.add_argument("--replay-one", type=str, help="Replay specific incident by ID")
    parser.add_argument("--stats", action="store_true", help="Show statistics only")
    parser.add_argument("--storage-path", type=str, default="/app/data/incidents.jsonl")
    args = parser.parse_args()

    recorder = IncidentRecorder(storage_path=args.storage_path)
    _ = IncidentReplayer(recorder=recorder)  # noqa: F841

    if args.stats:
        incidents = recorder.load_all()
        print(f"Total incidents recorded: {len(incidents)}")
        severities = {}
        for inc in incidents:
            sev = inc.alert_severity
            severities[sev] = severities.get(sev, 0) + 1
        print("By severity:", severities)

    elif args.replay_one:
        # This requires importing the actual agent module
        # For now, just print the incident data
        incident = recorder.load_by_id(args.replay_one)
        if incident:
            print(json.dumps(incident.to_dict(), indent=2))
        else:
            print(f"Incident {args.replay_one} not found")

    elif args.replay_all:
        print("Replay-all requires integration with agent module")
        print("See usage example in IncidentReplayer docstring")


if __name__ == "__main__":
    asyncio.run(main())
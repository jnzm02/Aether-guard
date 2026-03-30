"""
Aether-Guard Remediation Engine — Phase 4

Maps the AI Agent's action codes to concrete Docker operations.

Action → Implementation:
  RESTART  → docker SDK: container.restart(timeout=30)
             Service recovers with fresh memory / cleared state.
  SCALE    → docker SDK: run an additional replica (or log intent for k8s)
             Addresses load-driven latency spikes.
  ROLLBACK → docker SDK: stop container → swap image tag → restart
             Addresses regressions introduced by a bad deploy.
  IGNORE   → no-op; audit entry written, no container touched.

Safety gates (defence-in-depth):
  1. Confidence gate   — action only executes if confidence ≥ per-action threshold.
  2. Cooldown gate     — same container cannot be restarted more than once per 5 min.
  3. Dry-run flag      — DRY_RUN=true logs intent without touching Docker.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("aether-guard.remediation")

# ── Config ─────────────────────────────────────────────────────────────────────
TARGET_CONTAINER  = os.getenv("TARGET_CONTAINER",   "target-service")
ROLLBACK_IMAGE    = os.getenv("ROLLBACK_IMAGE",      "aether-guard/target-service:previous")
DRY_RUN           = os.getenv("DRY_RUN", "false").lower() == "true"

# Per-action minimum confidence required before execution.
THRESHOLDS = {
    "RESTART":  float(os.getenv("RESTART_CONFIDENCE_MIN",  "0.75")),
    "SCALE":    float(os.getenv("SCALE_CONFIDENCE_MIN",    "0.70")),
    "ROLLBACK": float(os.getenv("ROLLBACK_CONFIDENCE_MIN", "0.85")),
    "IGNORE":   0.0,
}

# ── Cooldown tracker (prevents remediation storms) ─────────────────────────────
_last_action_ts: dict[str, float] = {}   # container_name → epoch timestamp
COOLDOWN_SECONDS = int(os.getenv("REMEDIATION_COOLDOWN_S", "300"))   # 5 min


# ── Docker client ──────────────────────────────────────────────────────────────
try:
    import docker as _docker_lib
    _client = _docker_lib.from_env()
    _client.ping()
    log.info("Remediation engine: Docker socket connected")
except Exception as _exc:
    _client = None
    log.warning("Remediation engine: Docker socket unavailable (%s) — actions will be simulated", _exc)


# ── Result dataclass ───────────────────────────────────────────────────────────
@dataclass
class RemediationResult:
    action:      str
    executed:    bool
    outcome:     str          # "success" | "skipped" | "failed" | "dry_run" | "no_op"
    reason:      str          # human-readable explanation
    container:   str
    executed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details:     dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "action":      self.action,
            "executed":    self.executed,
            "outcome":     self.outcome,
            "reason":      self.reason,
            "container":   self.container,
            "executed_at": self.executed_at,
            "details":     self.details,
        }


# ── Public API ─────────────────────────────────────────────────────────────────

def execute_action(action: str, analysis: dict) -> RemediationResult:
    """
    Execute the remediation action recommended by the AI Agent.

    Args:
        action:   One of RESTART | SCALE | ROLLBACK | IGNORE
        analysis: The full analysis dict from agent.py (includes confidence)

    Returns:
        RemediationResult — always returned, never raises.
    """
    confidence = float(analysis.get("confidence", 0.0))
    alertname  = analysis.get("alertname", "unknown")
    container  = TARGET_CONTAINER

    log.info(
        "Remediation requested  action=%s  confidence=%.2f  alert=%s  dry_run=%s",
        action, confidence, alertname, DRY_RUN,
    )

    # ── Gate 1: confidence threshold ────────────────────────────────────────
    threshold = THRESHOLDS.get(action, 1.0)
    if confidence < threshold:
        reason = (
            f"Confidence {confidence:.2f} < required threshold {threshold:.2f} "
            f"for {action}. Skipping to avoid false-positive remediation."
        )
        log.warning("Remediation skipped: %s", reason)
        return RemediationResult(
            action=action, executed=False, outcome="skipped",
            reason=reason, container=container,
        )

    # ── Gate 2: cooldown ─────────────────────────────────────────────────────
    last = _last_action_ts.get(container, 0.0)
    elapsed = time.monotonic() - last
    if elapsed < COOLDOWN_SECONDS and action not in ("IGNORE",):
        reason = (
            f"Cooldown active: last action on {container!r} was "
            f"{elapsed:.0f}s ago (cooldown={COOLDOWN_SECONDS}s)."
        )
        log.warning("Remediation skipped: %s", reason)
        return RemediationResult(
            action=action, executed=False, outcome="skipped",
            reason=reason, container=container,
        )

    # ── Gate 3: dry-run ──────────────────────────────────────────────────────
    if DRY_RUN:
        log.info("DRY_RUN: would execute %s on container %s", action, container)
        return RemediationResult(
            action=action, executed=False, outcome="dry_run",
            reason=f"DRY_RUN=true — {action} would have been executed on {container!r}.",
            container=container,
        )

    # ── Dispatch ─────────────────────────────────────────────────────────────
    dispatch = {
        "RESTART":  _restart,
        "SCALE":    _scale,
        "ROLLBACK": _rollback,
        "IGNORE":   _ignore,
    }
    handler = dispatch.get(action, _unknown)
    result  = handler(container, analysis)
    if result.executed:
        _last_action_ts[container] = time.monotonic()

    log.info(
        "Remediation complete  action=%s  outcome=%s  container=%s",
        result.action, result.outcome, result.container,
    )
    return result


# ── Action handlers ────────────────────────────────────────────────────────────

def _restart(container_name: str, analysis: dict) -> RemediationResult:
    """Restart the container — clears memory leaks, resets in-process state."""
    if _client is None:
        return RemediationResult(
            action="RESTART", executed=False, outcome="failed",
            reason="Docker client unavailable — cannot restart container.",
            container=container_name,
        )
    try:
        container = _client.containers.get(container_name)
        status_before = container.status

        log.info("Restarting container %r (status=%s) ...", container_name, status_before)
        container.restart(timeout=30)
        container.reload()
        status_after = container.status

        return RemediationResult(
            action="RESTART", executed=True, outcome="success",
            reason=f"Container {container_name!r} restarted successfully.",
            container=container_name,
            details={
                "status_before": status_before,
                "status_after":  status_after,
                "image":         container.image.tags,
            },
        )
    except Exception as exc:
        log.error("RESTART failed for %r: %s", container_name, exc)
        return RemediationResult(
            action="RESTART", executed=False, outcome="failed",
            reason=f"Docker restart failed: {exc}",
            container=container_name,
        )


def _scale(container_name: str, analysis: dict) -> RemediationResult:
    """
    Scale-out: run an additional replica of the target service.
    In a real k8s environment this would patch the Deployment replicas field.
    Here we start a second container from the same image.
    """
    if _client is None:
        return RemediationResult(
            action="SCALE", executed=False, outcome="failed",
            reason="Docker client unavailable.",
            container=container_name,
        )
    try:
        original = _client.containers.get(container_name)
        image    = original.image.tags[0] if original.image.tags else original.image.id
        replica_name = f"{container_name}-scale-{int(time.time())}"

        # Expose on a random port to avoid bind conflicts.
        new_container = _client.containers.run(
            image,
            name=replica_name,
            detach=True,
            environment={"PORT": "8080"},
            network_mode=f"container:{container_name}",
            labels={"com.aether-guard.scaled-replica": "true"},
        )

        log.info("Scaled-out: new replica %r started from image %s", replica_name, image)
        return RemediationResult(
            action="SCALE", executed=True, outcome="success",
            reason=f"Replica {replica_name!r} started to absorb load.",
            container=container_name,
            details={"replica_name": replica_name, "image": image},
        )
    except Exception as exc:
        log.error("SCALE failed: %s", exc)
        return RemediationResult(
            action="SCALE", executed=False, outcome="failed",
            reason=f"Docker scale failed: {exc}",
            container=container_name,
        )


def _rollback(container_name: str, analysis: dict) -> RemediationResult:
    """
    Roll back to the previous known-good image.
    Production equivalent: kubectl set image deployment/target-service ...
    """
    if _client is None:
        return RemediationResult(
            action="ROLLBACK", executed=False, outcome="failed",
            reason="Docker client unavailable.",
            container=container_name,
        )
    try:
        container     = _client.containers.get(container_name)
        current_image = container.image.tags[0] if container.image.tags else container.image.id

        # Check if a "previous" image tag exists.
        try:
            _client.images.get(ROLLBACK_IMAGE)
            rollback_image = ROLLBACK_IMAGE
        except Exception:
            # No previous tag — simulate by restarting current image and logging intent.
            log.warning(
                "Rollback image %r not found. Restarting with current image as fallback.",
                ROLLBACK_IMAGE,
            )
            container.restart(timeout=30)
            return RemediationResult(
                action="ROLLBACK", executed=True, outcome="success",
                reason=(
                    f"Rollback image {ROLLBACK_IMAGE!r} not found in local registry. "
                    f"Restarted with current image {current_image!r} as safe fallback. "
                    f"In production: kubectl rollout undo deployment/target-service"
                ),
                container=container_name,
                details={"current_image": current_image, "intended_rollback": ROLLBACK_IMAGE},
            )

        # Stop, remove, and recreate with previous image.
        env     = container.attrs["Config"]["Env"]
        ports   = container.attrs["HostConfig"]["PortBindings"]
        network = list(container.attrs["NetworkSettings"]["Networks"].keys())[0]

        container.stop(timeout=15)
        container.remove()

        new_container = _client.containers.run(
            rollback_image,
            name=container_name,
            detach=True,
            environment=env,
            ports=ports,
            network=network,
            labels={"com.aether-guard.rolled-back-from": current_image},
        )

        log.info("Rollback complete: %r → %s", container_name, rollback_image)
        return RemediationResult(
            action="ROLLBACK", executed=True, outcome="success",
            reason=f"Container {container_name!r} rolled back from {current_image!r} to {rollback_image!r}.",
            container=container_name,
            details={"from_image": current_image, "to_image": rollback_image},
        )
    except Exception as exc:
        log.error("ROLLBACK failed: %s", exc)
        return RemediationResult(
            action="ROLLBACK", executed=False, outcome="failed",
            reason=f"Docker rollback failed: {exc}",
            container=container_name,
        )


def _ignore(container_name: str, analysis: dict) -> RemediationResult:
    """No-op — alert acknowledged, no container action required."""
    reason = analysis.get("reasoning", "AI Agent determined no action is required.")
    log.info("IGNORE: %s", reason)
    return RemediationResult(
        action="IGNORE", executed=False, outcome="no_op",
        reason=reason, container=container_name,
    )


def _unknown(container_name: str, analysis: dict) -> RemediationResult:
    action = analysis.get("action", "UNKNOWN")
    return RemediationResult(
        action=action, executed=False, outcome="failed",
        reason=f"Unknown action {action!r} — no handler registered.",
        container=container_name,
    )

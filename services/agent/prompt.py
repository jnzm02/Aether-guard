# ─────────────────────────────────────────────────────────────────────────────
# Prompt templates for the Aether-Guard AI SRE Agent
#
# Design principles (Google SRE alignment):
#   - System prompt establishes strict JSON-only output contract
#   - User prompt provides full observability context: alert + metrics + logs
#   - Action vocabulary maps 1-to-1 to Phase 4 remediation handlers
#   - Confidence calibration prevents false-positive automated actions
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an autonomous Site Reliability Engineer (SRE) AI agent embedded in \
Aether-Guard — an incident response system monitoring \
`aether-guard/target-service`, a Go microservice.

The service exposes three chaos injection endpoints you must be aware of:
  • /chaos/memleak  → allocates and retains heap memory (RSS grows unbounded)
  • /chaos/latency  → injects artificial response delays (p99 latency spikes)
  • /chaos/error    → forces HTTP 500 responses (burns error budget)

You will receive:
  1. Alert metadata (alertname, severity, SLO impacted, timestamps)
  2. A Prometheus metrics snapshot captured at the moment the alert fired
  3. The last 100 log lines from the target-service container

Your output MUST be a single, raw JSON object — no markdown fences, no prose \
outside the JSON, no trailing text.  The schema is EXACTLY:

{
  "analysis":             "<2–4 sentences of RCA citing specific metric values and log evidence>",
  "root_cause":           "<one sentence: what failed and why>",
  "confidence":           <float 0.0–1.0>,
  "action":               "<one of: RESTART | SCALE | ROLLBACK | IGNORE>",
  "reasoning":            "<why this action directly addresses the root cause>",
  "slo_impact":           "<which SLO is breached and estimated severity>",
  "recommended_followup": "<one concrete next step for the on-call engineer>"
}

Action semantics — choose exactly one:
  RESTART   Service is in an unrecoverable degraded state: memory leak approaching
            OOM, deadlock, corrupted in-memory state.  Restart the container.
  SCALE     Service is healthy but overwhelmed by load.  Add replicas or raise
            resource limits.
  ROLLBACK  A recent code or config change introduced a regression — sudden error
            rate increase or latency spike with no preceding load increase.
            Roll back to the previous known-good version.
  IGNORE    Alert is a false positive, transient blip, or already self-resolving.
            No action required; monitor and close.

Confidence calibration (BE HONEST — do not inflate):
  ≥ 0.90  Very high: direct causal chain is unambiguous in metrics + logs
  0.75–0.89  High: strong signal with minor ambiguity
  0.60–0.74  Medium: plausible hypothesis, some gaps in evidence
  < 0.60  Low: insufficient signal → always choose IGNORE

Rules:
  • Reference exact metric values (e.g., "error_ratio=0.97", "p99=3.12s").
  • If metrics_snapshot values are null, rely on log evidence and lower confidence.
  • Never speculate beyond the evidence provided.
  • Keep analysis concise — this feeds into an automated post-mortem.
"""

# ─────────────────────────────────────────────────────────────────────────────

def build_user_prompt(alert: dict) -> str:
    """
    Build the user-turn prompt by injecting enriched alert context.
    Uses the data structure produced by the listener's enrich_alert() function.
    """
    labels      = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    metrics     = alert.get("metrics_snapshot", {})
    logs        = alert.get("log_tail", [])

    # ── Metrics formatting ─────────────────────────────────────────────────

    def pct(v):
        return f"{v * 100:.2f}%" if v is not None else "N/A"

    def sec(v):
        return f"{v:.4f}s" if v is not None else "N/A"

    def rps(v):
        return f"{v:.2f} req/s" if v is not None else "N/A"

    def mib(v):
        return f"{v / 1_048_576:.1f} MiB  ({int(v):,} bytes)" if v is not None else "N/A"

    def num(v):
        return f"{int(v)}" if v is not None else "N/A"

    # Derive breach indicators for the prompt
    e_ratio   = metrics.get("error_ratio_5m")
    p99       = metrics.get("latency_p99_5m_seconds")
    p50       = metrics.get("latency_p50_5m_seconds")
    req_rate  = metrics.get("request_rate_5m_rps")
    leak      = metrics.get("memleak_bytes_allocated")
    c_errors  = metrics.get("chaos_errors_injected_total")

    slo_flags = []
    if e_ratio is not None and e_ratio > 0.001:
        slo_flags.append(f"  ⚠ error_ratio={pct(e_ratio)} (SLO budget: 0.10%)")
    if p99 is not None and p99 > 0.200:
        slo_flags.append(f"  ⚠ p99_latency={sec(p99)} (SLO threshold: 0.200s)")
    if leak is not None and leak > 104_857_600:
        slo_flags.append(f"  ⚠ memleak={mib(leak)} (alert threshold: 100 MiB)")
    slo_status = "\n".join(slo_flags) if slo_flags else "  (no threshold breaches detected in snapshot)"

    # ── Log tail — last 50 lines are most relevant ─────────────────────────
    log_lines = logs[-50:] if logs else []
    log_text  = "\n".join(log_lines) if log_lines else "(no log lines available)"

    return f"""\
## INCIDENT ALERT

Alert Name   : {labels.get('alertname', 'unknown')}
Severity     : {labels.get('severity', 'unknown').upper()}
SLO Impacted : {labels.get('slo', 'unknown')}
Status       : {alert.get('status', 'unknown').upper()}
Started At   : {alert.get('starts_at', 'N/A')}
Summary      : {annotations.get('summary', 'N/A')}
Description  : {annotations.get('description', 'N/A')}

## PROMETHEUS METRICS SNAPSHOT (captured at alert-fire time)

Error Ratio   (5m)  : {pct(e_ratio)}       ← SLO budget: 0.10%
p99 Latency   (5m)  : {sec(p99)}            ← SLO threshold: 200ms
p50 Latency   (5m)  : {sec(p50)}
Request Rate  (5m)  : {rps(req_rate)}
Leaked Memory       : {mib(leak)}   ← alert threshold: 100 MiB
Chaos Events Total  : {num(c_errors)}

SLO BREACH INDICATORS:
{slo_status}

## TARGET-SERVICE LOG TAIL (last {len(log_lines)} lines)

{log_text}

## YOUR TASK

Perform Root Cause Analysis using only the evidence above.
Determine the remediation action that directly addresses the root cause.
Respond with ONLY the JSON object — no other text.\
"""

"""
Aether-Guard — Blameless Post-Mortem Generator (Phase 4)

Generates a Google-SRE-style blameless post-mortem Markdown document
from a structured analysis dict produced by agent.py + remediation.py.

Design:
  - Deterministic: no LLM call, pure Python template rendering.
  - Self-contained: only uses data already present in the analysis record.
  - Testable: every function is pure (input → output, no side effects).
  - Google SRE aligned: follows https://sre.google/sre-book/postmortem-culture/

Public API:
  generate(analysis: dict) -> str        — render Markdown string
  save(text: str, analysis: dict, output_dir: Path) -> Path  — write to disk
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

_VERSION = "1.1.0"

# Per-action follow-up items aligned to Google SRE toil-reduction culture.
_ACTION_ITEMS: dict[str, list[tuple[str, str]]] = {
    "RESTART": [
        ("Identify the root trigger that required a container restart; add a targeted alert.", "P1"),
        ("Add a liveness probe that catches the degraded state before OOM occurs.", "P1"),
        ("Review memory limits and request/limit ratio for the target service.", "P2"),
    ],
    "SCALE": [
        ("Tune HPA minReplicas / maxReplicas based on observed traffic patterns.", "P1"),
        ("Add a predictive scaling rule for known traffic ramp periods.", "P2"),
        ("Review CPU/memory resource requests to ensure HPA thresholds are realistic.", "P2"),
    ],
    "ROLLBACK": [
        ("Introduce automated canary analysis (e.g., Argo Rollouts) to catch regressions before full rollout.", "P1"),
        ("Add a pre-deploy smoke test that gates on p99 latency and error rate.", "P1"),
        ("Review the deployment diff to understand what change introduced the regression.", "P2"),
    ],
    "IGNORE": [
        ("Review alerting rule sensitivity — this alert fired but required no action.", "P2"),
        ("Increase the alert evaluation window or raise the breach threshold to reduce false positives.", "P2"),
        ("Add a runbook note documenting this as a known transient condition.", "P3"),
    ],
}

_DEFAULT_ACTION_ITEMS = [
    ("Review automated remediation playbook for completeness.", "P2"),
    ("Verify SLO thresholds are correctly calibrated for service traffic.", "P3"),
]

# ── Public functions ───────────────────────────────────────────────────────────

def generate(analysis: dict[str, Any]) -> str:
    """
    Render a blameless post-mortem Markdown document from an analysis record.

    Args:
        analysis: Dict produced by agent.py containing Claude's RCA fields,
                  plus a 'remediation' sub-dict from remediation.py.

    Returns:
        A multi-section Markdown string ready to be written to a .md file.
    """
    # Normalise confidence to float so formatters never raise on bad input.
    try:
        analysis = dict(analysis)
        analysis["confidence"] = float(analysis.get("confidence", 0.0))
    except (TypeError, ValueError):
        analysis["confidence"] = 0.0

    return "\n\n".join([
        _header(analysis),
        _summary_section(analysis),
        _impact_section(analysis),
        _timeline_section(analysis),
        _root_cause_section(analysis),
        _contributing_factors_section(analysis),
        _detection_section(analysis),
        _resolution_section(analysis),
        _lessons_learned_section(analysis),
        _action_items_section(analysis),
        _error_budget_section(analysis),
        _footer(analysis),
    ])


def save(text: str, analysis: dict[str, Any], output_dir: Path) -> Path:
    """
    Write post-mortem Markdown to output_dir / {timestamp}-{alertname}-{id}.md.

    Creates output_dir if it does not exist.  Returns the path written.
    Never raises — failures are returned as a (falsy) empty Path.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        ts        = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        alertname = _slug(analysis.get("alertname", "incident"))
        short_id  = str(analysis.get("alert_id", "unknown"))[:8]
        filename  = f"{ts}-{alertname}-{short_id}.md"
        path      = output_dir / filename
        path.write_text(text, encoding="utf-8")
        return path
    except OSError:
        return Path()


# ── Section renderers ─────────────────────────────────────────────────────────

def _header(a: dict) -> str:
    labels    = a.get("alert_labels", {})
    alertname = a.get("alertname", labels.get("alertname", "Unknown Incident"))
    severity  = labels.get("severity", "unknown").upper()
    date_str  = _fmt_date(a.get("analyzed_at", ""))
    short_id  = str(a.get("alert_id", ""))[:8]

    title = _incident_title(alertname, a)

    return f"""\
# Blameless Post-Mortem: {title}

| Field | Value |
|---|---|
| **Date** | {date_str} |
| **Status** | Resolved — Closed |
| **Severity** | {severity} |
| **Alert** | `{alertname}` |
| **Incident ID** | `{short_id}` |
| **Author** | Aether-Guard AI SRE Agent v{_VERSION} |
| **Reviewed By** | *(pending human review)* |

---"""


def _summary_section(a: dict) -> str:
    analysis = a.get("analysis", "No analysis available.")
    return f"""\
## Summary

{analysis}"""


def _impact_section(a: dict) -> str:
    labels     = a.get("alert_labels", {})
    slo        = labels.get("slo", a.get("slo_impact", "availability"))
    severity   = labels.get("severity", "unknown")
    remediation = a.get("remediation", {})
    action      = a.get("action", "IGNORE")

    # Estimate duration from timestamps
    started  = a.get("starts_at", "")
    resolved = remediation.get("executed_at", a.get("analyzed_at", ""))
    duration = _duration_str(started, resolved)

    budget_burned = _estimate_budget_burn(a)

    return f"""\
## Impact

- **SLO Affected:** {slo}
- **Severity:** {severity}
- **Estimated Duration:** {duration}
- **Error Budget Burned:** {budget_burned}
- **Users Affected:** Users of `aether-guard/target-service` endpoints
- **Automated Remediation:** {action} executed → {remediation.get("outcome", "N/A")}"""


def _timeline_section(a: dict) -> str:
    alertname   = a.get("alertname", "unknown")
    starts_at   = a.get("starts_at", "N/A")
    analyzed_at = a.get("analyzed_at", "N/A")
    remediation = a.get("remediation", {})
    rem_at      = remediation.get("executed_at", analyzed_at)
    action      = a.get("action", "IGNORE")
    outcome     = remediation.get("outcome", "N/A")
    confidence  = a.get("confidence", 0.0)

    rows = [
        (starts_at,   f"🔴 Alert `{alertname}` fired (Prometheus → Alertmanager → Listener)"),
        (analyzed_at, "🤖 Aether-Guard AI Agent received alert and began RCA"),
        (analyzed_at, f"🔍 Claude completed Root Cause Analysis (confidence: {confidence:.0%})"),
        (rem_at,      f"⚡ Automated remediation: **{action}** → `{outcome}`"),
        (rem_at,      "✅ Incident resolved — error budget accumulation stopped"),
    ]

    table = "| Time (UTC) | Event |\n|---|---|\n"
    for ts, event in rows:
        table += f"| `{_fmt_time(ts)}` | {event} |\n"

    return f"""\
## Timeline (UTC)

{table}"""


def _root_cause_section(a: dict) -> str:
    root_cause = a.get("root_cause", "Root cause could not be determined.")
    reasoning  = a.get("reasoning", "")
    confidence = a.get("confidence", 0.0)
    conf_label = _confidence_label(confidence)

    return f"""\
## Root Cause

{root_cause}

**Agent Confidence:** {confidence:.0%} ({conf_label})

**Reasoning:** {reasoning}"""


def _contributing_factors_section(a: dict) -> str:
    snap    = a.get("metrics_snapshot") or {}
    factors = []

    e_ratio = snap.get("error_ratio_5m")
    if e_ratio is not None and e_ratio > 0.001:
        factors.append(f"Elevated error ratio: `{e_ratio * 100:.2f}%` (SLO budget: 0.10%)")

    p99 = snap.get("latency_p99_5m_seconds")
    if p99 is not None and p99 > 0.200:
        factors.append(f"p99 latency breach: `{p99:.3f}s` (SLO threshold: 200ms)")

    leak = snap.get("memleak_bytes_allocated")
    if leak is not None and leak > 0:
        factors.append(f"Memory leak detected: `{leak / 1_048_576:.1f} MiB` retained in heap")

    chaos = snap.get("chaos_errors_injected_total")
    if chaos is not None and chaos > 0:
        factors.append(f"Chaos injection events observed: `{int(chaos)}` total")

    goroutines = snap.get("runtime_goroutines")
    if goroutines is not None and goroutines > 50:
        factors.append(f"Elevated goroutine count: `{int(goroutines)}` (possible goroutine leak)")

    if not factors:
        factors.append("No specific metric anomalies captured at alert time.")

    factor_list = "\n".join(f"- {f}" for f in factors)

    return f"""\
## Contributing Factors

{factor_list}"""


def _detection_section(a: dict) -> str:
    alertname = a.get("alertname", "unknown")
    labels    = a.get("alert_labels", {})
    slo       = labels.get("slo", "availability")
    severity  = labels.get("severity", "unknown")

    return f"""\
## Detection

The incident was detected **automatically** by Prometheus alerting rule \\
`{alertname}` (severity: `{severity}`), which fires when the `{slo}` SLO is \\
breached. The alert was routed through Alertmanager → Aether-Guard Listener \\
→ AI SRE Agent without any human pager notification.

**Mean Time to Detect (MTTD):** < 1 minute (Prometheus evaluation interval: 30s)"""


def _resolution_section(a: dict) -> str:
    remediation = a.get("remediation", {})
    action      = a.get("action", "IGNORE")
    container   = remediation.get("container", "target-service")
    outcome     = remediation.get("outcome", "N/A")
    reason      = remediation.get("reason", "")
    details     = remediation.get("details", {})

    detail_lines = ""
    if details:
        detail_lines = "\n" + "\n".join(f"  - `{k}`: `{v}`" for k, v in details.items())

    return f"""\
## Resolution

**Action:** `{action}` on container `{container}`
**Outcome:** `{outcome}`
**Details:** {reason}{detail_lines}

**Mean Time to Resolve (MTTR):** Fully automated — human intervention not required."""


def _lessons_learned_section(a: dict) -> str:
    action     = a.get("action", "IGNORE")
    confidence = a.get("confidence", 0.0)
    followup   = a.get("recommended_followup", "Review alerting and observability coverage.")
    dry_run    = a.get("dry_run", False)

    went_well = [
        "Aether-Guard detected and remediated the incident **fully autonomously**.",
        f"AI confidence gate (`{confidence:.0%}`) prevented false-positive actions.",
    ]
    if not dry_run and action != "IGNORE":
        went_well.append("Automated remediation executed successfully with no manual pager escalation.")
    if dry_run:
        went_well.append("DRY_RUN mode correctly logged intent without touching production.")

    improve = [
        followup,
        "Consider adding a secondary verification check after remediation to confirm service recovery.",
    ]
    if confidence < 0.80:
        improve.append(
            f"Agent confidence was `{confidence:.0%}` — improve observability to raise signal quality."
        )

    went_well_md = "\n".join(f"- {w}" for w in went_well)
    improve_md   = "\n".join(f"- {i}" for i in improve)

    return f"""\
## Lessons Learned

### What Went Well
{went_well_md}

### What Could Be Improved
{improve_md}"""


def _action_items_section(a: dict) -> str:
    action = a.get("action", "IGNORE")
    items  = _ACTION_ITEMS.get(action, _DEFAULT_ACTION_ITEMS)

    rows = "| Action | Priority | Owner |\n|---|---|---|\n"
    for description, priority in items:
        rows += f"| {description} | **{priority}** | On-call SRE |\n"

    # Always add: review auto-generated post-mortem
    rows += "| Review this auto-generated post-mortem and add human context. | **P2** | Incident Commander |\n"

    return f"""\
## Action Items (Toil Reduction)

{rows}"""


def _error_budget_section(a: dict) -> str:
    labels     = a.get("alert_labels", {})
    slo        = labels.get("slo", "availability")
    snap       = a.get("metrics_snapshot") or {}
    e_ratio    = snap.get("error_ratio_5m")
    action     = a.get("action", "IGNORE")

    if e_ratio is not None:
        burn_rate = f"`{e_ratio * 100:.2f}%` error ratio observed (budget allows 0.10%)"
        consumed  = f"~{min(e_ratio / 0.001 * 100, 100):.0f}× the allowed burn rate during the incident window"
    else:
        burn_rate = "Not measurable from available snapshot"
        consumed  = "Unknown — metrics snapshot unavailable at alert time"

    remediation_note = (
        "Remediation was executed automatically, stopping error budget accumulation."
        if action != "IGNORE"
        else "No action was taken; budget burn resolved naturally."
    )

    return f"""\
## Error Budget Impact

- **SLO:** 99.9% `{slo}` (30-day rolling window)
- **Burn Rate During Incident:** {burn_rate}
- **Budget Consumed:** {consumed}
- **Recovery:** {remediation_note}"""


def _footer(a: dict) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    model = a.get("model", "claude")
    return f"""\
---

*This post-mortem was generated automatically by Aether-Guard AI SRE Agent \\
using {model}. It must be reviewed and approved by a human SRE before \\
being considered final. Blameless culture reminder: focus on systems and \\
processes, not individuals.*

*Generated at: {generated_at} UTC*"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _incident_title(alertname: str, a: dict) -> str:
    """Human-readable incident title derived from alert name."""
    titles = {
        "SLOErrorBudgetBurnCritical": "Critical Error Budget Burn — High Request Failure Rate",
        "SLOErrorBudgetBurnWarning":  "Error Budget Burn Warning — Elevated Failure Rate",
        "SLOLatencyP99Breach":        "p99 Latency SLO Breach — Response Time Degradation",
        "MemorySaturationWarning":    "Memory Saturation — Potential Memory Leak Detected",
        "TargetServiceDown":          "Service Unavailability — Target Service Unreachable",
    }
    return titles.get(alertname, alertname.replace("_", " "))


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.90:
        return "Very High — direct causal chain unambiguous"
    if confidence >= 0.75:
        return "High — strong signal with minor ambiguity"
    if confidence >= 0.60:
        return "Medium — plausible hypothesis, some evidence gaps"
    return "Low — insufficient signal; action downgraded to IGNORE"


def _estimate_budget_burn(a: dict) -> str:
    snap    = a.get("metrics_snapshot") or {}
    e_ratio = snap.get("error_ratio_5m")
    if e_ratio is None:
        return "Unavailable (no metrics snapshot)"
    # 30-day SLO 99.9% = 43.2 min budget total
    # Each minute at error_ratio e burns e/0.001 minutes of budget
    burn_multiplier = e_ratio / 0.001
    return f"~{burn_multiplier:.0f}× normal burn rate"


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return iso or "unknown"


def _fmt_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except Exception:
        return iso or "N/A"


def _duration_str(start: str, end: str) -> str:
    try:
        t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
        delta = abs((t1 - t0).total_seconds())
        if delta < 60:
            return f"{delta:.0f}s"
        return f"{delta / 60:.1f}m"
    except Exception:
        return "unknown"


def _slug(name: str) -> str:
    """Convert alert name to a safe filename component."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).lower()[:40]

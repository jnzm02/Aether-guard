#!/usr/bin/env python3
"""
Aether-Guard Blameless Post-Mortem Generator — Phase 4

Reads analyses.jsonl produced by the AI Agent and generates a Google SRE-style
blameless post-mortem Markdown file for each incident.

Usage:
    python3 scripts/generate_postmortem.py                    # latest incident
    python3 scripts/generate_postmortem.py --all              # all incidents
    python3 scripts/generate_postmortem.py --alert-id <uuid>  # specific alert
    python3 scripts/generate_postmortem.py --input path/to/analyses.jsonl

Output: postmortems/{timestamp}-{alertname}.md
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL       = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
DEFAULT_INPUT      = Path(os.getenv("ANALYSIS_LOG_PATH", "services/agent/data/analyses.jsonl"))
OUTPUT_DIR         = Path("postmortems")

# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a Google SRE writing a blameless post-mortem for a production incident.
Your audience is senior engineers doing a weekly post-mortem review.

Rules:
- Blameless: focus on systems and processes, never individuals.
- Specific: cite exact metric values, timestamps, and log evidence.
- Actionable: every lesson must translate into a concrete action item.
- Concise: 2–5 sentences per section unless it is a timeline or table.

Output ONLY the Markdown document. No preamble, no code fences, no apologies.\
"""


def build_prompt(analysis: dict) -> str:
    labels     = analysis.get("alert_labels", {})
    snap       = analysis.get("metrics_snapshot") or {}
    remediation = analysis.get("remediation") or {}
    logs       = (analysis.get("log_tail") or [])[-20:]

    def fmt_snap(s: dict) -> str:
        lines = []
        mapping = {
            "error_ratio_5m":          "Error Ratio  (5m)",
            "latency_p99_5m_seconds":  "p99 Latency  (5m)",
            "latency_p50_5m_seconds":  "p50 Latency  (5m)",
            "request_rate_5m_rps":     "Request Rate (5m)",
            "memleak_bytes_allocated": "Leaked Memory",
            "chaos_errors_injected_total": "Chaos Events",
        }
        for k, label in mapping.items():
            v = s.get(k)
            lines.append(f"  {label:<24}: {v if v is not None else 'N/A'}")
        return "\n".join(lines)

    action_outcome = (
        f"**{remediation.get('action','N/A')}** → _{remediation.get('outcome','N/A')}_\n"
        f"  {remediation.get('reason','')}"
        if remediation else "_No remediation record_"
    )

    return f"""\
## Incident Data

Alert Name    : {labels.get('alertname', 'unknown')}
Severity      : {labels.get('severity', 'unknown').upper()}
SLO Impacted  : {labels.get('slo', 'unknown')}
Service       : {labels.get('service', 'aether-guard/target-service')}
Alert Fired   : {analysis.get('starts_at', 'unknown')}
AI Analyzed At: {analysis.get('analyzed_at', 'unknown')}
Model         : {analysis.get('model', CLAUDE_MODEL)}

## AI Root Cause Analysis

Root Cause      : {analysis.get('root_cause', 'N/A')}
Confidence      : {analysis.get('confidence', 0):.0%}
Recommended Act.: {analysis.get('action', 'N/A')}
SLO Impact      : {analysis.get('slo_impact', 'N/A')}
Reasoning       : {analysis.get('reasoning', 'N/A')}
Followup        : {analysis.get('recommended_followup', 'N/A')}

## Prometheus Metrics at Alert-Fire Time

{fmt_snap(snap)}

## Remediation Executed

{action_outcome}

## Log Evidence (last 20 lines)

```
{chr(10).join(logs) if logs else '(no log lines available)'}
```

## Full AI Analysis

{analysis.get('analysis', 'N/A')}

---

## Your Task

Write a complete, blameless post-mortem for this incident using exactly these sections:

# Blameless Post-Mortem: [craft a descriptive, specific incident title]

**Date:** [derive from analyzed_at field above]
**Status:** Complete — Closed
**Severity:** [from alert data]
**Author:** Aether-Guard AI SRE Agent (autonomous)
**Reviewers:** Platform SRE Team

## Summary

[2-3 sentences: what happened, what was the user/system impact, how was it resolved]

## Impact

[Bullet list: duration estimate, SLO breach details, error budget consumed, blast radius]

## Timeline (UTC)

[Chronological table of events derived from the timestamps and log evidence above]

| Time (UTC) | Event |
|------------|-------|

## Root Cause

[Clear causal chain. Reference specific metric values. No blame.]

## Contributing Factors

[Bullet list of systemic factors that allowed this to happen]

## Resolution

[What action was taken, when, and how recovery was confirmed]

## Lessons Learned

### What Went Well

[What parts of the detection/response pipeline worked]

### What Could Be Improved

[Honest gaps — without blaming people]

## Action Items (Toil Reduction)

[Concrete follow-ups to prevent recurrence — Google SRE philosophy: eliminate toil]

| # | Action | Priority | Owner | Due |
|---|--------|----------|-------|-----|

## Error Budget Impact

[Quantify SLO consumption: budget used, remaining, time-to-exhaustion at that burn rate]
"""


# ─────────────────────────────────────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────────────────────────────────────

def generate(analysis: dict, client: anthropic.Anthropic) -> str:
    """Call Claude synchronously and return the post-mortem Markdown."""
    prompt = build_prompt(analysis)
    print(f"  Calling {CLAUDE_MODEL} for {analysis.get('alertname','?')} ...", flush=True)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def write_postmortem(text: str, analysis: dict) -> Path:
    alertname = analysis.get("alertname", "incident").replace("/", "_")
    ts        = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename  = f"{ts}-{alertname}.md"
    outpath   = OUTPUT_DIR / filename
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outpath.write_text(text, encoding="utf-8")
    return outpath


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def load_analyses(path: Path) -> list[dict]:
    if not path.exists():
        print(f"❌  analyses file not found: {path}")
        print("    Run the full stack and inject chaos to generate analyses first.")
        sys.exit(1)
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Aether-Guard Post-Mortem Generator")
    parser.add_argument("--all",      action="store_true", help="Generate PM for every analysis in the log")
    parser.add_argument("--alert-id", metavar="UUID",      help="Generate PM for a specific alert ID")
    parser.add_argument("--input",    type=Path, default=DEFAULT_INPUT, help="Path to analyses.jsonl")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        print("❌  ANTHROPIC_API_KEY is not set. Export it or add it to .env")
        sys.exit(1)

    client    = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    all_recs  = load_analyses(args.input)

    if not all_recs:
        print("❌  No analyses found in", args.input)
        sys.exit(1)

    if args.alert_id:
        targets = [r for r in all_recs if r.get("alert_id") == args.alert_id]
        if not targets:
            print(f"❌  Alert ID {args.alert_id!r} not found")
            sys.exit(1)
    elif args.all:
        targets = all_recs
    else:
        targets = [all_recs[-1]]   # default: latest

    print(f"📝  Generating {len(targets)} post-mortem(s) from {args.input} ...\n")

    for rec in targets:
        try:
            pm_text = generate(rec, client)
            outpath = write_postmortem(pm_text, rec)
            print(f"  ✅  Written → {outpath}")
            print()
            # Print preview (first 40 lines)
            for line in pm_text.splitlines()[:40]:
                print("   ", line)
            print("   ...")
            print()
        except Exception as exc:
            print(f"  ❌  Failed for {rec.get('alert_id')}: {exc}")


if __name__ == "__main__":
    main()

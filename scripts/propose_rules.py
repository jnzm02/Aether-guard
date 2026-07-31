#!/usr/bin/env python3
"""Propose new deterministic RCA rules from recorded incidents — PROPOSAL ONLY.

Aether-Guard's RuleEngine handles the common incident patterns deterministically;
anything it can't match escalates to the LLM. Over time, the recorded incident
history (the same JSONL that `replay.py` consumes) reveals *recurring* patterns the
rules keep missing — good candidates for a new deterministic rule.

This tool mines that history and emits a **report** of candidate rules. It never
edits `rules.py`, `policy.py`, `remediation.py`, or anything else — by design. The
loop is deliberately gated:

    propose (this tool)  ->  a human writes the rule  ->  validate  ->  merge

    Validate a hand-written rule before merging it:
        python3 services/agent/replay.py --replay-all      # RCA accuracy must not regress
        python3 .claude/evals/run.py                        # agent behavior evals

Nothing here touches a safety layer. It surfaces where the rules fall short so a
human can close the gap with review + replay.

Usage:
    python3 scripts/propose_rules.py --incidents path/to/incidents.jsonl
    python3 scripts/propose_rules.py --incidents ... --min-count 3 --out proposals.md
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_incidents(path):
    records = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"warning: skipping malformed line {i}: {e}", file=sys.stderr)
    return records


def agent_missed(rec):
    """Heuristic: the rule/agent path did not confidently reach the ground truth."""
    gt = rec.get("ground_truth_root_cause")
    if not gt:
        return False  # no ground truth → can't learn from it
    agent_rc = rec.get("agent_root_cause")
    conf = rec.get("agent_confidence")
    # Missed if the agent got the root cause wrong, or was unsure (low/absent conf).
    if agent_rc != gt:
        return True
    if conf is None or conf < 0.75:
        return True
    return False


def common_log_signals(logs_lists, top=5):
    """Cheap signal extraction: words that recur across incidents' logs.

    Deliberately simple — these are hints for a human to turn into a real regex,
    not a finished pattern.
    """
    per_incident_tokens = []
    for logs in logs_lists:
        toks = set()
        for line in logs or []:
            for w in re.findall(r"[A-Za-z_][A-Za-z0-9_\-]{3,}", line.lower()):
                toks.add(w)
        per_incident_tokens.append(toks)
    n = len(per_incident_tokens) or 1
    tally = Counter()
    for toks in per_incident_tokens:
        tally.update(toks)
    # Keep tokens present in a majority of incidents, minus obvious noise.
    noise = {"info", "warn", "error", "debug", "true", "false", "none", "null"}
    return [(w, c) for w, c in tally.most_common(40)
            if c >= max(2, n // 2) and w not in noise][:top]


def elevated_metrics(recs, top=5):
    """Metric names most frequently present (non-null) across the group."""
    tally = Counter()
    for r in recs:
        for k, v in (r.get("metrics") or {}).items():
            if v is not None:
                tally[k] += 1
    return tally.most_common(top)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--incidents", default="services/agent/data/incidents.jsonl",
                    help="path to the incidents JSONL (same file replay.py uses)")
    ap.add_argument("--min-count", type=int, default=3,
                    help="minimum recurring incidents to propose a rule (default 3)")
    ap.add_argument("--out", help="write the report here (default: stdout)")
    args = ap.parse_args()

    path = Path(args.incidents)
    if not path.exists():
        print(f"error: incidents file not found: {path}", file=sys.stderr)
        print("Point --incidents at the JSONL replay.py records to "
              "(e.g. /app/data/incidents.jsonl from the running agent).", file=sys.stderr)
        sys.exit(1)

    records = load_incidents(path)
    missed = [r for r in records if agent_missed(r)]

    # Group missed incidents by (alert_name, ground_truth_root_cause).
    groups = defaultdict(list)
    for r in missed:
        key = (r.get("alert_name", "?"), r.get("ground_truth_root_cause", "?"))
        groups[key].append(r)

    candidates = {k: v for k, v in groups.items() if len(v) >= args.min_count}

    lines = []
    lines.append("# Candidate RCA rule proposals\n")
    lines.append(f"- Incidents scanned: **{len(records)}**")
    lines.append(f"- Escalated / mis-diagnosed (learnable): **{len(missed)}**")
    lines.append(f"- Recurring gaps (≥{args.min_count}): **{len(candidates)}**\n")
    lines.append("> Proposal only — no code was changed. For each candidate, a human "
                 "writes a `_check_*` rule in `services/agent/rules.py`, then validates "
                 "with `python3 services/agent/replay.py --replay-all` (accuracy must not "
                 "regress) and `python3 .claude/evals/run.py` before merging.\n")

    if not candidates:
        lines.append("_No recurring gaps met the threshold. The rules are keeping up._")
    for (alert, root_cause), recs in sorted(candidates.items(), key=lambda kv: -len(kv[1])):
        actions = Counter(r.get("ground_truth_action") for r in recs)
        rec_action = actions.most_common(1)[0][0]
        signals = common_log_signals([r.get("logs") for r in recs])
        metrics = elevated_metrics(recs)
        ids = [r.get("incident_id", "?") for r in recs][:8]

        lines.append(f"\n## `{alert}` → `{root_cause}`  ({len(recs)} incidents)")
        lines.append(f"- **Recommended action** (ground-truth majority): "
                     f"`{rec_action}`  {dict(actions)}")
        lines.append("- **Candidate log signals** (hints, refine into a regex): "
                     + (", ".join(f"`{w}`×{c}" for w, c in signals) or "_none obvious_"))
        lines.append("- **Metrics usually present**: "
                     + (", ".join(f"`{m}`×{c}" for m, c in metrics) or "_none_"))
        lines.append(f"- **Supporting incidents**: {', '.join(ids)}")
        lines.append("- **Sketch**: add a `_check_*` returning a `RuleMatch(matched=True, "
                     f"root_cause=RootCauseCategory.{root_cause.upper()}, "
                     f"recommended_action=\"{rec_action}\", ...)` keyed on the signals above.")

    report = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(report)
        print(f"wrote {args.out} ({len(candidates)} candidate rule(s))")
    else:
        print(report)


if __name__ == "__main__":
    main()

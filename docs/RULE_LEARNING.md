# Replay-gated rule learning

Aether-Guard's `RuleEngine` handles common incidents deterministically; the rest escalate
to the LLM. The recorded incident history shows where the rules keep falling short — and
those recurring gaps are candidates for new deterministic rules. This is how the agent
"learns" new rules **without ever self-modifying safety-critical code.**

## The loop (strictly gated)

```
propose            a human            validate                     merge
(tool, read-only)  writes the rule    replay + evals must pass     (human review)
```

1. **Propose** — `scripts/propose_rules.py` mines the incident JSONL for recurring
   patterns the agent mis-diagnosed or was unsure about, and writes a **report** of
   candidate rules. It does **not** edit any code.
2. **Write** — a human (or Claude in a reviewed PR) turns a candidate into a `_check_*`
   method in `services/agent/rules.py`.
3. **Validate** — the change must pass the gates before it can merge:
   ```bash
   python3 services/agent/replay.py --replay-all   # RCA accuracy on history must not regress
   python3 .claude/evals/run.py                      # agent-behavior evals
   ```
4. **Merge** — only after human review.

## Running the proposer

```bash
python3 scripts/propose_rules.py --incidents /path/to/incidents.jsonl
python3 scripts/propose_rules.py --incidents ... --min-count 3 --out proposals.md
```

`--incidents` points at the same JSONL `replay.py` reads (the running agent records to
`/app/data/incidents.jsonl`). For each recurring gap it reports: the recommended action
(ground-truth majority), candidate log signals to turn into a regex, the metrics usually
present, and the supporting incident IDs.

## Why it's safe

- **Proposal-only.** The tool has no write path to `rules.py`, `policy.py`,
  `remediation.py`, thresholds, or `DRY_RUN`. It reads history and prints a report.
- **Replay is the gate.** A new rule can't lower RCA accuracy on the historical dataset
  without `replay.py --replay-all` catching it.
- **Human in the loop.** Nothing merges without review. The agent proposes; humans dispose.

## Heuristics (and their limits)

"Missed" = the agent's `agent_root_cause` disagreed with the human `ground_truth_root_cause`,
or `agent_confidence` was below 0.75. Signal extraction is deliberately crude (frequent
log tokens) — it produces *hints* for a human to refine into a real pattern, not a finished
rule. Treat the output as a lead, not a spec.

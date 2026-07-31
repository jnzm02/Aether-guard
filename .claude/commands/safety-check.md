---
description: Run the safety-reviewer on the current diff (remediation/policy/verification)
---
Review the current working diff for any weakening of Aether-Guard's 6-layer safety
pipeline (rules → policy → approval → remediation → verification → rollback).

Do this:
1. Gather the diff: `git diff` (unstaged) and `git diff --cached` (staged).
2. If nothing under `services/agent/` touches safety-critical logic
   (`policy.py`, `remediation.py`, `verification.py`, confidence/`DRY_RUN` handling),
   say so briefly and stop — no need to over-review.
3. Otherwise delegate to the **safety-reviewer** subagent.
4. Report findings ranked by severity, each with `file:line`, the concrete failure
   scenario (inputs → wrong/ungated action), and a suggested fix. Never rationalize
   away a weakened safety check.

---
description: Diagnose the most recent failed CD/CI run and propose a minimal fix
argument-hint: "[run-id, PR#, or blank for latest failure]"
---
Diagnose a deployment or CI failure in Aether-Guard and propose the minimal fix.

Target: $ARGUMENTS
(If blank, find the most recent **failed** GitHub Actions run on the current branch.)

Do this:
1. Get the concrete failure first — don't theorize.
   - If `gh` is available: `gh run list --branch <branch>`, then
     `gh run view <run-id> --log-failed` to read the failing job's log.
   - Otherwise inspect `.github/workflows/cd.yml`, `scripts/deploy.sh`, and the
     most recent commits (this area has a long history of deploy-debug fixes).
2. Delegate the analysis to the **deploy-debugger** subagent, handing it the actual
   failing log / error output.
3. Report: root cause with `file:line`, the failure mechanism (what actually breaks),
   and the smallest fix. Common classes here: health-check timing, grep matches
   tripping `set -e` traps, rollback leaving orphaned containers, missing build steps.
4. If the fix touches `scripts/deploy.sh` or a workflow file, **confirm before editing** —
   a bad deploy script breaks production rollout.

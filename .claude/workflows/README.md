# Workflows

Saved multi-agent workflow scripts for Aether-Guard. A workflow orchestrates a *team*
of agents deterministically (fan-out, adversarial verification, synthesis) — more
structure than a single agent, so you get independent perspectives cross-checked
before a decision.

## Available

| Workflow | What it does |
|----------|--------------|
| `feature-team.js` | Plan → parallel Design → adversarial Review → Synthesize. Takes a task and returns a reviewed, synthesized implementation **plan** (it does not edit files by default). |

## Running one

Workflows run via Claude Code's multi-agent orchestration, which is **opt-in** (it can
spawn many agents and use significant tokens). Ask for it explicitly, e.g.:

> "Use a workflow: run feature-team on 'add a /ready endpoint to the listener'."

Claude then invokes the workflow with your task as `args`, e.g. `{ task: "..." }`.

## Notes

- `feature-team` is a **planning** team by design — it produces a vetted plan, not code,
  so it's safe to run anytime. To turn it into a *building* team, add
  `isolation: 'worktree'` to the design-stage agents and have them edit files (each in
  its own worktree so parallel edits don't collide). See the comment at the bottom of
  the script.
- Every agent inherits this repo's harness — `CLAUDE.md`, the subagents, and the safety
  rules — so the team already knows not to weaken `DRY_RUN` / policy / thresholds.

# Claude in GitHub Actions

Two workflows put the agent harness into CI:

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `.github/workflows/claude.yml` | `@claude` in an issue / PR / review comment | Runs Claude Code in the repo. Can answer questions, investigate, or implement a fix as a commit/PR. Only acts for users with write access. |
| `.github/workflows/claude-review.yml` | every non-draft PR (`opened`, `reopened`, `synchronize`) | Posts one focused review comment. Uses the `safety-reviewer` subagent for agent-safety changes and flags likely CI failures. Never approves or merges. |

Both run the official [`anthropics/claude-code-action@v1`](https://github.com/anthropics/claude-code-action)
and inherit this repo's harness — `CLAUDE.md`, `.claude/agents/`, and `.mcp.json`.

## One-time setup

1. **Add the API key secret.** In GitHub: **Settings → Secrets and variables →
   Actions → New repository secret**
   - Name: `ANTHROPIC_API_KEY`
   - Value: an Anthropic API key with billing enabled.

   > This is separate from the `ANTHROPIC_API_KEY` **placeholder** the CI test job
   > sets inline — that one is a dummy for `DRY_RUN` tests. The workflows here need a
   > real key from the repository **secret** to call the model.

2. **Merge this PR.** The workflows activate once on `main`.

3. **Try it:** open an issue or PR comment containing `@claude ...`, or just open a PR
   and watch the review comment appear.

## Cost & scope controls (already applied)

- The review job skips **draft** PRs and uses `concurrency: cancel-in-progress`, so
  pushing new commits supersedes an in-flight review instead of stacking runs.
- The interactive job only starts when `@claude` is actually present (an `if:` guard),
  and the action itself refuses to act for users without write access.
- `--max-turns` bounds the review run.

## Tuning

- **Pin a model:** add `--model claude-sonnet-4-5` (or another id) to `claude_args`.
- **Restrict tools:** add `--allowedTools "Read,Grep,Glob,Bash(git diff:*)"` to
  `claude_args` on the review job to keep it read-only.
- **Narrow review triggers:** add a `paths:` filter under `pull_request` to review only
  changes to `services/**`, etc.
- **Auto-triage failed CD:** add a `workflow_run` trigger keyed off the CD workflow and
  a prompt that routes to the `deploy-debugger` subagent.

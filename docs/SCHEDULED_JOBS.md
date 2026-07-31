# Scheduled maintenance jobs

Cron-driven GitHub Actions that run maintenance work outside the per-PR pipeline.
All are also `workflow_dispatch` (a "Run workflow" button) so you can trigger them
on demand.

| Workflow | Schedule (UTC) | What it does |
|----------|----------------|--------------|
| `ci.yml` (nightly) | daily 05:00 | The full CI suite — Go + Python tests, config validation, Docker builds, integration smoke — re-run on `main` to catch drift a PR diff wouldn't surface. |
| `nightly-mutation.yml` | daily 06:00 | `mutmut` against the agent's safety-critical modules (scope + test command from `services/agent/pyproject.toml [tool.mutmut]`). Time-boxed, non-blocking; results in the job summary + an artifact. Surviving mutants = behavior no test pins down. |
| `weekly-deps-audit.yml` | Mondays 07:00 | `govulncheck` (Go services) + `pip-audit` (agent/listener requirements). Report-only — findings go to the job summary so the job doesn't stay permanently red. |

## Notes
- These are plain CI jobs — they do **not** require the Claude GitHub App or an
  `ANTHROPIC_API_KEY` (unlike the `@claude` / review workflows).
- Mutation testing config lives in two places for the two mutmut major versions:
  `pyproject.toml [tool.mutmut]` (v3, used by the workflow) and `setup.cfg [mutmut]`
  (v2). Keep the `source_paths` in sync if you change what's mutated.
- The dependency audit is intentionally non-failing. If you'd rather be paged on new
  vulnerabilities, drop the `|| true` and let the job go red.

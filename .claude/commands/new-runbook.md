---
description: Scaffold a new incident runbook in docs/runbooks/ using the repo's format
argument-hint: "<incident-slug> (e.g. disk-pressure)"
---
Create a new incident runbook at `docs/runbooks/$1.md` for the incident type "$1".

Match the existing runbooks exactly in structure and tone. Reference:
@docs/runbooks/high-latency.md

Requirements:
- Same section layout as the existing runbooks: thresholds → mitigation commands →
  PromQL investigation → escalation.
- Use **real** metric and alert names from this codebase (grep for `aether_guard`
  metrics and the alert rules in `infra/prometheus/rules/`). Do not invent endpoints,
  metrics, or services.
- Keep commands consistent with the Makefile targets and service ports already in use.
- After writing it, note whether `docs/runbooks/README.md` has an index that should
  also be updated.

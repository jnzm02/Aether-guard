---
name: verify
description: Verify a change to Aether-Guard actually works by exercising it end-to-end — run the affected tests AND, for agent/pipeline changes, drive a real incident through detect → RCA → policy → (dry-run) remediation → verification and observe the behavior. Use before committing a non-trivial change.
---

# Verify an Aether-Guard change

Goal: confirm a change does what it's supposed to by **observing runtime behavior**, not
just that tests pass. Pick the smallest path that actually exercises what changed.

## 0. Always: scope by what changed

Run `git diff --name-only` (staged + unstaged). Route to the sections below by path.
If nothing has a runtime surface (docs only), say so and stop — there's nothing to drive.

## 1. Fast checks (run for every change)

- **Python changed** (`services/agent/`, `services/listener/`, `scripts/`):
  ```bash
  make lint-py
  make test-py            # or test-agent / test-listener to narrow
  ```
  First run needs `make setup` (creates the `.venv`). Harmless
  `exporting traces to tempo:4317` warnings appear with no Tempo running — ignore them.
- **Go changed** (`services/target-service`, `services/event-tracker`):
  ```bash
  cd services/target-service && go build ./... && go vet ./... && go test -race -count=1 ./...
  ```
- **Prometheus / Alertmanager config** (`infra/`):
  ```bash
  promtool check config infra/prometheus/prometheus.yml
  promtool check rules infra/prometheus/rules/slo_alerts.yml
  amtool check-config infra/alertmanager/alertmanager.yml
  ```

Tests are necessary but **not sufficient** for agent-behavior changes — continue below.

## 2. End-to-end incident drive (for agent / pipeline / remediation / policy changes)

This is the real verification: does the 6-layer pipeline still behave correctly?

```bash
make docker-up          # build + start the full stack (target-service, listener,
                        # agent, Prometheus, Alertmanager, Grafana, Redis, Postgres)
make health-check       # all services report healthy before proceeding
```

Then inject a fault that matches what you changed and watch it flow through:

```bash
# choose the chaos that exercises your change:
make chaos-error        # 100% HTTP 500s      → high-error-rate path
make chaos-latency      # 3s latency spike     → high-latency path
make chaos-memleak      # 150 MiB leak         → memory-leak path

# wait for the alert to fire and the agent to analyze (~60-120s), then observe:
make alert-status       # Prometheus alert should be firing
make listener-pending   # alert enriched + queued for the agent
make agent-analyses     # the RCA the agent produced
make agent-remediation  # action chosen + outcome (DRY_RUN by default → no real action)
```

**What to confirm (map to the layers you touched):**
- **rules / RCA** — `agent-analyses` shows the expected `matched_pattern` / root cause and
  a sane confidence; low-confidence cases fall back to the LLM path as intended.
- **policy / gating** — the chosen `action` is allowed by policy; anything unsafe is gated.
- **remediation** — with `DRY_RUN=true` (default) it plans but does **not** act; the
  outcome reflects that. Never flip `DRY_RUN` off just to verify.
- **verification / rollback** — post-action metric check runs and would roll back if
  metrics didn't improve.

If the change touches safety-critical files, also run `/safety-check` (the
`safety-reviewer` subagent) on the diff.

Clean up:
```bash
make chaos-reset
make docker-down
```

## 3. Single-service drives (lighter, when the stack is overkill)

- **target-service only:**
  ```bash
  make build && make run          # then, in another shell:
  curl -s localhost:8080/api/users | python3 -m json.tool
  curl -s localhost:8080/metrics | grep aether_guard
  ```
- **A specific agent function:** prefer a focused test, e.g.
  `cd services/agent && "$PWD/../../.venv/bin/python" -m pytest tests/ -k <name> -v`.

## 4. Report

State what you **observed**, not just "tests pass":
- which checks ran and their results (paste the failing output verbatim if any),
- for an e2e drive: the actual alert → RCA → action → outcome you saw,
- anything that didn't match the intended behavior.

If you could not drive the change (e.g. Docker unavailable), say so explicitly rather
than implying it was verified.

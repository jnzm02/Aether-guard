# CLAUDE.md

Guidance for Claude Code when working in the Aether-Guard repository.

## What this is

Aether-Guard is an **autonomous AI SRE agent** for Kubernetes/Docker workloads. It
detects incidents, performs root-cause analysis, gates remediation through safety
policies, verifies the outcome, and auto-rolls-back when metrics don't improve.
The design goal is a **6-layer defense pipeline**: rules → policy → approval →
remediation → verification → rollback.

## Services

The system is a set of cooperating services under `services/`:

| Service | Language | Port | Role |
|---------|----------|------|------|
| `target-service` | Go | 8080 | Demo workload with chaos-injection endpoints (`/chaos/*`) and Prometheus metrics. The thing that "breaks." |
| `listener` | Python (FastAPI) | 8081 | Receives Alertmanager webhooks, enriches alerts with metric snapshots, queues them for the agent. |
| `agent` | Python (FastAPI) | 8082 | The SRE brain: hybrid RCA (rules + Claude LLM), policy gating, remediation, verification, post-mortems. |
| `event-tracker` | Go | — | Ingests real traffic signals (e.g. GitHub Events API) for validation. |

Supporting infra (Prometheus 9090, Alertmanager 9093, Grafana 3001, Redis, Postgres)
is defined in `infra/` and orchestrated via docker compose. Kubernetes manifests
live in `k8s/`.

### Agent internals (`services/agent/`)

The agent is the most complex service. Key modules:

- `agent.py` — main loop / FastAPI app (largest file, ~72k).
- `rules.py` — deterministic rules-based RCA (the fast, free, offline path).
- `prompt.py` — LLM prompt construction for the Claude-based RCA path.
- `policy.py` — action gating / safety policy.
- `remediation.py` — executes remediation actions.
- `verification.py` — post-remediation metric checks + rollback decision.
- `incident_storage.py` — Postgres + Redis persistence and analytics.
- `investigation_graph.py` — LangGraph-based investigation flow.
- `embedding.py` / `enrichment.py` — RAG similarity search over past incidents.
- `postmortem.py` / `incident_report.py` — post-mortem generation.

## Build & test commands

Prefer the **Makefile** for local workflows (`make help` lists all targets).

### Go (`target-service`, `event-tracker`)
```bash
make build            # build target-service to /tmp/target-service
make test             # go test ./... -v -race -count=1   (in target-service)
# direct, in a service dir:
cd services/target-service && go build ./... && go vet ./... && go test -race -count=1 ./...
```

### Python (`agent`, `listener`)
```bash
# Lint (CI uses ruff 0.4.4):
ruff check services/agent/ services/listener/ scripts/

# Agent tests (set PYTHONPATH to the service dir):
cd services/agent && PYTHONPATH=$PWD python3 -m pytest tests/ -v --tb=short

# Listener tests (needs importlib import mode):
cd services/listener && python3 -m pytest tests/ --import-mode=importlib -v
```
Python is **3.11**. Tests run with `ANTHROPIC_API_KEY` set to a placeholder and
`DRY_RUN=true` — no real Claude calls or real remediation happen in tests.

### Full stack
```bash
make docker-up        # build + start everything (compose in infra/)
make health-check     # probe /health on every service
make demo-e2e         # inject chaos → wait for alert → show AI analysis
make docker-down      # tear down (removes volumes)
```

## CI expectations

`.github/workflows/ci.yml` is the source of truth. A change must pass:
1. Go: `go build`, `go vet`, `go test -race` in `services/target-service`.
2. Python: `ruff check` on agent/listener/scripts, then pytest for agent + listener.
3. Infra: `promtool`/`amtool` validation of Prometheus & Alertmanager configs.
4. Docker build of all images + a compose smoke test.

Before proposing a change is done, run the relevant lint + tests locally.
`.github/workflows/cd.yml` handles deployment (the recent commit history is
CD/deploy debugging — be careful editing `scripts/deploy.sh`).

## Conventions

- **LLM usage**: default to the latest Claude models. Existing config references
  `claude-sonnet-4-5-20250929` via `CLAUDE_MODEL`; the newest models are the Claude 5
  family and Opus/Haiku 4.x — prefer those for new work unless matching existing config.
- **Safety first**: this system takes real remediation actions. Preserve the
  `DRY_RUN` guard, confidence thresholds, and policy gating. Never weaken a safety
  layer to make a test pass.
- **Secrets**: `.env` / `.env.production` are gitignored. Use `.env.example` as the
  template. Never commit `ANTHROPIC_API_KEY` or other credentials.
- **Commit style**: conventional commits (`fix(cd):`, `debug(deploy):`, `feat:` …),
  matching existing history. Branch off `main` for PRs.
- Config is environment-driven (`PROMETHEUS_URL`, `TARGET_CONTAINER`,
  `CONFIDENCE_THRESHOLD`, `POLL_INTERVAL`, etc.) — see `.env.example`.

## Docs worth reading

- `README.md` — full architecture and the V2 6-layer pipeline.
- `docs/ARCHITECTURE_V2.md`, `docs/CICD-ARCHITECTURE.md` — deeper design.
- `docs/BRING_YOUR_OWN_SERVICE.md` — onboarding a new monitored service.
- `docs/runbooks/` — per-incident-type response runbooks.

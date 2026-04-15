# Aether-Guard 🛡️

> **Autonomous SRE AI Agent** — monitors a microservice, detects failures using Prometheus SLO burn-rate alerting, performs Root Cause Analysis with Claude AI, and executes automated remediation with blameless post-mortem generation.

[![CI](https://github.com/jnzm02/Aether-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/jnzm02/Aether-guard/actions/workflows/ci.yml)
[![CD](https://github.com/jnzm02/Aether-guard/actions/workflows/cd.yml/badge.svg)](https://github.com/jnzm02/Aether-guard/actions/workflows/cd.yml)
![Go](https://img.shields.io/badge/Go-1.21-00ADD8?logo=go)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python)
![Prometheus](https://img.shields.io/badge/Prometheus-2.48-E6522C?logo=prometheus)
![Claude](https://img.shields.io/badge/Claude-Sonnet-8A2BE2)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)
![Kubernetes](https://img.shields.io/badge/Kubernetes-manifests-326CE5?logo=kubernetes)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         AETHER-GUARD STACK                          │
│                                                                     │
│  ┌──────────────────┐   scrape/5s   ┌──────────────────────────┐   │
│  │  target-service  │◄──────────────│       Prometheus         │   │
│  │  (Go, :8080)     │               │  (:9090)                 │   │
│  │                  │               │  • SLO recording rules   │   │
│  │  Chaos Endpoints:│               │  • Multi-burn-rate alerts│   │
│  │  /chaos/memleak  │               └──────────┬───────────────┘   │
│  │  /chaos/latency  │                          │ alert fired        │
│  │  /chaos/error    │               ┌──────────▼───────────────┐   │
│  │                  │               │      Alertmanager         │   │
│  │  Golden Signals: │               │  (:9093)                 │   │
│  │  • request_rate  │               │  • Routing + inhibitions │   │
│  │  • error_ratio   │               └──────────┬───────────────┘   │
│  │  • p99_latency   │                          │ POST /webhook      │
│  │  • mem_leak_bytes│               ┌──────────▼───────────────┐   │
│  └──────────────────┘               │        Listener           │   │
│                                     │  (Python/FastAPI, :8081)  │   │
│                                     │  • Enriches alert with:   │   │
│                                     │    - Prometheus metrics   │   │
│                                     │    - Docker container logs│   │
│                                     └──────────┬───────────────┘   │
│                                                │ poll every 10s    │
│                                     ┌──────────▼───────────────┐   │
│                                     │       AI SRE Agent        │   │
│                                     │  (Python/FastAPI, :8082)  │   │
│                                     │                           │   │
│                                     │  Claude AI ──► RCA JSON   │   │
│                                     │  {analysis, root_cause,   │   │
│                                     │   confidence, action,     │   │
│                                     │   slo_impact}             │   │
│                                     │                           │   │
│                                     │  Remediation Engine:      │   │
│                                     │  RESTART │ SCALE │ IGNORE │   │
│                                     │                           │   │
│                                     │  Post-Mortem Generator ──►│──►│── postmortems/*.md
│                                     └───────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Alert Pipeline

```
Metric breach → Prometheus rule fires (15s eval) →
  Alertmanager routes (5s group_wait for CRITICAL) →
    Listener enriches (metrics snapshot + 100 log lines) →
      Agent polls (10s) → Claude API (RCA + action) →
        Remediation executes (Docker API) →
          Post-mortem generated (Markdown)
```

---

## SLO Contract

| Signal | Target | Alert Threshold | Severity |
|--------|--------|----------------|----------|
| Availability | 99.9% non-5xx | >5% error rate for 2m (50× burn) | **CRITICAL** |
| Availability | 99.9% non-5xx | >0.5% error rate for 5m (5× burn) | WARNING |
| Latency | p99 < 200ms | p99 > 200ms for 2m | **CRITICAL** |
| Saturation | Memory | >100 MiB leak for 1m | WARNING |
| Availability | Service Up | `up == 0` for 30s | **CRITICAL** |

Error budget: **43.2 minutes / 30-day window** (0.1% of requests allowed to fail).

Alert methodology: [Google SRE Workbook — Multi-Window, Multi-Burn-Rate Alerts](https://sre.google/workbook/alerting-on-slos/).

---

## Project Structure

```
aether-guard/
├── services/
│   ├── target-service/          # Go microservice with chaos endpoints
│   │   ├── cmd/server/main.go
│   │   └── internal/
│   │       ├── chaos/           # MemLeak, Latency, Error injection + tests
│   │       ├── handlers/        # /api/users, /api/orders, /health + tests
│   │       └── metrics/         # Prometheus instruments + middleware + tests
│   ├── listener/                # Python alert enrichment service
│   │   ├── listener.py          # FastAPI webhook + Prometheus + Docker log fetch
│   │   └── tests/               # 14 pytest unit tests
│   └── agent/                   # Python AI SRE agent
│       ├── agent.py             # Polling loop + FastAPI endpoints
│       ├── prompt.py            # Claude system prompt + context builder
│       ├── remediation.py       # Docker SDK remediation engine (safety gates)
│       └── tests/               # 44 pytest unit tests
├── infra/
│   ├── docker-compose.yml       # Full 6-service stack
│   ├── prometheus/
│   │   ├── prometheus.yml       # Scrape config + alerting stanza
│   │   └── rules/slo_alerts.yml # 5 SLO-based alert rules + recording rules
│   ├── alertmanager/
│   │   └── alertmanager.yml     # Routing + inhibit rules
│   └── grafana/                 # Auto-provisioned SLO dashboard (21 panels)
│       ├── provisioning/
│       └── dashboards/
├── k8s/                         # Production Kubernetes manifests (Kustomize)
│   ├── namespace.yaml
│   ├── target-service.yaml      # Deployment + Service + HPA (2→10 pods)
│   ├── prometheus.yaml          # RBAC + ConfigMap + PVC + Deployment
│   ├── alertmanager.yaml
│   ├── listener.yaml
│   ├── agent.yaml               # Secret + PVC + Deployment
│   ├── grafana.yaml
│   └── kustomization.yaml
├── docs/
│   └── runbooks/                # SRE runbooks for all 5 alert types
│       ├── high-error-rate.md
│       ├── high-latency.md
│       ├── memory-leak.md
│       └── service-down.md
├── scripts/
│   ├── load_gen.py              # Traffic generator with chaos scenarios
│   └── generate_postmortem.py  # Standalone post-mortem CLI
├── postmortems/                 # Auto-generated blameless post-mortems
├── .github/workflows/ci.yml     # 6-job CI pipeline (see CI section)
├── .env.example                 # Environment variable template
└── Makefile                     # Developer ergonomics
```

---

## Quick Start

### Prerequisites

- Docker Desktop (or Docker Engine + Compose plugin)
- Python 3.11+ (for local scripts only)
- An [Anthropic API key](https://console.anthropic.com/)

### 1. Clone & configure

```bash
git clone https://github.com/jnzm02/Aether-guard.git
cd Aether-guard
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY
```

### 2. Start the full stack

```bash
make docker-up
```

All 6 services start in dependency order. Verify:

```bash
make health-check   # checks all /health endpoints
```

### 3. Open dashboards

| Service | URL |
|---------|-----|
| **Grafana** | http://localhost:3001 *(admin / aether-guard)* |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| Alert Listener | http://localhost:8081/docs |
| AI Agent | http://localhost:8082/docs |
| Target Service | http://localhost:8080/api/users |

---

## Demo: End-to-End Chaos → RCA → Remediation

```bash
# Terminal 1 — watch the agent's decision stream
make agent-logs

# Terminal 2 — inject chaos and observe
make chaos-memleak      # trigger memory leak → MemorySaturationWarning fires
make chaos-error        # inject 500s → SLOErrorBudgetBurnCritical fires
make chaos-latency      # add 2s delay → SLOLatencyP99Breach fires

# After ~2 minutes, alert fires and flows through the full pipeline:
#   Prometheus → Alertmanager → Listener → Agent → Claude → Remediation → Post-mortem

make agent-analyses     # view AI RCA decisions
make postmortem-latest  # read generated blameless post-mortem
make chaos-reset        # restore healthy state
```

### Example AI Agent Output

```json
{
  "analysis": "Memory leak chaos endpoint was activated. Container RSS is growing linearly at ~52 MiB/s with no upper bound.",
  "root_cause": "Intentional chaos injection via /chaos/memleak endpoint. Underlying issue: unbounded slice growth retaining references preventing GC.",
  "confidence": 0.95,
  "action": "RESTART",
  "reasoning": "Container restart will free all retained heap. No user data at risk. Restart time < 5s given current health check config.",
  "slo_impact": "MemorySaturationWarning active. If OOM kill occurs before restart: ~30s downtime = 0.035% of monthly error budget consumed.",
  "recommended_followup": [
    "Add memory limits to container (e.g., mem_limit: 512m in docker-compose)",
    "Add OOM kill alerting as a separate SLO signal",
    "Review chaos endpoint access controls — should require auth token"
  ]
}
```

---

## CI/CD Pipeline

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push and PR:

```
go-build (build+vet+test) ─┐
python-lint ───────────────┼──► docker-build ──► integration-smoke
python-test ───────────────┤
validate-infra-config ─────┘
```

| Job | What it checks |
|-----|----------------|
| `go-build` | `go build` + `go vet` + `go test -race` (23 tests) |
| `python-lint` | `ruff` linting on agent, listener, scripts |
| `python-test` | `pytest` — 44 agent tests + 14 listener tests, JUnit XML artifacts |
| `validate-infra-config` | `promtool check config/rules` + `amtool check-config` |
| `docker-build` | Builds all 3 Docker images (only runs if all 4 above pass) |
| `integration-smoke` | Starts full stack, hits all health endpoints, queries Prometheus |

---

## Configuration

Copy `.env.example` to `.env` and fill in:

```bash
ANTHROPIC_API_KEY=sk-ant-...        # Required — Claude API key
CLAUDE_MODEL=claude-sonnet-4-5-20250929
CONFIDENCE_THRESHOLD=0.75           # Min confidence to execute an action
DRY_RUN=false                       # Set true to skip Docker remediation calls
POLL_INTERVAL=10                    # Agent polling interval (seconds)
```

### Remediation Safety Gates

The remediation engine has three independent safety mechanisms:

1. **Confidence threshold** — per-action minimums: RESTART≥0.75, SCALE≥0.70, ROLLBACK≥0.85
2. **Cooldown** — 5-minute per-container cooldown prevents remediation storms
3. **Dry-run mode** — `DRY_RUN=true` logs actions without executing any Docker calls

---

## Makefile Reference

```bash
make docker-up          # Start full stack
make docker-down        # Stop and remove containers
make docker-rebuild     # Force rebuild all images

make chaos-memleak      # Inject memory leak
make chaos-latency      # Inject 2s latency
make chaos-error        # Inject 50% 500 errors
make chaos-reset        # Reset all chaos

make load-gen           # Run traffic generator
make agent-analyses     # Print all AI analyses
make alert-status       # Show Prometheus alert states
make listener-pending   # Show unprocessed alerts in queue
make postmortem-latest  # Print latest post-mortem

make health-check       # Check all service health endpoints
make agent-logs         # Tail agent container logs
make listener-logs      # Tail listener container logs
```

---

## Makefile for Local Development

```bash
# Build the Go service locally
make build-local

# Run load + chaos without Docker
make load-gen
```

---

## Post-Mortem Generation

Post-mortems are generated automatically when an analysis completes, following the [Google SRE blameless post-mortem format](https://sre.google/sre-book/postmortem-culture/):

- **Impact** — affected users, error budget consumed, duration
- **Timeline** — detection, diagnosis, resolution with timestamps
- **Root cause** — technical explanation from AI analysis
- **Contributing factors** — systemic issues identified
- **Action items** — prioritized with owner and due date

```bash
# Generate post-mortem for a specific alert
curl -X POST http://localhost:8082/postmortem/{alert_id}

# Or via CLI for all historical analyses
python3 scripts/generate_postmortem.py --all
```

Post-mortems are written to `postmortems/YYYYMMDD-HHMMSS-{AlertName}.md`.

---

## Testing

**81 tests** across Go and Python, all running in CI.

```bash
# Go — 23 tests (chaos, handlers, metrics)
cd services/target-service && go test -race ./...

# Python agent — 44 tests (parse/validate, remediation safety gates)
python3 -m pytest services/agent/tests/ -v

# Python listener — 14 tests (webhook, enrichment, queue)
python3 -m pytest services/listener/tests/ --import-mode=importlib -v
```

---

## Production Deployment

### DigitalOcean / VPS Deployment (Docker Compose)

Automated CD pipeline via GitHub Actions for deploying to any VPS:

```bash
# 1. Run server setup script
curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/Aether-guard/main/scripts/setup-server.sh | sudo bash

# 2. Configure GitHub Secrets (see docs/CD-SETUP-GUIDE.md)
# 3. Trigger deployment via GitHub Actions UI
```

**Features:**
- Zero-downtime rolling updates
- Automatic health checks & rollback
- Manual approval required
- Docker image caching for fast builds
- Backup & restore capabilities

See [CD Setup Guide](docs/CD-SETUP-GUIDE.md) for detailed instructions.

---

## Kubernetes Deployment

Production-grade manifests in `k8s/` — deploy with a single command:

```bash
# minikube quick start
eval $(minikube docker-env)
docker build -t aether-guard/target-service:latest services/target-service
docker build -t aether-guard/listener:latest        services/listener
docker build -t aether-guard/agent:latest           services/agent

kubectl create secret generic agent-secrets \
  -n aether-guard --from-literal=ANTHROPIC_API_KEY=sk-ant-...

kubectl apply -k k8s/
```

Key production features: HPA (2→10 pods on CPU), zero-downtime rolling deploys (`maxUnavailable: 0`), `secretKeyRef` for API key, liveness/readiness probes on every service, PVCs for stateful data (Prometheus 5 Gi, agent 1 Gi).

See [`k8s/README.md`](k8s/README.md) for full instructions, NodePort mapping, and secret management options.

---

## Runbooks

Operational playbooks for all 5 alerts in [`docs/runbooks/`](docs/runbooks/):

| Alert | Runbook |
|-------|---------|
| `SLOErrorBudgetBurnCritical` / `Warning` | [high-error-rate.md](docs/runbooks/high-error-rate.md) |
| `SLOLatencyP99Breach` | [high-latency.md](docs/runbooks/high-latency.md) |
| `MemorySaturationWarning` | [memory-leak.md](docs/runbooks/memory-leak.md) |
| `TargetServiceDown` | [service-down.md](docs/runbooks/service-down.md) |

Each runbook: thresholds → mitigation commands → PromQL investigation → escalation policy → post-mortem trigger → toil-reduction recommendations.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Monitored service | Go 1.21, `prometheus/client_golang`, `uber/zap` |
| Metrics & alerting | Prometheus 2.48, Alertmanager 0.26, **Grafana 10.3** |
| Alert enrichment | Python 3.11, FastAPI, Docker SDK |
| AI RCA engine | Anthropic Claude (Sonnet), structured JSON output |
| Remediation | Docker SDK (`docker restart`, `docker update`) |
| Orchestration | Docker Compose + **Kubernetes** (Kustomize, HPA) |
| CI | GitHub Actions (6 jobs: build, lint, test, validate, docker, smoke) |

---

## License

MIT

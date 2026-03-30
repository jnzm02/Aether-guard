# ─────────────────────────────────────────────────────────────────────────────
# Aether-Guard Makefile
# Developer ergonomics for the Phase 1 target-service.
# ─────────────────────────────────────────────────────────────────────────────

SERVICE_DIR := services/target-service
INFRA_DIR   := infra

.PHONY: help build run test deps tidy docker-up docker-down chaos-memleak \
        chaos-latency chaos-error chaos-reset load-baseline load-all metrics

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Load .env for local (non-Docker) commands (e.g. make run, make load-all)
ifneq (,$(wildcard ./.env))
  include .env
  export
endif

# ── Go ────────────────────────────────────────────────────────────────────────

deps: ## Download Go module dependencies
	cd $(SERVICE_DIR) && go mod download

tidy: ## Tidy and vendor Go modules
	cd $(SERVICE_DIR) && go mod tidy

build: deps ## Build the target-service binary locally
	cd $(SERVICE_DIR) && \
	  CGO_ENABLED=0 go build -o /tmp/target-service ./cmd/server/
	@echo "✅  Binary: /tmp/target-service"

run: ## Run the target-service locally (no Docker)
	cd $(SERVICE_DIR) && go run ./cmd/server/

test: ## Run unit tests
	cd $(SERVICE_DIR) && go test ./... -v -race -count=1

# ── Docker Compose ────────────────────────────────────────────────────────────

docker-up: ## Build image and start all services (including Grafana)
	cd $(INFRA_DIR) && docker compose up --build -d
	@echo "✅  Services running"
	@echo "   target-service : http://localhost:8080"
	@echo "   prometheus     : http://localhost:9090"
	@echo "   alertmanager   : http://localhost:9093"
	@echo "   listener       : http://localhost:8081"
	@echo "   agent          : http://localhost:8082"
	@echo "   grafana        : http://localhost:3001  (admin / aether-guard)"

docker-down: ## Stop and remove all Phase 1 containers
	cd $(INFRA_DIR) && docker compose down -v

docker-logs: ## Tail logs from all containers
	cd $(INFRA_DIR) && docker compose logs -f

# ── Chaos Injection (requires running service) ────────────────────────────────

chaos-memleak: ## Inject a 100 MiB memory leak (2 × 50 MiB calls)
	@echo "🔴  Injecting memory leak..."
	curl -s "http://localhost:8080/chaos/memleak?mb=50" | python3 -m json.tool
	curl -s "http://localhost:8080/chaos/memleak?mb=50" | python3 -m json.tool

chaos-latency: ## Inject a 3-second latency spike
	@echo "🔴  Injecting 3 s latency spike..."
	curl -s "http://localhost:8080/chaos/latency?ms=3000" | python3 -m json.tool

chaos-error: ## Inject HTTP 500 errors at 100% rate (10 requests)
	@echo "🔴  Injecting 500 errors..."
	for i in $$(seq 1 10); do \
	  curl -s -o /dev/null -w "  HTTP %{http_code}\n" \
	    "http://localhost:8080/chaos/error?rate=1.0"; \
	done

chaos-reset: ## Reset all chaos state and free leaked memory
	@echo "♻️   Resetting chaos state..."
	curl -s "http://localhost:8080/chaos/reset" | python3 -m json.tool

# ── Load Generation ───────────────────────────────────────────────────────────

load-baseline: ## Run 30 s of healthy baseline traffic
	python3 scripts/load_gen.py --scenario baseline

load-all: ## Run the full kitchen-sink chaos scenario
	python3 scripts/load_gen.py --scenario all

# ── Metrics Inspection ────────────────────────────────────────────────────────

metrics: ## Print current Prometheus metrics from target-service
	@curl -s "http://localhost:8080/metrics" | grep "aether_guard"

health: ## Check /health endpoint
	@curl -s "http://localhost:8080/health" | python3 -m json.tool

health-check: ## Check health of all services
	@echo "target-service:"; curl -sf http://localhost:8080/health | python3 -m json.tool
	@echo "listener:";       curl -sf http://localhost:8081/health | python3 -m json.tool
	@echo "agent:";          curl -sf http://localhost:8082/health | python3 -m json.tool
	@echo "prometheus:";     curl -sf http://localhost:9090/-/healthy && echo OK
	@echo "alertmanager:";   curl -sf http://localhost:9093/-/healthy && echo OK
	@echo "grafana:";        curl -sf http://localhost:3001/api/health | python3 -m json.tool

grafana-open: ## Open Grafana dashboard in the browser
	open http://localhost:3001/d/aether-guard-slo

# ── Phase 2: Alerting & Listener ─────────────────────────────────────────────

alert-status: ## Show all currently firing Prometheus alerts
	@curl -s "http://localhost:9090/api/v1/alerts" | python3 -m json.tool

alert-rules: ## Show all loaded Prometheus alerting rules
	@curl -s "http://localhost:9090/api/v1/rules" | python3 -c \
	  "import sys,json; rules=json.load(sys.stdin); \
	   [print(r['name'], '-', r['state']) \
	    for g in rules['data']['groups'] for r in g['rules'] if r.get('type')=='alerting']"

listener-health: ## Check listener /health
	@curl -s "http://localhost:8081/health" | python3 -m json.tool

listener-alerts: ## List all enriched alerts in the listener queue
	@curl -s "http://localhost:8081/alerts" | python3 -m json.tool

listener-pending: ## List only unprocessed alerts (waiting for AI Agent)
	@curl -s "http://localhost:8081/alerts?unprocessed_only=true" | python3 -m json.tool

listener-snapshot: ## Show current Prometheus metric snapshot via listener
	@curl -s "http://localhost:8081/metrics-snapshot" | python3 -m json.tool

reload-prometheus: ## Hot-reload Prometheus config and rules (no restart)
	@curl -sX POST "http://localhost:9090/-/reload" && echo "Prometheus reloaded"

# ── Phase 3: AI SRE Agent ─────────────────────────────────────────────────────

agent-health: ## Check AI Agent health + stats
	@curl -s "http://localhost:8082/health" | python3 -m json.tool

agent-analyses: ## Show all RCA analyses produced by the agent
	@curl -s "http://localhost:8082/analyses" | python3 -m json.tool

agent-stats: ## Show agent poll/error counters
	@curl -s "http://localhost:8082/stats" | python3 -m json.tool

agent-logs: ## Tail live agent container logs
	cd $(INFRA_DIR) && docker compose logs -f agent

agent-trigger: ## Manually trigger analysis of the oldest pending alert
	@ALERT_ID=$$(curl -s "http://localhost:8081/alerts?unprocessed_only=true" \
	  | python3 -c "import sys,json; a=json.load(sys.stdin)['alerts']; print(a[0]['id'] if a else '')") && \
	  if [ -z "$$ALERT_ID" ]; then echo "No pending alerts."; \
	  else curl -s -X POST "http://localhost:8082/analyze/$$ALERT_ID" | python3 -m json.tool; fi

# ── Full E2E scenario (Phases 1-3) ────────────────────────────────────────────

demo-e2e: ## Full demo: inject chaos → wait for alert → show AI analysis
	@echo "Step 1: injecting error chaos..."
	@make chaos-error
	@echo "\nStep 2: waiting 90s for alert to fire and agent to analyze..."
	@sleep 90
	@echo "\nStep 3: AI Agent analyses:"
	@make agent-analyses

# ── Phase 4: Remediation & Post-Mortem ───────────────────────────────────────

agent-remediation: ## Show remediation results from last 5 analyses
	@curl -s "http://localhost:8082/analyses?limit=5" | \
	  python3 -c "import sys,json; \
	  data=json.load(sys.stdin); \
	  [print(f\"  {a['alertname']:<35} action={a['action']:<10} outcome={a.get('remediation',{}).get('outcome','N/A')}\") \
	   for a in data['analyses']]"

postmortem-latest: ## Generate post-mortem for the most recent alert
	@ALERT_ID=$$(curl -s http://localhost:8082/analyses | \
	  python3 -c "import sys,json; a=json.load(sys.stdin)['analyses']; print(a[-1]['alert_id'] if a else '')") && \
	  if [ -z "$$ALERT_ID" ]; then echo "No analyses yet — inject chaos first"; \
	  else curl -s -X POST "http://localhost:8082/postmortem/$$ALERT_ID" | \
	       python3 -c "import sys,json; d=json.load(sys.stdin); print(d['content'])"; fi

postmortem-file: ## Run standalone post-mortem generator against analyses.jsonl
	ANTHROPIC_API_KEY=$(ANTHROPIC_API_KEY) \
	  python3 scripts/generate_postmortem.py

postmortem-all: ## Generate post-mortems for ALL recorded analyses
	ANTHROPIC_API_KEY=$(ANTHROPIC_API_KEY) \
	  python3 scripts/generate_postmortem.py --all

demo-full: ## Kitchen-sink demo: all phases end-to-end (requires docker-up)
	@echo "════════════════════════════════════════════════"
	@echo "  AETHER-GUARD FULL DEMO (Phases 1-4)"
	@echo "════════════════════════════════════════════════"
	@echo "\n[Phase 1] Injecting memory leak (150 MiB)..."
	@make chaos-memleak
	@echo "\n[Phase 1] Injecting 100% error rate..."
	@make chaos-error
	@echo "\n[Phase 2] Watching for alerts (120s)..."
	@sleep 120
	@echo "\n[Phase 3] AI Agent analyses:"
	@make agent-analyses
	@echo "\n[Phase 4] Remediation results:"
	@make agent-remediation
	@echo "\n[Phase 4] Generating post-mortem..."
	@make postmortem-latest
	@echo "\n[Cleanup] Resetting chaos state..."
	@make chaos-reset
	@echo "\n✅  Demo complete. Post-mortems saved to ./postmortems/"

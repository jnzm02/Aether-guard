# Aether-Guard Runbooks

Operational playbooks for on-call engineers. Each runbook maps directly to a Prometheus alert and follows the Google SRE blameless post-mortem culture.

## Alert → Runbook Index

| Alert | Severity | SLO | Runbook |
|-------|----------|-----|---------|
| `SLOErrorBudgetBurnCritical` | 🔴 Critical | Availability | [high-error-rate.md](./high-error-rate.md) |
| `SLOErrorBudgetBurnWarning`  | 🟡 Warning  | Availability | [high-error-rate.md](./high-error-rate.md) |
| `SLOLatencyP99Breach`        | 🔴 Critical | Latency      | [high-latency.md](./high-latency.md) |
| `MemorySaturationWarning`    | 🟡 Warning  | Saturation   | [memory-leak.md](./memory-leak.md) |
| `TargetServiceDown`          | 🔴 Critical | Availability | [service-down.md](./service-down.md) |

## Runbook Structure

Every runbook contains:

1. **Thresholds** — exact conditions that fired the alert and what they mean
2. **Immediate Mitigation** — step-by-step commands to stop the bleeding (< 5 min)
3. **Root Cause Investigation** — PromQL queries, log grep patterns, classification tables
4. **Escalation Policy** — who to page and when
5. **Post-Mortem Trigger** — conditions requiring a blameless post-mortem
6. **Prevention / Toil Reduction** — long-term fixes to avoid repeat incidents

## Key Links

| Resource | URL |
|----------|-----|
| Grafana SLO Dashboard | http://localhost:3001/d/aether-guard-slo |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| Agent RCA History | http://localhost:8082/analyses |
| Agent Health | http://localhost:8082/health |
| Target Service Metrics | http://localhost:8080/metrics |

## Error Budget Reference

**SLO: 99.9% availability over 30 days**

| Burn Rate | Error Rate | Budget Exhausted In | Alert Fired |
|-----------|------------|---------------------|-------------|
| 1×  | 0.1% | 30 days | — |
| 5×  | 0.5% | 6 days  | `SLOErrorBudgetBurnWarning` |
| 50× | 5.0% | ~14 hrs | `SLOErrorBudgetBurnCritical` |
| 100% outage | 100% | 43 min  | `TargetServiceDown` |

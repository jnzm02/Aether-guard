# Runbook: High Error Rate / Error Budget Burn

| Field         | Value |
|---------------|-------|
| **Alerts**    | `SLOErrorBudgetBurnCritical` (severity: critical), `SLOErrorBudgetBurnWarning` (severity: warning) |
| **SLO**       | Availability — 99.9% of requests return non-5xx over a 30-day window |
| **SLI**       | `job:aether_guard_error_ratio:rate5m` and `job:aether_guard_error_ratio:rate30m` |
| **Team**      | Platform |
| **Escalation**| On-call → TL → Engineering Manager |

---

## 1. Thresholds

| Alert | Condition | Burn Rate | Budget Exhausted In |
|-------|-----------|-----------|---------------------|
| `SLOErrorBudgetBurnCritical` | error ratio > 5% for 2 min | 50× | ~14 hours — **PAGE NOW** |
| `SLOErrorBudgetBurnWarning`  | error ratio > 0.5% over 30 min for 5 min | 5× | ~6 days — investigate today |

---

## 2. Symptoms

- HTTP 5xx responses visible in service logs and Prometheus.
- Grafana → **Aether-Guard SLO** dashboard → "Error Rate" panel shows spike above red SLO line.
- Alertmanager fires webhook → Listener enqueues alert → Agent performs RCA.
- Agent may have already issued `RESTART` or `ROLLBACK` action (check agent logs).

---

## 3. Immediate Mitigation (< 5 minutes)

### Step 1 — Check agent decision

```bash
# Was an automated action already taken?
curl -s http://localhost:8082/analyses | jq '.[-1] | {action, confidence, reasoning}'
```

If the agent issued `RESTART` and the error rate dropped → **monitor for 10 minutes, close if stable**.

### Step 2 — Identify the failing handler

```promql
# Which endpoint is producing 5xx?
sum by (handler) (rate(aether_guard_http_requests_total{status_code=~"5.."}[5m]))
```

### Step 3 — Check if chaos injection is active

```bash
curl -s http://localhost:8080/chaos/status | jq .
```

If `"error_injection": true` → this is a **simulated failure**. Reset it:

```bash
curl -X POST http://localhost:8080/chaos/reset
```

### Step 4 — Manual restart if agent did not act

```bash
docker restart target-service
# Verify recovery
curl -s http://localhost:8080/health
```

---

## 4. Root Cause Investigation

### Check recent logs

```bash
docker logs target-service --tail 100 --timestamps
```

Look for: panic traces, dependency timeouts, OOM kills, port conflicts.

### Query error ratio history

```promql
# How long has the error rate been elevated?
job:aether_guard_error_ratio:rate5m[30m]
```

### Cross-reference memory leak

A memory leak can cause GC pressure → request processing failures.
Check `aether_guard_chaos_memleak_bytes_allocated` — if > 0, see [memory-leak runbook](./memory-leak.md).

---

## 5. Escalation

| Condition | Action |
|-----------|--------|
| Error rate > 5% AND agent `RESTART` did not recover | Page TL immediately |
| Error rate > 0.5% sustained > 30 min after restart | Open SEV-2, start post-mortem |
| Error budget < 10% remaining in current 30-day window | Freeze all non-critical deploys |

---

## 6. Post-Mortem Trigger

A blameless post-mortem is **required** if:
- `SLOErrorBudgetBurnCritical` fired for > 10 minutes, **or**
- Error budget consumption in a single incident > 20%.

The Aether-Guard agent generates a draft post-mortem automatically:

```bash
curl -s "http://localhost:8082/postmortem/{alert_id}" | jq -r .postmortem
```

---

## 7. Prevention / Toil Reduction

- Add canary deployment stage before rolling out new handlers.
- Set `ROLLBACK` as the default agent action when confidence > 0.85 and error rate > 5%.
- Consider adding a `rate()` circuit-breaker in the Go service that self-throttles at > 10% error rate.

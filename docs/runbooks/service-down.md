# Runbook: Service Down

| Field         | Value |
|---------------|-------|
| **Alert**     | `TargetServiceDown` (severity: critical) |
| **SLO**       | Availability — 99.9% of requests return non-5xx over a 30-day window |
| **SLI**       | `up{job="target-service"}` |
| **Team**      | Platform |
| **Escalation**| On-call → TL → Engineering Manager → VP Engineering |

---

## 1. Threshold

| Condition | Duration | Meaning |
|-----------|----------|---------|
| `up{job="target-service"} == 0` | 30 seconds | Prometheus cannot scrape the service — **100% of requests are failing** |

> **Note:** All other alerts (`SLOErrorBudgetBurnCritical`, `SLOLatencyP99Breach`, `MemorySaturationWarning`) are **inhibited** while `TargetServiceDown` is active. The outage is the root cause; downstream alerts are noise.

---

## 2. Symptoms

- Prometheus target page (`http://localhost:9090/targets`) shows `target-service` in **DOWN** state.
- All `aether_guard_*` metrics return no data.
- `/health` and `/ready` endpoints are unreachable.
- Error budget is being consumed at **maximum burn rate** — every second of downtime costs ~3.4 seconds of monthly budget.

---

## 3. Immediate Mitigation (< 2 minutes)

### Step 1 — Confirm the service is actually down

```bash
curl -sf http://localhost:8080/health || echo "SERVICE DOWN"
docker ps | grep target-service
```

### Step 2 — Check agent decision

```bash
curl -s http://localhost:8082/analyses | jq '.[-1] | {action, confidence, root_cause}'
```

The agent should have already issued `RESTART`. Verify:

```bash
docker logs agent --tail 20 | grep -i "restart\|action"
```

### Step 3 — Manual restart (if agent did not act or restart failed)

```bash
docker start target-service
# Wait up to 30s for health check
for i in $(seq 1 15); do
  curl -sf http://localhost:8080/health && echo "RECOVERED" && break
  sleep 2
done
```

### Step 4 — If container does not start

```bash
# Check exit code and reason
docker inspect target-service --format '{{.State.ExitCode}} {{.State.Error}}'

# Check OOM kill
docker inspect target-service --format '{{.State.OOMKilled}}'
```

If `OOMKilled: true` → see [memory-leak runbook](./memory-leak.md).

---

## 4. Root Cause Investigation

### Exit code reference

| Exit Code | Likely Cause |
|-----------|-------------|
| 0 | Clean shutdown (unexpected in production) |
| 1 | Application panic / unhandled error |
| 137 | OOM kill (`SIGKILL` from kernel) |
| 139 | Segmentation fault |
| 143 | Graceful shutdown (`SIGTERM`) |

### Check logs from the crash

```bash
# Last 200 lines before the crash
docker logs target-service --tail 200 --timestamps 2>&1 | grep -E "panic|fatal|error|FATAL"
```

### Check if port is already bound

```bash
lsof -i :8080
```

Port conflict is the most common cause of container start failure after a restart.

### Check Docker daemon health

```bash
docker info --format '{{.ServerErrors}}'
```

### Check available disk space

```bash
df -h /var/lib/docker
```

Docker containers fail to start silently when the overlay filesystem has no space.

---

## 5. Full Stack Restart

If a single container restart does not resolve the issue:

```bash
cd infra
docker compose down
docker compose up -d
# Monitor recovery
docker compose ps
curl -sf http://localhost:8080/health
curl -sf http://localhost:8081/health
curl -sf http://localhost:8082/health
```

---

## 6. Escalation

| Condition | Action |
|-----------|--------|
| Service does not recover within 5 min of restart | Page TL immediately |
| Two crashes within 1 hour | SEV-1, assign engineer, freeze deploys |
| OOM kill confirmed | See memory-leak runbook; do not restart without increasing memory limit |
| Data loss suspected | Escalate to VP Engineering, initiate incident war room |

**Error budget impact of a full outage:**

| Downtime | % of 30-day Budget Consumed |
|----------|-----------------------------|
| 1 minute  | 0.23% |
| 5 minutes | 1.16% |
| 30 minutes | 6.94% |
| 43 minutes | 100% — **budget exhausted** |

---

## 7. Post-Mortem Trigger

A blameless post-mortem is **required** for every `TargetServiceDown` incident.

```bash
curl -s "http://localhost:8082/postmortem/{alert_id}" | jq -r .postmortem
```

Post-mortem must be completed within **48 hours** of incident resolution and reviewed by the full team.

---

## 8. Prevention / Toil Reduction

- Add Docker `restart: unless-stopped` policy to the target-service compose definition (already present — verify on each deploy).
- Implement a `/ready` liveness probe with a strict 5-second deadline in the container health check.
- Add a Prometheus dead man's switch alert: if no scrape data for > 60 s, fire a `WatchdogHeartbeatMissing` alert to a separate receiver.
- Automate `RESTART` as the agent's default action for `TargetServiceDown` with confidence bypassed (outage = certainty, not probability).

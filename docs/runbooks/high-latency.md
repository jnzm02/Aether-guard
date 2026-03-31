# Runbook: High Latency / p99 SLO Breach

| Field         | Value |
|---------------|-------|
| **Alert**     | `SLOLatencyP99Breach` (severity: critical) |
| **SLO**       | Latency — p99 request duration < 200 ms |
| **SLI**       | `job:aether_guard_latency_p99:rate5m` |
| **Team**      | Platform |
| **Escalation**| On-call → TL → Engineering Manager |

---

## 1. Threshold

| Condition | Duration | Meaning |
|-----------|----------|---------|
| p99 latency > 200 ms | 2 minutes | 1 in 100 users is experiencing unacceptable response time — **page now** |

---

## 2. Symptoms

- Grafana → **Aether-Guard SLO** dashboard → "p50/p99/p999 Latency" panel shows p99 line crossing the 200 ms SLO marker.
- Client-facing requests timing out or returning slowly.
- Agent RCA output likely contains `action: RESTART` or `action: SCALE`.

---

## 3. Immediate Mitigation (< 5 minutes)

### Step 1 — Check agent decision

```bash
curl -s http://localhost:8082/analyses | jq '.[-1] | {action, confidence, root_cause}'
```

### Step 2 — Check if latency chaos is active

```bash
curl -s http://localhost:8080/chaos/status | jq .latency_injection
```

If `true` → intentional latency spike is running. Reset:

```bash
curl -X POST http://localhost:8080/chaos/reset
```

### Step 3 — Identify the slow handler

```promql
# p99 latency per handler
histogram_quantile(
  0.99,
  sum by (handler, le) (
    rate(aether_guard_http_request_duration_seconds_bucket[5m])
  )
)
```

### Step 4 — Check for resource saturation

High latency often means CPU or memory pressure.

```bash
docker stats target-service --no-stream
```

If memory is near the container limit → see [memory-leak runbook](./memory-leak.md).

### Step 5 — Manual restart if agent did not act

```bash
docker restart target-service
# Confirm recovery — p99 should drop within 30s
curl -s "http://localhost:9090/api/v1/query?query=job%3Aaether_guard_latency_p99%3Arate5m" | jq '.data.result[0].value[1]'
```

---

## 4. Root Cause Investigation

### Check for context timeouts in logs

```bash
docker logs target-service --tail 100 | grep -i "context\|timeout\|deadline\|slow"
```

### Common causes ranked by frequency

| Cause | Signal | Fix |
|-------|--------|-----|
| Chaos latency injection | `/chaos/status` shows `latency_injection: true` | `POST /chaos/reset` |
| Downstream dependency timeout | Logs show connection refused / timeout errors | Restart dependency or increase timeout |
| Memory pressure / GC pause | RSS > 80% of container limit | Restart; investigate leak |
| CPU throttling | `docker stats` shows > 90% CPU | Scale horizontally |
| Cold start after restart | Spike immediately after deploy | Wait 60s, monitor |

### Latency distribution deep dive

```promql
# Are p50 users also affected, or only the tail?
job:aether_guard_latency_p50:rate5m
job:aether_guard_latency_p99:rate5m
```

If p50 is normal but p99 is elevated → tail latency issue (GC pauses, slow DB queries, queue head-of-line blocking).

If both elevated → systemic saturation.

---

## 5. Escalation

| Condition | Action |
|-----------|--------|
| p99 > 200 ms AND agent restart did not recover | Page TL |
| p99 > 1 s (5× SLO) sustained > 5 min | SEV-1, all hands |
| p50 > 200 ms | SEV-1 immediately — majority of users affected |

---

## 6. Post-Mortem Trigger

A blameless post-mortem is **required** if:
- `SLOLatencyP99Breach` was active for > 10 minutes, **or**
- p99 exceeded 1 s at any point.

```bash
curl -s "http://localhost:8082/postmortem/{alert_id}" | jq -r .postmortem
```

---

## 7. Prevention / Toil Reduction

- Add p99 latency SLO check to pre-deploy integration tests (reject deploys that push p99 > 150 ms in staging).
- Set per-request context deadline in the Go service to 500 ms to prevent runaway requests.
- Enable horizontal pod autoscaling (HPA) triggered at p99 > 180 ms — see [kubernetes manifests](../../k8s/).

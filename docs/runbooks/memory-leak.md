# Runbook: Memory Leak / Saturation

| Field         | Value |
|---------------|-------|
| **Alert**     | `MemorySaturationWarning` (severity: warning) |
| **SLO**       | Saturation — container RSS must not exceed memory limit |
| **SLI**       | `aether_guard_chaos_memleak_bytes_allocated` |
| **Team**      | Platform |
| **Escalation**| On-call → TL |

---

## 1. Threshold

| Condition | Duration | Meaning |
|-----------|----------|---------|
| Leaked bytes > 100 MiB | 1 minute | OOM kill is likely imminent without intervention |

---

## 2. Symptoms

- Grafana → **Aether-Guard SLO** dashboard → "Memory Saturation" panel rising past the 100 MiB red line.
- `aether_guard_chaos_memleak_bytes_allocated` metric is non-zero and growing.
- Latency may be rising simultaneously (GC pressure) — check `SLOLatencyP99Breach`.
- In production: container RSS visible via `docker stats`, approaching container memory limit.

---

## 3. Immediate Mitigation (< 3 minutes)

### Step 1 — Check current leak size

```bash
curl -s "http://localhost:9090/api/v1/query?query=aether_guard_chaos_memleak_bytes_allocated" \
  | jq '.data.result[0].value[1]'
```

### Step 2 — Check agent decision

```bash
curl -s http://localhost:8082/analyses | jq '.[-1] | {action, confidence, root_cause}'
```

The agent may have already issued `RESTART`. If so, verify the metric returns to 0:

```promql
aether_guard_chaos_memleak_bytes_allocated
```

### Step 3 — Check if chaos injection is the cause

```bash
curl -s http://localhost:8080/chaos/status | jq .memory_leak
```

If `true` → intentional chaos leak. Reset it:

```bash
curl -X POST http://localhost:8080/chaos/reset
# Verify memory returns to 0
curl -s http://localhost:8080/metrics | grep memleak
```

### Step 4 — Manual restart if memory is not releasing

```bash
docker restart target-service
# Memory gauge resets to 0 on restart since it is in-process state
docker stats target-service --no-stream
```

---

## 4. Root Cause Investigation

### Check leak rate

```promql
# How fast is memory growing? (bytes per second)
rate(aether_guard_chaos_memleak_bytes_allocated[5m])
```

A rate > 1 MiB/s will exhaust a 256 MiB container limit in ~4 minutes.

### Check for correlated latency spike

```promql
job:aether_guard_latency_p99:rate5m
```

If both memory and latency are elevated → GC is stalling request processing. Restart is urgent.

### Check container-level memory

```bash
docker stats target-service --no-stream --format "{{.MemUsage}}"
```

If container RSS is near the `mem_limit` in `docker-compose.yml`, an OOM kill may happen before the alert resolves.

### Review chaos endpoint access logs

```bash
docker logs target-service --tail 200 | grep "/chaos/memleak"
```

Who triggered the leak? If it was not the chaos test harness, investigate whether a real code path is retaining unbounded references.

---

## 5. Memory Leak Classification

| Type | Signal | Action |
|------|--------|--------|
| Intentional chaos injection | `/chaos/status` memory_leak=true | `POST /chaos/reset` |
| Go goroutine leak | Goroutine count growing (`runtime.NumGoroutine`) | Restart, file bug |
| Unbounded in-memory cache | RSS grows proportional to request count | Eviction policy fix + restart |
| Dependency library leak | Heap grows with no obvious Go metrics cause | Restart, upgrade dep, add heap profile |

---

## 6. Escalation

| Condition | Action |
|-----------|--------|
| RSS > 80% of container limit | Page on-call — OOM kill is imminent |
| Leak persists after `chaos/reset` AND restart | This is a real application bug — SEV-2, assign to owner |
| Second leak event within 24 hours | Freeze deploys, mandatory RCA before resuming |

---

## 7. Post-Mortem Trigger

A blameless post-mortem is **required** if:
- Container was OOM-killed, **or**
- Leak was not caused by chaos injection (i.e. a real regression).

```bash
curl -s "http://localhost:8082/postmortem/{alert_id}" | jq -r .postmortem
```

---

## 8. Prevention / Toil Reduction

- Add a container `mem_limit` in `docker-compose.yml` (e.g. `256m`) so OOM kills are fast and predictable rather than system-wide thrash.
- Export Go runtime memory metrics (`runtime.MemStats`) to Prometheus for finer-grained heap analysis.
- Add a `/debug/pprof/heap` endpoint (Go `net/http/pprof`) so on-call engineers can capture a heap profile without restarting.
- Alert at 50 MiB (earlier warning) in addition to the existing 100 MiB page threshold.

# Bringing Your Own Service to Aether-Guard

Aether-Guard is designed to monitor **any Prometheus-instrumented service**, not just the bundled `target-service` demo.

This guide explains:
1. The metric contract your service must implement
2. How to configure Aether-Guard to monitor your service
3. Example instrumentation code
4. Troubleshooting tips

---

## Overview

Aether-Guard's AI agent analyzes SLO breaches and triggers automated remediation. To work with your service, you need to:

1. **Instrument your service** with Prometheus metrics (the "metric contract")
2. **Configure Prometheus** to scrape your service with a unique `job` label
3. **Set environment variables** to tell Aether-Guard which service to monitor

---

## 1. Metric Contract

Your service must expose these Prometheus metrics at `/metrics`:

### Required Metrics

| Metric Name | Type | Description | Labels |
|-------------|------|-------------|--------|
| `aether_guard_http_requests_total` | Counter | Total HTTP requests | `status_code` (e.g., "200", "500") |
| `aether_guard_http_request_duration_seconds` | Histogram | Request latency distribution | `le` (histogram bucket) |

### Optional Metrics (for enhanced diagnostics)

The agent detects resource-leak, memory-trend, and CPU-trend problems from
runtime metrics. Which metric **names** it queries depends on your service's
runtime, selected by the agent's `MONITORED_RUNTIME` env var (`go` — default —
or `jvm`). Expose the row matching your runtime:

| Logical signal | `MONITORED_RUNTIME=go` | `MONITORED_RUNTIME=jvm` |
|---|---|---|
| Concurrency / leak | `go_goroutines` | `jvm_threads_live_threads` |
| Heap in use | `go_memstats_heap_inuse_bytes` | `jvm_memory_used_bytes{area="heap"}` |
| Heap allocated (trend) | `go_memstats_heap_alloc_bytes` | `jvm_memory_used_bytes{area="heap"}` |
| CPU time | `process_cpu_seconds_total` | `process_cpu_seconds_total` *(same)* |
| GC pressure *(jvm only)* | — | `jvm_gc_pause_seconds_{sum,count}` |

**Go** services get the `go_*` metrics free from the Prometheus Go client.
**JVM** services get `jvm_*` and `process_cpu_seconds_total` free from
[Micrometer](https://micrometer.io) (`micrometer-registry-prometheus` +
Actuator, with the default JVM binders enabled). Set the agent's
`MONITORED_RUNTIME=jvm` and the goroutine/heap/CPU/GC patterns work unchanged —
no agent code changes. See `services/target-service-java/` for a reference JVM
implementation, and `services/agent/metric_profiles.py` for the full mapping.

**Other languages:** with only the two required HTTP metrics, the agent still
does error-rate, latency, traffic-spike, and log-based (OOM / dependency /
bad-deploy / disk) RCA; the runtime-metric patterns above are simply skipped
until a profile exists for that runtime.

---

## 2. Example Instrumentation

### Go (using `prometheus/client_golang`)

```go
package main

import (
    "net/http"
    "github.com/prometheus/client_golang/prometheus"
    "github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
    httpRequestsTotal = prometheus.NewCounterVec(
        prometheus.CounterOpts{
            Name: "aether_guard_http_requests_total",
            Help: "Total number of HTTP requests",
        },
        []string{"status_code"},
    )

    httpRequestDuration = prometheus.NewHistogramVec(
        prometheus.HistogramOpts{
            Name:    "aether_guard_http_request_duration_seconds",
            Help:    "HTTP request latency in seconds",
            Buckets: prometheus.DefBuckets, // [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]
        },
        []string{},
    )
)

func init() {
    prometheus.MustRegister(httpRequestsTotal)
    prometheus.MustRegister(httpRequestDuration)
}

func recordMetrics(statusCode string, duration float64) {
    httpRequestsTotal.WithLabelValues(statusCode).Inc()
    httpRequestDuration.WithLabelValues().Observe(duration)
}

func main() {
    // Your application handlers
    http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
        start := time.Now()
        // ... handle request ...
        statusCode := "200"  // or "500" if error
        recordMetrics(statusCode, time.Since(start).Seconds())
    })

    // Prometheus metrics endpoint
    http.Handle("/metrics", promhttp.Handler())

    http.ListenAndServe(":8080", nil)
}
```

### Python (using `prometheus_client`)

```python
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
from flask import Flask, Response
import time

app = Flask(__name__)

# Metrics
http_requests_total = Counter(
    'aether_guard_http_requests_total',
    'Total HTTP requests',
    ['status_code']
)

http_request_duration = Histogram(
    'aether_guard_http_request_duration_seconds',
    'HTTP request latency'
)

@app.route('/')
def index():
    start = time.time()
    try:
        # ... handle request ...
        http_requests_total.labels(status_code='200').inc()
        return "OK"
    except Exception:
        http_requests_total.labels(status_code='500').inc()
        raise
    finally:
        http_request_duration.observe(time.time() - start)

@app.route('/metrics')
def metrics():
    return Response(generate_latest(REGISTRY), mimetype='text/plain')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
```

---

## 3. Prometheus Configuration

Add your service to Prometheus `scrape_configs` with a unique `job_name`:

```yaml
# infra/prometheus/prometheus.yml

scrape_configs:
  # Your service (replace "my-api-service" with your service name)
  - job_name: "my-api-service"
    scrape_interval: 15s
    static_configs:
      - targets: ["my-api-service:8080"]  # Adjust host:port for your service

  # Optional: Keep target-service for testing
  - job_name: "target-service"
    scrape_interval: 15s
    static_configs:
      - targets: ["target-service:8080"]
```

**Important:** The `job_name` value becomes the `job` label in Prometheus. You'll use this in the next step.

---

## 4. Configure Aether-Guard Environment Variables

Set these environment variables in your `.env` file (copy from `.env.example`):

```bash
# ─────────────────────────────────────────────────────────────────────────────
# Service Configuration
# ─────────────────────────────────────────────────────────────────────────────

# MONITORED_JOB: Must match the "job_name" in your prometheus.yml scrape_configs
MONITORED_JOB=my-api-service

# TARGET_CONTAINER: Docker container name or Kubernetes pod name pattern
# Used for fetching logs and remediation actions (restart, scale, rollback)
TARGET_CONTAINER=my-api-service

# MONITORED_RUNTIME: Which runtime metric profile to use — "go" (default) or "jvm".
# Selects the metric names the agent queries for the runtime-metric RCA patterns
# (see the Optional Metrics mapping above). Leave as "go" for Go services.
MONITORED_RUNTIME=jvm
```

### What These Variables Do

| Variable | Used By | Purpose |
|----------|---------|---------|
| `MONITORED_JOB` | Prometheus SLO alerts, AI agent rules | Filters metrics by `job` label in PromQL queries |
| `TARGET_CONTAINER` | AI agent enrichment, remediation | Identifies Docker container for log fetching and restart/scale actions |
| `MONITORED_RUNTIME` | AI agent enrichment + rules | Picks the `go`/`jvm` metric profile (`services/agent/metric_profiles.py`) so runtime-metric patterns query the right metric names |

---

## 5. How It Works: Template Rendering

Aether-Guard uses **envsubst** to render Prometheus alert rules at container startup:

1. **Template file**: `infra/prometheus/rules/slo_alerts.yml.template` contains `${MONITORED_JOB}` placeholders
2. **Docker entrypoint**: Runs `envsubst '${MONITORED_JOB}' < template > slo_alerts.yml` before Prometheus starts
3. **Result**: Alert rules reference your service by name

**Example transformation:**

```yaml
# Before (template)
- alert: TargetServiceDown
  expr: up{job="${MONITORED_JOB}"} == 0
  labels:
    service: ${MONITORED_JOB}
  annotations:
    summary: "CRITICAL: ${MONITORED_JOB} is DOWN"

# After (MONITORED_JOB=my-api-service)
- alert: TargetServiceDown
  expr: up{job="my-api-service"} == 0
  labels:
    service: my-api-service
  annotations:
    summary: "CRITICAL: my-api-service is DOWN"
```

---

## 6. Quick Start: Replace target-service

### Step 1: Update Docker Compose

Modify `infra/docker-compose.yml`:

```yaml
services:
  # Replace target-service with your service
  my-api-service:
    build:
      context: ../services/my-api-service
      dockerfile: Dockerfile
    container_name: my-api-service
    ports:
      - "8080:8080"
    networks:
      - aether-net

  # Update Prometheus environment
  prometheus:
    environment:
      - MONITORED_JOB=my-api-service  # <-- Change this
    # ... rest of prometheus config ...

  # Update listener environment
  listener:
    environment:
      - TARGET_CONTAINER=my-api-service  # <-- Change this
    # ... rest of listener config ...

  # Update agent .env (or set here)
  agent:
    env_file:
      - ../.env  # Should contain MONITORED_JOB=my-api-service
```

### Step 2: Update .env

```bash
# .env
MONITORED_JOB=my-api-service
TARGET_CONTAINER=my-api-service
ANTHROPIC_API_KEY=sk-ant-...
```

### Step 3: Start the Stack

```bash
docker compose -f infra/docker-compose.yml up --build
```

### Step 4: Verify Alert Rules

Check that Prometheus loaded the rendered rules:

```bash
# View rendered alert rules
docker exec prometheus cat /etc/prometheus/rules/slo_alerts.yml | grep "my-api-service"

# Check Prometheus UI
open http://localhost:9090/alerts
```

---

## 7. Troubleshooting

### Problem: Prometheus alerts show "target-service" instead of my service

**Cause:** `MONITORED_JOB` environment variable not set or not passed to Prometheus container.

**Fix:**
```bash
# Check Prometheus container environment
docker exec prometheus printenv MONITORED_JOB

# Should output: my-api-service
# If empty, update docker-compose.yml prometheus.environment section
```

### Problem: Alert rules file validation fails

**Cause:** Template rendering failed or `envsubst` not available in Prometheus image.

**Fix:**
```bash
# Check entrypoint logs
docker logs prometheus 2>&1 | grep entrypoint

# Manually test rendering (on host)
MONITORED_JOB=my-api-service envsubst '${MONITORED_JOB}' \
  < infra/prometheus/rules/slo_alerts.yml.template \
  | promtool check rules /dev/stdin
```

### Problem: AI agent still queries "target-service" metrics

**Cause:** Agent service not reading `MONITORED_JOB` from `.env` file.

**Fix:**
```bash
# Check agent environment
docker exec agent python3 -c "import os; print(os.getenv('MONITORED_JOB'))"

# Should output: my-api-service
# If not, ensure .env is mounted and contains MONITORED_JOB
```

### Problem: No alerts firing even when service is broken

**Cause:** Metric names don't match the contract, or `job` label is wrong.

**Fix:**
```bash
# Query Prometheus to check metrics exist
curl -s 'http://localhost:9090/api/v1/query?query=aether_guard_http_requests_total' | jq .

# Check job label
curl -s 'http://localhost:9090/api/v1/query?query=up{job="my-api-service"}' | jq .

# Should return: "value": [timestamp, "1"]
# If "0" or empty, Prometheus can't scrape your service
```

---

## 8. Metric Contract Validation

Use this checklist to verify your service is ready:

- [ ] Service exposes `/metrics` endpoint (Prometheus format)
- [ ] `aether_guard_http_requests_total` counter increments on every request
- [ ] `status_code` label is set correctly ("200", "500", etc.)
- [ ] `aether_guard_http_request_duration_seconds` histogram records latencies
- [ ] Prometheus scrapes succeed: `up{job="my-service"} == 1`
- [ ] Error rate query works: `rate(aether_guard_http_requests_total{status_code=~"5.."}[5m])`
- [ ] Latency query works: `histogram_quantile(0.99, rate(aether_guard_http_request_duration_seconds_bucket[5m]))`

**Test command:**

```bash
# Replace "my-api-service" with your MONITORED_JOB value
curl -s http://localhost:9090/api/v1/query \
  --data-urlencode 'query=rate(aether_guard_http_requests_total{job="my-api-service"}[5m])' \
  | jq '.data.result'

# Should return non-empty array if metrics are flowing
```

---

## 9. Advanced: Kubernetes Deployment

For Kubernetes, set environment variables in the agent Deployment manifest:

```yaml
# k8s/agent-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent
spec:
  template:
    spec:
      containers:
      - name: agent
        env:
        - name: MONITORED_JOB
          value: "my-api-service"  # Match your Prometheus job_name
        - name: TARGET_CONTAINER
          value: "my-api-.*"       # Regex pattern for pod names
        envFrom:
        - secretRef:
            name: aether-guard-secrets  # Contains ANTHROPIC_API_KEY
```

For Prometheus, use an init container to render the template:

```yaml
# k8s/prometheus-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
spec:
  template:
    spec:
      initContainers:
      - name: render-rules
        image: busybox:1.36
        command:
        - sh
        - -c
        - |
          apk add --no-cache gettext
          envsubst '${MONITORED_JOB}' < /templates/slo_alerts.yml.template > /rules/slo_alerts.yml
        env:
        - name: MONITORED_JOB
          value: "my-api-service"
        volumeMounts:
        - name: rule-templates
          mountPath: /templates
        - name: rules
          mountPath: /rules
      containers:
      - name: prometheus
        volumeMounts:
        - name: rules
          mountPath: /etc/prometheus/rules
      volumes:
      - name: rule-templates
        configMap:
          name: prometheus-rule-templates
      - name: rules
        emptyDir: {}
```

---

## 10. Summary

| Step | Action | File/Command |
|------|--------|--------------|
| 1. Instrument | Add Prometheus metrics to your service | See examples above |
| 2. Configure Prometheus | Add `job_name` to `scrape_configs` | `infra/prometheus/prometheus.yml` |
| 3. Set env vars | `MONITORED_JOB` and `TARGET_CONTAINER` | `.env` |
| 4. Verify | Check rendered rules | `docker exec prometheus cat /etc/prometheus/rules/slo_alerts.yml` |
| 5. Test | Trigger an alert | Cause errors or latency spike in your service |

**Need help?** Open an issue at https://github.com/aether-guard/aether-guard/issues

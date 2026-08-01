# Target Service

**The intentionally "broken" microservice for Aether-Guard incident response testing.**

## What is this?

`target-service` is a **crash test dummy for SRE automation**. It's a Go-based HTTP service that simulates a real e-commerce backend, but with one critical difference: it has built-in endpoints to intentionally break itself in realistic ways.

Think of it as a controlled training environment where the Aether-Guard AI agent can practice incident detection, root cause analysis, and automated remediation without risking real production systems.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    TARGET-SERVICE (:8080)                   │
│                                                             │
│  ┌──────────────────────┐      ┌──────────────────────┐    │
│  │  Business API        │      │  Chaos Engineering   │    │
│  │  (Normal Traffic)    │      │  (Failure Injection) │    │
│  │                      │      │                      │    │
│  │  GET /api/users      │      │  POST /chaos/memleak │    │
│  │  GET /api/orders     │      │  POST /chaos/cpu     │    │
│  │                      │      │  POST /chaos/latency │    │
│  │  Returns: JSON       │      │  POST /chaos/error   │    │
│  │  Data: SQLite        │      │  POST /chaos/...     │    │
│  └──────────────────────┘      └──────────────────────┘    │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Observability Outputs                   │  │
│  │                                                      │  │
│  │  GET /metrics    - Prometheus metrics (RED + more)  │  │
│  │  GET /health     - Health check + dependencies      │  │
│  │  GET /ready      - Readiness probe                  │  │
│  │  Logs: JSON      - Structured logging (zap)         │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  Optional Dependencies:                                     │
│  • Postgres (POSTGRES_URL) - for health check testing      │
│  • Redis (REDIS_ADDR) - for cache layer + health checks    │
└─────────────────────────────────────────────────────────────┘
```

## What does it do?

### 1. Simulates a Real E-Commerce Backend

The service provides two business endpoints that mimic a real microservice:

**GET /api/users**
```bash
curl http://localhost:8080/api/users
```
Returns:
```json
{
  "users": [
    {"id": 1, "name": "Alice Johnson", "email": "alice@example.com"},
    {"id": 2, "name": "Bob Smith", "email": "bob@example.com"}
  ],
  "count": 2
}
```

**GET /api/orders**
```bash
curl http://localhost:8080/api/orders
```
Returns:
```json
{
  "orders": [
    {
      "id": 1,
      "user_id": 1,
      "user_name": "Alice Johnson",
      "product": "Laptop",
      "total": 1299.99,
      "status": "delivered"
    }
  ],
  "count": 21
}
```

**Why?** These endpoints generate baseline traffic with realistic:
- Database queries (SQLite with JOINs)
- Response times (actual I/O latency, not synthetic sleeps)
- Business metrics (orders by status, payment processing, inventory checks)

This establishes **"what healthy looks like"** before chaos is injected.

### 2. Provides Chaos Engineering Endpoints

The service can intentionally break itself in 9 different ways, corresponding to the 9 RCA patterns the AI agent needs to handle:

| Endpoint | What it does | RCA Pattern Tested |
|----------|--------------|-------------------|
| `POST /chaos/memleak?mb=500` | Allocates 500MB RAM and never frees it | Pattern 1: OOM Kill |
| `POST /chaos/cpu?seconds=30` | Spins CPU-burning goroutines for 30s | Pattern 7: High CPU |
| `POST /chaos/latency?ms=500` | Adds 500ms delay to all requests | Pattern 4: Slow Responses |
| `POST /chaos/error?rate=50` | Returns HTTP 500 on 50% of requests | Pattern 3: Error Spike |
| `POST /chaos/goroutine-leak?count=1000` | Leaks 1000 goroutines that never exit | Pattern 8: Goroutine Leak |

**Control endpoints:**
- `GET /chaos/status` - Shows current chaos state
- `POST /chaos/reset` - Clears all active chaos (except leaked memory/goroutines)

**Example:**
```bash
# Trigger a memory leak
curl -X POST "http://localhost:8080/chaos/memleak?mb=500"

# Check status
curl http://localhost:8080/chaos/status
# Returns: {"active_chaos":["memleak"],"leaked_bytes":524288000,...}

# Reset chaos
curl -X POST http://localhost:8080/chaos/reset
```

### 3. Exposes Comprehensive Observability

**Prometheus Metrics** (`GET /metrics`):

The service emits **15 metric families** across 4 categories:

**RED Metrics** (Requests, Errors, Duration):
```
aether_guard_http_requests_total{method="GET",path="/api/users",status_code="200"} 42
aether_guard_http_request_duration_seconds{method="GET",path="/api/users"}
aether_guard_http_requests_in_flight{endpoint="/api/orders"} 3
```

**Resource Metrics**:
```
aether_guard_db_connections_active 2
aether_guard_db_connections_idle 8
aether_guard_db_connections_max 10
aether_guard_process_open_fds 47
aether_guard_runtime_goroutines 23
go_memstats_alloc_bytes 8388608
```

**Business Metrics**:
```
aether_guard_business_orders_total{status="delivered"} 156
aether_guard_business_orders_total{status="pending"} 23
aether_guard_business_payment_processing_duration_seconds_sum 45.2
aether_guard_business_inventory_checks_total{result="success"} 892
aether_guard_business_background_jobs_queue_length{queue_name="order_processing"} 5
```

**Error Tracking**:
```
aether_guard_errors_total{type="server_error",endpoint="/api/orders"} 15
aether_guard_circuit_breaker_state{service="postgres"} 0  # 0=closed, 1=half_open, 2=open
aether_guard_timeout_errors_total{upstream="redis"} 3
```

**Structured Logs** (JSON via zap):
```json
{"level":"info","ts":1735689600.123,"caller":"server/main.go:146","msg":"🚀 aether-guard/target-service starting","addr":":8080","version":"1.2.0"}
{"level":"error","ts":1735689605.456,"caller":"infrastructure/postgres.go:38","msg":"postgres ping failed","error":"dial tcp 127.0.0.1:5432: connect: connection refused"}
```

Logs contain specific keywords the AI agent searches for during RCA:
- `"starting"` → Restart loop detection
- `"connection refused"` → Dependency failure detection
- `"out of memory"` → OOM pattern matching

## How it's used in Aether-Guard

### Full Incident Response Flow:

```
1. SERVICE RUNNING NORMALLY
   ├─ /api/users serves requests
   ├─ /api/orders processes orders
   └─ Prometheus scrapes /metrics every 5s
      → All SLIs look healthy

2. CHAOS INJECTION (manual or automated)
   └─ curl -X POST "http://localhost:8080/chaos/memleak?mb=500"
      → Service starts leaking memory rapidly
      → go_memstats_alloc_bytes metric climbs

3. PROMETHEUS ALERT FIRES
   └─ memory_usage > 80% for 2+ minutes
      → Alert sent to Alertmanager
      → Alertmanager routes to Listener webhook

4. LISTENER ENRICHES ALERT
   ├─ Fetches metrics from Prometheus (current + historical)
   ├─ Fetches container logs from Docker
   └─ Queues enriched alert for AI agent

5. AI AGENT INVESTIGATES
   ├─ Rules Engine (60% of incidents, <50ms, free):
   │  └─ Pattern matching on metrics/logs
   │     → "OOM pattern detected with 0.95 confidence"
   │
   └─ LLM Analysis (40% of incidents, 2-5s, $0.01):
      └─ RAG similarity search + Claude reasoning
         → "Memory leak likely in /api/orders handler"

6. POLICY CHECK
   └─ Action: "restart_container"
      ├─ Risk: LOW (safe action)
      ├─ Blast radius: 1 pod
      └─ Approval: Auto-approved (low risk)

7. EXECUTION
   ├─ Capture "before" metrics (error_rate, p99_latency)
   ├─ Execute: docker restart target-service
   └─ Start 5-minute cooldown timer

8. VERIFICATION (2 minutes later)
   ├─ Capture "after" metrics
   ├─ Compare: Did error_rate drop ≥50%?
   │  ├─ YES → Success! Generate post-mortem
   │  └─ NO  → Auto-rollback + escalate to human
   └─ Store incident in Postgres for future RAG retrieval
```

## Running Locally

### Quick Start (standalone)

```bash
# Build
cd services/target-service
go build -o /tmp/target-service ./cmd/server

# Run (SQLite only, no dependencies)
PORT=8080 /tmp/target-service

# Test
curl http://localhost:8080/health
curl http://localhost:8080/api/users
```

### With Optional Dependencies

```bash
# Run with Postgres connection pool tracking
POSTGRES_URL="postgresql://user:pass@localhost:5432/db" \
PORT=8080 \
/tmp/target-service

# Run with Redis cache layer
REDIS_ADDR="localhost:6379" \
REDIS_PASSWORD="secret" \
PORT=8080 \
/tmp/target-service
```

### Full Stack (Docker Compose)

```bash
# From repository root
make docker-up
# Starts: target-service, prometheus, alertmanager, grafana, agent, listener

# Trigger chaos
curl -X POST "http://localhost:8080/chaos/memleak?mb=100"

# Watch Prometheus alerts
open http://localhost:9090/alerts

# Watch AI agent logs
docker logs -f aether-guard-agent

# Cleanup
make docker-down
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | HTTP server port |
| `POSTGRES_URL` | (none) | PostgreSQL connection string (optional) |
| `REDIS_ADDR` | (none) | Redis address `host:port` (optional) |
| `REDIS_PASSWORD` | (none) | Redis password (optional) |

**Note:** If Postgres/Redis fail to connect, the service logs a warning and continues without health checks (graceful degradation).

## Testing

```bash
# Run all tests
go test ./... -v -race -count=1

# Run specific test
go test -v -run TestMemLeakHandler ./internal/chaos

# Check test coverage
go test -coverprofile=coverage.out ./...
go tool cover -html=coverage.out
```

**Test Coverage:**
- 19 tests across chaos, handlers, metrics packages
- 100% of chaos endpoints tested
- Handler edge cases (DB errors, malformed requests)
- Metrics collection and update logic

## Development Phases

### Phase A (Complete): Pattern Validation
- ✅ 9 chaos endpoints for all RCA patterns
- ✅ Real dependency testing (Postgres/Redis)
- ✅ Structured logging with RCA keywords
- ✅ Graceful degradation (Warn instead of Fatal)
- ✅ Backward compatibility (100% existing tests passing)

### Phase B (Complete): Full Observability
- ✅ 15 new metric families (RED + Resource + Business + Error)
- ✅ Circuit breaker with state transitions (0→2 verified)
- ✅ DB pool metrics tracking real Postgres (not SQLite)
- ✅ Process metrics (FDs, goroutines, memory)
- ✅ Business metrics (orders, payments, inventory, jobs)

### Phase C (Future): Advanced Patterns
- [ ] Distributed tracing (OpenTelemetry)
- [ ] Custom metrics injection via config
- [ ] Dynamic chaos schedules (GameDay mode)
- [ ] Multi-container dependency simulation

## Troubleshooting

### Service won't start
```bash
# Check if port is in use
lsof -i :8080

# Check logs
PORT=8080 /tmp/target-service 2>&1 | tee service.log
```

### Chaos not triggering alerts
```bash
# Verify Prometheus is scraping
curl http://localhost:8080/metrics | grep aether_guard

# Check alert rules
curl http://localhost:9090/api/v1/rules

# Verify Alertmanager
curl http://localhost:9093/api/v2/alerts
```

### Circuit breaker stuck in OPEN state
```bash
# Check current state
curl http://localhost:8080/metrics | grep circuit_breaker_state

# Reset requires fixing the dependency + waiting for reset timeout (30s)
# Or restart the service
```

## Architecture Details

### Package Structure

```
services/target-service/
├── cmd/server/
│   └── main.go              # Entry point, wires everything together
├── internal/
│   ├── chaos/               # Chaos engineering endpoints
│   │   ├── memory.go        # Memory leak injection
│   │   ├── cpu.go           # CPU spike injection
│   │   ├── latency.go       # Latency injection
│   │   ├── error.go         # Error rate injection
│   │   ├── goroutine_leak.go # Goroutine leak injection
│   │   ├── status.go        # Chaos status reporting
│   │   └── reset.go         # Chaos reset
│   ├── handlers/            # Business API endpoints
│   │   └── handlers.go      # /api/users, /api/orders, /health, /ready
│   ├── metrics/             # Prometheus metrics
│   │   ├── metrics.go       # Metric definitions + collectors
│   │   ├── process_unix.go  # Platform-specific FD counting
│   │   └── process_other.go # Fallback for non-Unix
│   ├── infrastructure/      # External dependencies
│   │   ├── postgres.go      # Postgres health check + circuit breaker
│   │   └── redis.go         # Redis health check + circuit breaker
│   ├── circuitbreaker/      # Circuit breaker pattern
│   │   └── breaker.go       # State machine (closed/half_open/open)
│   ├── cache/               # Redis-backed cache layer
│   │   └── cache.go         # Cache with hit rate metrics
│   ├── jobs/                # Background job simulation
│   │   └── jobs.go          # Job queue manager for metrics
│   └── db/                  # SQLite database
│       └── db.go            # Schema + seed data
└── README.md                # This file
```

### Key Design Decisions

**Why SQLite for business data?**
- Embedded (no external dependency required for basic operation)
- Fast enough for demo workloads (not production-scale)
- Simplifies local development and testing
- Real Postgres is optional (for connection pool metrics only)

**Why separate chaos package?**
- Clean separation: business logic vs. failure injection
- Each chaos type is isolated (memory.go, cpu.go, etc.)
- Easy to add new failure modes without touching business code
- Testable in isolation

**Why optional dependencies?**
- Service should work standalone (developer experience)
- Optional Postgres/Redis enable testing Pattern 6 (dependency failures)
- Graceful degradation: Warn instead of Fatal if connection fails
- Mirrors real production services (robust to partial outages)

**Why circuit breaker pattern?**
- Protects service from cascading failures
- Provides clear observability (state metric: 0/1/2)
- Demonstrates AI agent's ability to detect protection mechanisms
- Production-realistic behavior (not just crash-and-burn)

## Contributing

When adding new features:

1. **Preserve backward compatibility**: All existing endpoints, metrics, logs must continue working
2. **Add tests**: Every new endpoint needs test coverage
3. **Update metrics**: New features should emit relevant Prometheus metrics
4. **Log structured**: Use `zap.Logger` with appropriate levels (Debug/Info/Warn/Error)
5. **Document chaos patterns**: If adding new failure modes, document which RCA pattern it tests

## License

Part of the Aether-Guard project. See repository root for license details.

## See Also

- [Aether-Guard Main README](../../README.md) - Full system architecture
- [Phase A Validation](../../PHASE_A4_COMPLETE_VALIDATION.md) - Pattern testing results
- [Phase B Verification](../../PHASE_B_COMPLETE_VERIFICATION.md) - Metrics verification
- [CLAUDE.md](../../CLAUDE.md) - AI assistant guidance for this codebase

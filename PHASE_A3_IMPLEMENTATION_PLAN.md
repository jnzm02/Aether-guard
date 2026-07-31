# Phase A.3: Minimal Implementation for Pattern Validation

**Goal**: Implement ONLY what's needed to trigger and validate all 9 existing RCA patterns
**Strategy**: Smallest possible surface area → easiest to debug if something breaks
**Success Criteria**: All 9 patterns fire correctly in Phase A.4

---

## Implementation Scope (MINIMAL)

### What We MUST Implement:

1. **Preserved chaos endpoints** (6 existing)
   - `/chaos/memleak` (Pattern 3)
   - `/chaos/cpu` (Patterns 4a, 4b)
   - `/chaos/error` (Pattern 7)
   - `/chaos/latency` (not used by patterns, but part of existing API)
   - `/chaos/reset` (cleanup)
   - `/chaos/status` (observability)

2. **New goroutine leak endpoint** (1 new, critical for Pattern 8)
   - `/chaos/goroutine-leak` (Pattern 8)

3. **Preserved metrics** (all existing names/labels)
   - HTTP: `aether_guard_http_requests_total`, `aether_guard_http_request_duration_seconds`
   - Chaos: `aether_guard_chaos_memleak_bytes_allocated`, `aether_guard_chaos_cpu_cores_active`, `aether_guard_chaos_errors_injected_total`
   - Runtime: `aether_guard_runtime_goroutines`, `aether_guard_runtime_heap_inuse_bytes`

4. **Preserved HTTP endpoints** (existing API surface)
   - `/api/users` (minimal stub, just for HTTP metrics)
   - `/api/orders` (minimal stub)
   - `/health`, `/ready`, `/metrics`

5. **Real dependency connections** (Pattern 6 validation ONLY)
   - Postgres connection (doesn't need queries, just health check that can fail)
   - Redis connection (doesn't need caching logic, just ping that can fail)
   - Logs connection errors with stdlib error messages

6. **Preserved logging**
   - Startup: "starting" keyword
   - Fatal errors: "failed" keyword
   - Dependency errors: `zap.Error(err)` with stdlib messages

### What We DEFER to Phase B+:

- ❌ Business logic (orders, checkout, inventory)
- ❌ Caching layer (cache service, cache metrics)
- ❌ Worker pools (background jobs)
- ❌ Circuit breakers (external API resilience)
- ❌ New business metrics
- ❌ New chaos modes (connection-leak, cache-stampede, cascading)
- ❌ Complex failure scenarios

**Rationale**: None of the above are needed to validate existing patterns. Add them AFTER confirming backward compatibility.

---

## File Structure (Minimal)

```
services/target-service/
├── cmd/
│   └── server/
│       └── main.go                    # Entrypoint with real Postgres/Redis connections
├── internal/
│   ├── chaos/
│   │   ├── memory.go                  # PRESERVED: /chaos/memleak
│   │   ├── cpu.go                     # PRESERVED: /chaos/cpu
│   │   ├── latency.go                 # PRESERVED: /chaos/latency
│   │   ├── error.go                   # PRESERVED: /chaos/error
│   │   ├── goroutine_leak.go          # NEW: /chaos/goroutine-leak (Pattern 8)
│   │   ├── status.go                  # PRESERVED: /chaos/status
│   │   └── reset.go                   # PRESERVED: /chaos/reset
│   ├── handlers/
│   │   ├── users.go                   # Minimal stub (just responds 200)
│   │   ├── orders.go                  # Minimal stub
│   │   └── health.go                  # Health check (pings Postgres/Redis)
│   ├── metrics/
│   │   ├── http.go                    # HTTP golden signals + middleware
│   │   ├── chaos.go                   # Chaos metrics
│   │   └── runtime.go                 # Runtime collector (goroutines, heap)
│   └── infrastructure/
│       ├── postgres.go                # Minimal: connection + health check only
│       └── redis.go                   # Minimal: connection + health check only
├── Dockerfile
├── go.mod
└── go.sum
```

---

## Implementation Details

### 1. Main Entrypoint (cmd/server/main.go)

**Purpose**: Wire up all endpoints, connect to real Postgres/Redis for Pattern 6 validation

```go
package main

import (
    "context"
    "net/http"
    "os"
    "os/signal"
    "syscall"
    "time"

    "github.com/aether-guard/target-service/internal/chaos"
    "github.com/aether-guard/target-service/internal/handlers"
    "github.com/aether-guard/target-service/internal/infrastructure"
    "github.com/aether-guard/target-service/internal/metrics"
    "github.com/prometheus/client_golang/prometheus/promhttp"
    "go.uber.org/zap"
)

func main() {
    logger, _ := zap.NewProduction()
    defer logger.Sync()

    // ── Real dependencies (Pattern 6 validation) ─────────────────────────────
    ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
    defer cancel()

    // Optional Postgres connection (enable with POSTGRES_URL env var)
    var pg *infrastructure.PostgresHealth
    if pgURL := os.Getenv("POSTGRES_URL"); pgURL != "" {
        var err error
        pg, err = infrastructure.NewPostgresHealth(ctx, pgURL, logger)
        if err != nil {
            // PRESERVED: "failed" keyword for Pattern 7
            logger.Fatal("failed to connect to Postgres", zap.Error(err))
        }
        defer pg.Close()
        logger.Info("✅ Postgres connection established")
    }

    // Optional Redis connection (enable with REDIS_ADDR env var)
    var rdb *infrastructure.RedisHealth
    if redisAddr := os.Getenv("REDIS_ADDR"); redisAddr != "" {
        var err error
        rdb, err = infrastructure.NewRedisHealth(ctx, redisAddr, os.Getenv("REDIS_PASSWORD"), logger)
        if err != nil {
            logger.Fatal("failed to connect to Redis", zap.Error(err))
        }
        defer rdb.Close()
        logger.Info("✅ Redis connection established")
    }

    // ── HTTP router ───────────────────────────────────────────────────────────
    mux := http.NewServeMux()

    // PRESERVED: Business endpoints (minimal stubs)
    mux.Handle("/api/users", metrics.HTTPMiddleware(handlers.UsersHandler(logger)))
    mux.Handle("/api/orders", metrics.HTTPMiddleware(handlers.OrdersHandler(logger)))

    // PRESERVED: Chaos endpoints
    mux.Handle("/chaos/memleak", metrics.HTTPMiddleware(chaos.MemLeakHandler(logger)))
    mux.Handle("/chaos/cpu", metrics.HTTPMiddleware(chaos.CPUSpikeHandler(logger)))
    mux.Handle("/chaos/latency", metrics.HTTPMiddleware(chaos.LatencyHandler(logger)))
    mux.Handle("/chaos/error", metrics.HTTPMiddleware(chaos.ErrorHandler(logger)))
    mux.Handle("/chaos/status", chaos.StatusHandler(logger))
    mux.Handle("/chaos/reset", chaos.ResetHandler(logger))

    // NEW: Goroutine leak endpoint (Pattern 8)
    mux.Handle("/chaos/goroutine-leak", metrics.HTTPMiddleware(chaos.GoroutineLeakHandler(logger)))

    // PRESERVED: Observability
    mux.Handle("/metrics", promhttp.Handler())
    mux.Handle("/health", handlers.HealthHandler(pg, rdb, logger))
    mux.Handle("/ready", handlers.ReadyHandler(pg, rdb, logger))

    // ── Background runtime metrics collector ─────────────────────────────────
    stopChan := make(chan struct{})
    metrics.StartRuntimeCollector(stopChan)

    // ── HTTP server ───────────────────────────────────────────────────────────
    port := os.Getenv("PORT")
    if port == "" {
        port = "8080"
    }
    addr := ":" + port

    server := &http.Server{
        Addr:         addr,
        Handler:      mux,
        ReadTimeout:  30 * time.Second,
        WriteTimeout: 60 * time.Second,
        IdleTimeout:  120 * time.Second,
    }

    go func() {
        // PRESERVED: "starting" keyword for Pattern 2
        logger.Info("🚀 aether-guard/target-service starting",
            zap.String("addr", addr),
            zap.String("version", "1.2.0"),
        )
        if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
            logger.Fatal("server terminated unexpectedly", zap.Error(err))
        }
    }()

    // ── Graceful shutdown ─────────────────────────────────────────────────────
    quit := make(chan os.Signal, 1)
    signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
    <-quit

    logger.Info("shutdown signal received — draining requests")
    close(stopChan)

    shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer shutdownCancel()

    if err := server.Shutdown(shutdownCtx); err != nil {
        logger.Fatal("graceful shutdown failed", zap.Error(err))
    }

    logger.Info("✅ target-service shutdown complete")
}
```

### 2. Postgres Health Check (infrastructure/postgres.go)

**Purpose**: ONLY for Pattern 6 validation (dependency failure logs). No queries, no repos, just connection + ping.

```go
package infrastructure

import (
    "context"
    "fmt"

    "github.com/jackc/pgx/v5/pgxpool"
    "go.uber.org/zap"
)

type PostgresHealth struct {
    pool   *pgxpool.Pool
    logger *zap.Logger
}

func NewPostgresHealth(ctx context.Context, url string, logger *zap.Logger) (*PostgresHealth, error) {
    pool, err := pgxpool.New(ctx, url)
    if err != nil {
        // This error will contain stdlib messages like:
        // "failed to connect to `host=localhost`: dial tcp 127.0.0.1:5432: connect: connection refused"
        logger.Error("postgres connection failed", zap.Error(err))
        return nil, fmt.Errorf("postgres connection failed: %w", err)
    }

    if err := pool.Ping(ctx); err != nil {
        logger.Error("postgres ping failed", zap.Error(err))
        pool.Close()
        return nil, fmt.Errorf("postgres ping failed: %w", err)
    }

    return &PostgresHealth{pool: pool, logger: logger}, nil
}

func (p *PostgresHealth) Ping(ctx context.Context) error {
    if err := p.pool.Ping(ctx); err != nil {
        // Pattern 6 validation: these errors contain "connection refused", "timeout", etc.
        p.logger.Error("postgres health check failed", zap.Error(err))
        return err
    }
    return nil
}

func (p *PostgresHealth) Close() {
    p.pool.Close()
}
```

### 3. Redis Health Check (infrastructure/redis.go)

**Purpose**: Same as Postgres — ONLY for Pattern 6 validation.

```go
package infrastructure

import (
    "context"
    "fmt"

    "github.com/redis/go-redis/v9"
    "go.uber.org/zap"
)

type RedisHealth struct {
    client *redis.Client
    logger *zap.Logger
}

func NewRedisHealth(ctx context.Context, addr, password string, logger *zap.Logger) (*RedisHealth, error) {
    client := redis.NewClient(&redis.Options{
        Addr:     addr,
        Password: password,
        DB:       0,
    })

    if err := client.Ping(ctx).Err(); err != nil {
        // Pattern 6 validation: errors like "dial tcp ...: connect: connection refused"
        logger.Error("redis connection failed", zap.Error(err))
        return nil, fmt.Errorf("redis connection failed: %w", err)
    }

    return &RedisHealth{client: client, logger: logger}, nil
}

func (r *RedisHealth) Ping(ctx context.Context) error {
    if err := r.client.Ping(ctx).Err(); err != nil {
        r.logger.Error("redis health check failed", zap.Error(err))
        return err
    }
    return nil
}

func (r *RedisHealth) Close() error {
    return r.client.Close()
}
```

### 4. Health Handlers (handlers/health.go)

**Purpose**: Ping dependencies, log errors if they fail (Pattern 6 validation)

```go
package handlers

import (
    "context"
    "encoding/json"
    "net/http"
    "time"

    "github.com/aether-guard/target-service/internal/infrastructure"
    "go.uber.org/zap"
)

func HealthHandler(pg *infrastructure.PostgresHealth, rdb *infrastructure.RedisHealth, logger *zap.Logger) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
        defer cancel()

        status := "healthy"
        deps := map[string]string{}

        // Check Postgres (if configured)
        if pg != nil {
            if err := pg.Ping(ctx); err != nil {
                status = "unhealthy"
                deps["postgres"] = "down"
                // Pattern 6: error logged with stdlib message
            } else {
                deps["postgres"] = "up"
            }
        }

        // Check Redis (if configured)
        if rdb != nil {
            if err := rdb.Ping(ctx); err != nil {
                status = "unhealthy"
                deps["redis"] = "down"
                // Pattern 6: error logged with stdlib message
            } else {
                deps["redis"] = "up"
            }
        }

        code := http.StatusOK
        if status == "unhealthy" {
            code = http.StatusServiceUnavailable
        }

        w.Header().Set("Content-Type", "application/json")
        w.WriteHeader(code)
        json.NewEncoder(w).Encode(map[string]any{
            "service":      "aether-guard/target-service",
            "status":       status,
            "version":      "1.2.0",
            "dependencies": deps,
        })
    })
}

func ReadyHandler(pg *infrastructure.PostgresHealth, rdb *infrastructure.RedisHealth, logger *zap.Logger) http.Handler {
    // Same implementation as HealthHandler for now
    return HealthHandler(pg, rdb, logger)
}
```

### 5. Minimal Business Handlers (handlers/users.go, handlers/orders.go)

**Purpose**: ONLY to generate HTTP metrics for Pattern 5. No real logic.

```go
package handlers

import (
    "encoding/json"
    "net/http"

    "go.uber.org/zap"
)

func UsersHandler(logger *zap.Logger) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        // Minimal stub: just return empty list
        w.Header().Set("Content-Type", "application/json")
        json.NewEncoder(w).Encode(map[string]any{
            "users": []any{},
        })
    })
}

func OrdersHandler(logger *zap.Logger) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        // Minimal stub: just return empty list
        w.Header().Set("Content-Type", "application/json")
        json.NewEncoder(w).Encode(map[string]any{
            "orders": []any{},
        })
    })
}
```

### 6. Goroutine Leak Handler (chaos/goroutine_leak.go)

**Purpose**: NEW endpoint for Pattern 8 validation.

```go
package chaos

import (
    "encoding/json"
    "net/http"
    "strconv"
    "sync/atomic"
    "time"

    "github.com/aether-guard/target-service/internal/metrics"
    "go.uber.org/zap"
)

var leakedGoroutines atomic.Int32

func GoroutineLeakHandler(logger *zap.Logger) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        count := getQueryInt(r, "count", 100)
        duration := getQueryInt(r, "duration", 0) // 0 = infinite

        if count < 1 || count > 10000 {
            http.Error(w, "count must be 1-10000", http.StatusBadRequest)
            return
        }

        for i := 0; i < count; i++ {
            leakedGoroutines.Add(1)
            go func() {
                // INTENTIONAL LEAK: No defer, no cleanup
                if duration == 0 {
                    select {} // block forever
                } else {
                    time.Sleep(time.Duration(duration) * time.Second)
                    leakedGoroutines.Add(-1)
                }
            }()
        }

        metrics.ChaosErrorsInjected.WithLabelValues("goroutine_leak").Inc()

        logger.Warn("⚠️  chaos/goroutine-leak: goroutine leak injected",
            zap.Int("count", count),
            zap.Int("duration_seconds", duration),
            zap.Int32("total_leaked", leakedGoroutines.Load()),
        )

        w.Header().Set("Content-Type", "application/json")
        json.NewEncoder(w).Encode(map[string]any{
            "event":        "goroutine_leak_injected",
            "count":        count,
            "duration":     duration,
            "total_leaked": leakedGoroutines.Load(),
        })
    })
}

func getQueryInt(r *http.Request, key string, defaultVal int) int {
    s := r.URL.Query().Get(key)
    if s == "" {
        return defaultVal
    }
    v, err := strconv.Atoi(s)
    if err != nil {
        return defaultVal
    }
    return v
}
```

### 7. All Other Chaos Handlers (chaos/*.go)

**Copy existing implementations from current target-service with NO CHANGES**:
- `chaos/memory.go` ← from current `internal/chaos/chaos.go` (MemLeakHandler)
- `chaos/cpu.go` ← from current `internal/chaos/chaos.go` (CPUSpikeHandler)
- `chaos/latency.go` ← from current `internal/chaos/chaos.go` (LatencyHandler)
- `chaos/error.go` ← from current `internal/chaos/chaos.go` (ErrorHandler)
- `chaos/status.go` ← from current `internal/chaos/chaos.go` (StatusHandler)
- `chaos/reset.go` ← from current `internal/chaos/chaos.go` (ResetHandler)

**Action**: Split existing `internal/chaos/chaos.go` into separate files (refactor, no logic changes)

### 8. Metrics (metrics/*.go)

**Copy existing implementations from current target-service with NO CHANGES**:
- `metrics/http.go` ← from current `internal/metrics/metrics.go` (HTTPRequestsTotal, HTTPRequestDuration, Middleware)
- `metrics/chaos.go` ← from current `internal/metrics/metrics.go` (chaos metrics)
- `metrics/runtime.go` ← from current `internal/metrics/metrics.go` (RuntimeGoroutines, RuntimeHeapBytes, StartRuntimeCollector)

**Action**: Split existing `internal/metrics/metrics.go` into separate files (refactor, no logic changes)

---

## Dependencies (go.mod)

```go
module github.com/aether-guard/target-service

go 1.21

require (
    github.com/jackc/pgx/v5 v5.5.0           // Postgres driver
    github.com/prometheus/client_golang v1.17.0
    github.com/redis/go-redis/v9 v9.3.0      // Redis client
    go.uber.org/zap v1.26.0
)
```

---

## Docker Compose for Pattern 6 Validation

**File**: `infra/docker-compose.test.yml` (separate from production compose)

```yaml
version: "3.9"

services:
  # Target service with real dependencies
  target-service:
    build: ../services/target-service
    ports:
      - "8080:8080"
    environment:
      - PORT=8080
      - POSTGRES_URL=postgres://testuser:testpass@postgres:5432/testdb
      - REDIS_ADDR=redis:6379
      - REDIS_PASSWORD=testpass
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - test-net

  # Postgres for Pattern 6 validation
  postgres:
    image: postgres:16-alpine
    environment:
      - POSTGRES_USER=testuser
      - POSTGRES_PASSWORD=testpass
      - POSTGRES_DB=testdb
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U testuser -d testdb"]
      interval: 5s
      timeout: 3s
      retries: 3
    networks:
      - test-net

  # Redis for Pattern 6 validation
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass testpass
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "testpass", "ping"]
      interval: 5s
      timeout: 3s
      retries: 3
    networks:
      - test-net

networks:
  test-net:
    driver: bridge
```

---

## Phase A.3 Implementation Checklist

- [ ] Refactor existing `chaos.go` → split into `chaos/*.go` (memory, cpu, latency, error, status, reset)
- [ ] Refactor existing `metrics.go` → split into `metrics/*.go` (http, chaos, runtime)
- [ ] Implement `chaos/goroutine_leak.go` (new)
- [ ] Implement `infrastructure/postgres.go` (minimal health check only)
- [ ] Implement `infrastructure/redis.go` (minimal health check only)
- [ ] Implement `handlers/health.go` (ping dependencies, log errors)
- [ ] Update `handlers/users.go`, `handlers/orders.go` to minimal stubs
- [ ] Update `cmd/server/main.go` (wire up dependencies, preserve logging)
- [ ] Update `go.mod` (add pgx, redis)
- [ ] Create `infra/docker-compose.test.yml` (Postgres + Redis for Pattern 6)
- [ ] Verify all existing metrics still exported
- [ ] Verify all existing chaos endpoints still work

---

## Phase A.4 Validation Plan (AFTER A.3 Implementation)

### Test Environment Setup

```bash
# Start test stack (target-service + Postgres + Redis)
cd infra
docker-compose -f docker-compose.test.yml up -d

# Verify all services healthy
docker-compose -f docker-compose.test.yml ps
```

### Pattern Validation Tests

**Pattern 1: OOM Kill**
```bash
# Allocate 2GB (exceeds typical container limit)
curl -X POST "http://localhost:8080/chaos/memleak?mb=2000"

# Check logs for kernel OOM message
docker logs target-service 2>&1 | grep -i "oom"
# Expected: "Out of memory" or "oom-kill" from kernel

# ✅ PASS if: Kernel OOM message found in logs
# ❌ FAIL if: No kernel message (increase mb or lower container memory limit)
```

**Pattern 2: Restart Loop**
```bash
# Restart container 3 times
for i in 1 2 3; do
    docker restart target-service
    sleep 5
done

# Check logs for "starting" messages
docker logs target-service 2>&1 | grep -i "starting" | wc -l
# Expected: >= 3

# ✅ PASS if: "starting" appears 3+ times
# ❌ FAIL if: Message missing or different wording
```

**Pattern 3: Memory Leak**
```bash
# Inject 1GB leak
curl -X POST "http://localhost:8080/chaos/memleak?mb=1000"

# Check metrics
curl -s http://localhost:8080/metrics | grep "aether_guard_chaos_memleak_bytes_allocated"
# Expected: 1048576000 (1GB in bytes)

curl -s http://localhost:8080/metrics | grep "go_memstats_heap_alloc_bytes"
# Expected: Value increased by ~1GB

# ✅ PASS if: Both metrics show expected values
# ❌ FAIL if: Metrics missing or incorrect
```

**Pattern 4a: CPU Saturation (Traffic)**
```bash
# Inject CPU spike
curl "http://localhost:8080/chaos/cpu?cores=4&ms=60000" &

# Generate traffic (simulate load)
for i in {1..1000}; do
    curl -s http://localhost:8080/api/users > /dev/null &
done

# Check metrics (wait 10s for collection)
sleep 10
curl -s http://localhost:8080/metrics | grep "process_cpu_seconds_total"
curl -s http://localhost:8080/metrics | grep "aether_guard_http_requests_total"

# ✅ PASS if: CPU high AND request rate high
# ❌ FAIL if: Metrics don't show elevated values
```

**Pattern 4b: CPU Saturation (Efficiency)**
```bash
# Inject CPU spike WITHOUT traffic
curl "http://localhost:8080/chaos/cpu?cores=4&ms=60000"

# Check metrics (NO concurrent traffic)
sleep 10
curl -s http://localhost:8080/metrics | grep "process_cpu_seconds_total"
# Expected: High CPU usage

curl -s http://localhost:8080/metrics | grep "aether_guard_http_requests_total"
# Expected: Low request rate

# ✅ PASS if: CPU high, request rate normal
# ❌ FAIL if: Pattern indistinguishable from 4a
```

**Pattern 5: Traffic Spike**
```bash
# Generate high traffic + errors
for i in {1..5000}; do
    curl -s http://localhost:8080/api/users > /dev/null &
    curl -s http://localhost:8080/chaos/error?rate=0.1 > /dev/null &
done

# Check metrics
sleep 15
curl -s http://localhost:8080/metrics | grep "aether_guard_http_requests_total"
# Expected: High request rate

curl -s http://localhost:8080/metrics | grep "status_code=\"500\""
# Expected: Some 500 errors

# ✅ PASS if: High traffic + elevated errors
# ❌ FAIL if: Metrics don't show spike
```

**Pattern 6: Dependency Failure (CRITICAL TEST - NOT ASSUMED)**
```bash
# Stop Postgres
docker-compose -f docker-compose.test.yml stop postgres

# Trigger health check (causes connection attempt)
curl http://localhost:8080/health

# Check logs for stdlib error messages
docker logs target-service 2>&1 | grep -E "connection refused|dial tcp.*timeout|database.*unavailable"
# Expected: AT LEAST ONE match

# Stop Redis
docker-compose -f docker-compose.test.yml start postgres
docker-compose -f docker-compose.test.yml stop redis

# Trigger health check again
curl http://localhost:8080/health

# Check logs for Redis error
docker logs target-service 2>&1 | grep -E "connection refused|redis.*failed"
# Expected: AT LEAST ONE match

# ✅ PASS if: Exact error strings from rules.py patterns appear in logs
# ❌ FAIL if: Error messages don't match regex patterns (REQUIRES RULES.PY FIX)
```

**Pattern 7: Bad Deployment**
```bash
# Inject high error rate
curl "http://localhost:8080/chaos/error?rate=0.5" &

# Generate traffic to trigger errors
for i in {1..100}; do
    curl -s http://localhost:8080/api/users > /dev/null
done

# Check metrics
curl -s http://localhost:8080/metrics | grep "status_code=\"500\""
# Expected: ~50 errors (50% rate)

# Check logs for error messages
docker logs target-service 2>&1 | grep -i "error"

# ✅ PASS if: Error rate > 5% AND logs contain errors
# ❌ FAIL if: Metrics or logs missing
```

**Pattern 8: Goroutine Leak**
```bash
# Check baseline
curl -s http://localhost:8080/metrics | grep "aether_guard_runtime_goroutines"
# Expected: ~10-50 (baseline)

# Inject leak
curl -X POST "http://localhost:8080/chaos/goroutine-leak?count=500&duration=0"

# Wait for runtime collector (samples every 5s)
sleep 10

# Check metrics
curl -s http://localhost:8080/metrics | grep "aether_guard_runtime_goroutines"
# Expected: baseline + 500

# ✅ PASS if: Goroutine count increased by ~500
# ❌ FAIL if: No increase (endpoint not working)
```

**Pattern 9: (N/A - only 9 patterns total, this is a placeholder)**

---

## Success Criteria for Phase A.3 → A.4 Transition

Before proceeding to Phase B (full observability), ALL of the following must be TRUE:

- ✅ All 8 tests above pass
- ✅ Pattern 6 logs contain EXACT stdlib error strings (not assumed, tested)
- ✅ All existing metrics still exported with same names
- ✅ All existing chaos endpoints respond correctly
- ✅ Startup log contains "starting" keyword
- ✅ Fatal errors contain "failed" keyword
- ✅ No rules.py changes required (if required, document why and update)

If ANY test fails, FIX in Phase A.3 before moving forward. Surface area is small now, debugging is easy.

---

**Phase A.3 Status**: Ready to implement
**Next**: Implement minimal validation paths, then run Phase A.4 tests

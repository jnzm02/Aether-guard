# Phase A.3 Implementation Status

## Completed Files ✅

### 1. Chaos Handlers (Refactored)
- ✅ `internal/chaos/shared.go` - Shared state and utilities
- ✅ `internal/chaos/memory.go` - `/chaos/memleak` (Pattern 3)
- ✅ `internal/chaos/cpu.go` - `/chaos/cpu` (Patterns 4a, 4b)
- ✅ `internal/chaos/latency.go` - `/chaos/latency`
- ✅ `internal/chaos/error.go` - `/chaos/error` (Pattern 7)
- ✅ `internal/chaos/goroutine_leak.go` - `/chaos/goroutine-leak` (Pattern 8) **NEW**
- ✅ `internal/chaos/status.go` - `/chaos/status`
- ✅ `internal/chaos/reset.go` - `/chaos/reset`

### 2. Infrastructure (Health Checks)
- ✅ `internal/infrastructure/postgres.go` - Postgres health check (Pattern 6)
- ✅ `internal/infrastructure/redis.go` - Redis health check (Pattern 6)

## Remaining Work ⚠️

### 1. Delete Old File
```bash
rm services/target-service/internal/chaos/chaos.go
```
The functionality has been split into separate files above.

### 2. Update Handlers (internal/handlers/handlers.go)

Replace the HealthHandler and ReadyHandler functions with:

```go
// Add at top of file:
import (
	"context"
	"time"
)

// Add interface after imports:
type DependencyPinger interface {
	Ping(ctx context.Context) error
}

// Replace HealthHandler function (lines 116-126):
func HealthHandler(pg, rdb DependencyPinger, logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()

		status := "healthy"
		deps := map[string]string{}

		if pg != nil {
			if err := pg.Ping(ctx); err != nil {
				status = "unhealthy"
				deps["postgres"] = "down"
			} else {
				deps["postgres"] = "up"
			}
		}

		if rdb != nil {
			if err := rdb.Ping(ctx); err != nil {
				status = "unhealthy"
				deps["redis"] = "down"
			} else {
				deps["redis"] = "up"
			}
		}

		code := http.StatusOK
		if status == "unhealthy" {
			code = http.StatusServiceUnavailable
		}

		respondJSON(w, map[string]any{
			"service":      "aether-guard/target-service",
			"status":       status,
			"version":      "1.2.0",
			"dependencies": deps,
		})
		w.WriteHeader(code)
	})
}

// Replace ReadyHandler function (lines 128-134):
func ReadyHandler(pg, rdb DependencyPinger, logger *zap.Logger) http.Handler {
	return HealthHandler(pg, rdb, logger)
}
```

### 3. Update main.go (cmd/server/main.go)

Replace entire contents with:

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
	"github.com/aether-guard/target-service/internal/db"
	"github.com/aether-guard/target-service/internal/handlers"
	"github.com/aether-guard/target-service/internal/infrastructure"
	"github.com/aether-guard/target-service/internal/metrics"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.uber.org/zap"
)

func main() {
	logger, _ := zap.NewProduction()
	defer logger.Sync()

	// ── SQLite database (existing behavior) ──────────────────────────────────
	database, err := db.New()
	if err != nil {
		logger.Fatal("failed to initialise SQLite database", zap.Error(err))
	}
	defer database.Close()

	// ── Optional real dependencies (Pattern 6 validation) ────────────────────
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	var pg *infrastructure.PostgresHealth
	if pgURL := os.Getenv("POSTGRES_URL"); pgURL != "" {
		pg, err = infrastructure.NewPostgresHealth(ctx, pgURL, logger)
		if err != nil {
			logger.Fatal("failed to connect to Postgres", zap.Error(err))
		}
		defer pg.Close()
		logger.Info("✅ Postgres connection established")
	}

	var rdb *infrastructure.RedisHealth
	if redisAddr := os.Getenv("REDIS_ADDR"); redisAddr != "" {
		password := os.Getenv("REDIS_PASSWORD")
		rdb, err = infrastructure.NewRedisHealth(ctx, redisAddr, password, logger)
		if err != nil {
			logger.Fatal("failed to connect to Redis", zap.Error(err))
		}
		defer rdb.Close()
		logger.Info("✅ Redis connection established")
	}

	// ── HTTP router ───────────────────────────────────────────────────────────
	mux := http.NewServeMux()

	// PRESERVED: Business endpoints
	mux.Handle("/api/users", metrics.Middleware(handlers.UsersHandler(logger, database)))
	mux.Handle("/api/orders", metrics.Middleware(handlers.OrdersHandler(logger, database)))

	// PRESERVED: Existing chaos endpoints
	mux.Handle("/chaos/memleak", metrics.Middleware(chaos.MemLeakHandler(logger)))
	mux.Handle("/chaos/cpu", metrics.Middleware(chaos.CPUSpikeHandler(logger)))
	mux.Handle("/chaos/latency", metrics.Middleware(chaos.LatencyHandler(logger)))
	mux.Handle("/chaos/error", metrics.Middleware(chaos.ErrorHandler(logger)))
	mux.Handle("/chaos/status", chaos.StatusHandler(logger))
	mux.Handle("/chaos/reset", chaos.ResetHandler(logger))

	// NEW: Goroutine leak endpoint (Pattern 8)
	mux.Handle("/chaos/goroutine-leak", metrics.Middleware(chaos.GoroutineLeakHandler(logger)))

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

### 4. Update go.mod

Add dependencies:

```bash
cd services/target-service
go get github.com/jackc/pgx/v5@latest
go get github.com/redis/go-redis/v9@latest
go mod tidy
```

### 5. Build and Test Locally

```bash
cd services/target-service
go build -o /tmp/target-service ./cmd/server
/tmp/target-service
```

In another terminal:
```bash
# Test chaos endpoints
curl -X POST "http://localhost:8080/chaos/memleak?mb=10"
curl "http://localhost:8080/chaos/cpu?cores=2&ms=5000"
curl -X POST "http://localhost:8080/chaos/goroutine-leak?count=100&duration=0"
curl "http://localhost:8080/chaos/status"

# Test metrics
curl http://localhost:8080/metrics | grep aether_guard_runtime_goroutines
curl http://localhost:8080/metrics | grep aether_guard_chaos_memleak_bytes_allocated
```

### 6. Create Docker Compose Test File

Create `infra/docker-compose.test.yml`:

```yaml
version: "3.9"

services:
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

## Phase A.3 Completion Checklist

- [ ] Delete `internal/chaos/chaos.go`
- [ ] Update `internal/handlers/handlers.go` (HealthHandler, ReadyHandler)
- [ ] Replace `cmd/server/main.go` entirely
- [ ] Run `go get` for pgx and redis dependencies
- [ ] Run `go mod tidy`
- [ ] Build locally and test chaos endpoints
- [ ] Verify metrics are exported correctly
- [ ] Create `infra/docker-compose.test.yml`
- [ ] Test with Docker Compose: `docker-compose -f infra/docker-compose.test.yml up`
- [ ] Verify all services start healthy

Once all above are complete, proceed to **Phase A.4 Validation**.

## Next: Phase A.4 Validation Tests

After completing Phase A.3, run the validation tests from `PHASE_A3_IMPLEMENTATION_PLAN.md`:

1. Pattern 1 (OOM Kill) - Memory leak → kernel OOM
2. Pattern 2 (Restart Loop) - 3x container restart
3. Pattern 3 (Memory Leak) - Metrics validation
4. Pattern 4a/4b (CPU Saturation) - CPU + traffic metrics
5. Pattern 5 (Traffic Spike) - Load test
6. **Pattern 6 (Dependency Failure) - ACTUAL TEST: docker-compose stop postgres/redis**
7. Pattern 7 (Bad Deployment) - Error injection
8. Pattern 8 (Goroutine Leak) - New endpoint validation

**Critical**: Pattern 6 must show ACTUAL log output from stopped dependencies, not assumptions.

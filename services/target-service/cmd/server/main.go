// Aether-Guard target-service — the intentionally "broken" microservice.
//
// Phase A.3: Minimal implementation for pattern validation.
// - Preserves all existing chaos endpoints
// - Adds /chaos/goroutine-leak for Pattern 8
// - Adds optional Postgres/Redis health checks for Pattern 6
package main

import (
	"context"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/aether-guard/target-service/internal/cache"
	"github.com/aether-guard/target-service/internal/chaos"
	"github.com/aether-guard/target-service/internal/db"
	"github.com/aether-guard/target-service/internal/handlers"
	"github.com/aether-guard/target-service/internal/infrastructure"
	"github.com/aether-guard/target-service/internal/jobs"
	"github.com/aether-guard/target-service/internal/metrics"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.uber.org/zap"
)

func main() {
	logger, err := zap.NewProduction()
	if err != nil {
		panic(err)
	}
	defer logger.Sync() //nolint:errcheck

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
			logger.Warn("postgres connection failed, continuing without health check", zap.Error(err))
			pg = nil
		} else {
			defer pg.Close()
			logger.Info("✅ Postgres connection established")
		}
	}

	var rdb *infrastructure.RedisHealth
	if redisAddr := os.Getenv("REDIS_ADDR"); redisAddr != "" {
		password := os.Getenv("REDIS_PASSWORD")
		rdb, err = infrastructure.NewRedisHealth(ctx, redisAddr, password, logger)
		if err != nil {
			logger.Warn("redis connection failed, continuing without health check", zap.Error(err))
			rdb = nil
		} else {
			defer rdb.Close()
			logger.Info("✅ Redis connection established")
		}
	}

	// ── Phase B: Cache layer ──────────────────────────────────────────────────
	userCache, err := cache.New(ctx, os.Getenv("REDIS_ADDR"), os.Getenv("REDIS_PASSWORD"), "users", logger)
	if err != nil {
		logger.Warn("cache initialization failed, continuing without cache", zap.Error(err))
	}
	if userCache != nil {
		defer userCache.Close()
		logger.Info("✅ Cache layer initialized")
	}

	// ── Phase B: Background job queue manager ─────────────────────────────────
	jobManager := jobs.NewManager("order_processing")
	jobManager.Start()
	defer jobManager.Stop()
	logger.Info("✅ Background job manager started")

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

	// Fix for typed nil interface issue: convert nil pointers to proper nil interfaces
	var pgPinger, rdbPinger handlers.DependencyPinger
	if pg != nil {
		pgPinger = pg
	}
	if rdb != nil {
		rdbPinger = rdb
	}
	mux.Handle("/health", handlers.HealthHandler(pgPinger, rdbPinger, logger))
	mux.Handle("/ready", handlers.ReadyHandler(pgPinger, rdbPinger, logger))

	// ── Background runtime metrics collector ─────────────────────────────────
	stopChan := make(chan struct{})
	metrics.StartRuntimeCollector(stopChan)

	// ── Phase B: DB pool metrics collector ────────────────────────────────────
	// Track real Postgres pool if configured, otherwise skip (SQLite metrics aren't meaningful)
	if pg != nil {
		startPostgresPoolCollector(pg, stopChan, logger)
	}

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

// startPostgresPoolCollector periodically collects Postgres connection pool metrics.
// Phase B: Tracks real production pool stats (not SQLite which is capped at 1 connection).
func startPostgresPoolCollector(pg *infrastructure.PostgresHealth, stop <-chan struct{}, logger *zap.Logger) {
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()

		for {
			select {
			case <-ticker.C:
				pool := pg.GetPool()
				stats := pool.Stat()

				// pgxpool.Stat provides: AcquiredConns(), IdleConns(), TotalConns(), MaxConns()
				metrics.UpdateDBPoolMetrics(struct {
					OpenConnections int
					InUse           int
					Idle            int
					MaxOpen         int
				}{
					OpenConnections: int(stats.TotalConns()),
					InUse:           int(stats.AcquiredConns()),
					Idle:            int(stats.IdleConns()),
					MaxOpen:         int(stats.MaxConns()),
				})
			case <-stop:
				return
			}
		}
	}()
}

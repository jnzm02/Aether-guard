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

	"github.com/aether-guard/target-service/internal/chaos"
	"github.com/aether-guard/target-service/internal/db"
	"github.com/aether-guard/target-service/internal/handlers"
	"github.com/aether-guard/target-service/internal/infrastructure"
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

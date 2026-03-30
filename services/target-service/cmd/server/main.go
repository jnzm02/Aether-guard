// Aether-Guard target-service — the intentionally "broken" microservice.
//
// This service exposes:
//   - Production-like API endpoints to generate healthy baseline traffic
//   - Chaos endpoints to inject the three canonical failure modes
//   - A /metrics endpoint for Prometheus scraping
//   - /health and /ready probes for orchestration
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

	mux := http.NewServeMux()

	// ── Production-like API endpoints ────────────────────────────────────────
	// Wrapped with metrics.Middleware to record Latency + Traffic SLIs.
	mux.Handle("/api/users", metrics.Middleware(handlers.UsersHandler(logger)))
	mux.Handle("/api/orders", metrics.Middleware(handlers.OrdersHandler(logger)))

	// ── Chaos injection endpoints ─────────────────────────────────────────────
	// These are the "break glass" controls that Aether-Guard's AI agent will
	// observe triggering via alert spikes.
	//
	//   POST /chaos/memleak?mb=50     — allocate & retain 50 MiB
	//   GET  /chaos/latency?ms=3000   — inject 3 s delay
	//   GET  /chaos/error?rate=0.5    — 50% of requests return HTTP 500
	//   POST /chaos/reset             — release all chaos state
	mux.Handle("/chaos/memleak", metrics.Middleware(chaos.MemLeakHandler(logger)))
	mux.Handle("/chaos/latency", metrics.Middleware(chaos.LatencyHandler(logger)))
	mux.Handle("/chaos/error", metrics.Middleware(chaos.ErrorHandler(logger)))
	mux.Handle("/chaos/reset", chaos.ResetHandler(logger))

	// ── Observability & health endpoints ─────────────────────────────────────
	mux.Handle("/metrics", promhttp.Handler())
	mux.Handle("/health", handlers.HealthHandler(logger))
	mux.Handle("/ready", handlers.ReadyHandler(logger))

	addr := ":8080"
	if p := os.Getenv("PORT"); p != "" {
		addr = ":" + p
	}

	server := &http.Server{
		Addr:         addr,
		Handler:      mux,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 60 * time.Second, // generous for chaos/latency endpoint
		IdleTimeout:  120 * time.Second,
	}

	// Start server in background goroutine.
	go func() {
		logger.Info("🚀 aether-guard/target-service starting",
			zap.String("addr", addr),
			zap.String("version", "1.0.0"),
		)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Fatal("server terminated unexpectedly", zap.Error(err))
		}
	}()

	// ── Graceful shutdown ─────────────────────────────────────────────────────
	// Matches Google SRE practice: drain in-flight requests before stopping.
	// We give 30 s for requests to finish — well above our p99 SLO of 200 ms.
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	sig := <-quit

	logger.Info("shutdown signal received — draining requests",
		zap.String("signal", sig.String()),
	)

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if err := server.Shutdown(ctx); err != nil {
		logger.Fatal("graceful shutdown failed", zap.Error(err))
	}

	logger.Info("✅ target-service shutdown complete")
}

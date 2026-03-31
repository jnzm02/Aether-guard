// Aether-Guard target-service — the intentionally "broken" microservice.
//
// This service exposes:
//   - Production-like API endpoints backed by a real SQLite database
//   - Chaos endpoints to inject all four canonical failure modes
//   - A /metrics endpoint for Prometheus scraping (including runtime stats)
//   - /health and /ready probes for orchestration
//   - /debug/pprof/* endpoints for CPU and heap profiling
package main

import (
"context"
"net/http"
"net/http/pprof"
"os"
"os/signal"
"syscall"
"time"

"github.com/aether-guard/target-service/internal/chaos"
"github.com/aether-guard/target-service/internal/db"
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

// ── SQLite database ───────────────────────────────────────────────────────
database, err := db.New()
if err != nil {
logger.Fatal("failed to initialise SQLite database", zap.Error(err))
}
defer database.Close()

mux := http.NewServeMux()

// ── Production-like API endpoints ────────────────────────────────────────
// Wrapped with metrics.Middleware to record Latency + Traffic SLIs.
// Backed by a real SQLite DB so latency metrics reflect actual I/O.
mux.Handle("/api/users", metrics.Middleware(handlers.UsersHandler(logger, database)))
mux.Handle("/api/orders", metrics.Middleware(handlers.OrdersHandler(logger, database)))

// ── Chaos injection endpoints ─────────────────────────────────────────────
//
//   POST /chaos/memleak?mb=50          — allocate & retain 50 MiB
//   GET  /chaos/latency?ms=3000        — inject 3 s delay
//   GET  /chaos/error?rate=0.5         — 50% of requests return HTTP 500
//   GET  /chaos/cpu?cores=2&ms=30000   — burn 2 CPU cores for 30 s
//   GET  /chaos/status                 — show active chaos state
//   POST /chaos/reset                  — release all chaos state
mux.Handle("/chaos/memleak", metrics.Middleware(chaos.MemLeakHandler(logger)))
mux.Handle("/chaos/latency", metrics.Middleware(chaos.LatencyHandler(logger)))
mux.Handle("/chaos/error", metrics.Middleware(chaos.ErrorHandler(logger)))
mux.Handle("/chaos/cpu", metrics.Middleware(chaos.CPUSpikeHandler(logger)))
mux.Handle("/chaos/status", chaos.StatusHandler(logger))
mux.Handle("/chaos/reset", chaos.ResetHandler(logger))

// ── Observability & health endpoints ─────────────────────────────────────
mux.Handle("/metrics", promhttp.Handler())
mux.Handle("/health", handlers.HealthHandler(logger))
mux.Handle("/ready", handlers.ReadyHandler(logger))

// ── Go pprof profiling endpoints ─────────────────────────────────────────
// Access with: go tool pprof http://localhost:8080/debug/pprof/heap
mux.HandleFunc("/debug/pprof/", pprof.Index)
mux.HandleFunc("/debug/pprof/cmdline", pprof.Cmdline)
mux.HandleFunc("/debug/pprof/profile", pprof.Profile)
mux.HandleFunc("/debug/pprof/symbol", pprof.Symbol)
mux.HandleFunc("/debug/pprof/trace", pprof.Trace)
mux.Handle("/debug/pprof/goroutine", pprof.Handler("goroutine"))
mux.Handle("/debug/pprof/heap", pprof.Handler("heap"))
mux.Handle("/debug/pprof/allocs", pprof.Handler("allocs"))
mux.Handle("/debug/pprof/block", pprof.Handler("block"))
mux.Handle("/debug/pprof/mutex", pprof.Handler("mutex"))
mux.Handle("/debug/pprof/threadcreate", pprof.Handler("threadcreate"))

// ── Background runtime metric collector ──────────────────────────────────
// Samples goroutine count, heap usage, and GC pause every 5 s.
runtimeStop := make(chan struct{})
metrics.StartRuntimeCollector(runtimeStop)

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
zap.String("version", "1.1.0"),
)
if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
logger.Fatal("server terminated unexpectedly", zap.Error(err))
}
}()

// ── Graceful shutdown ─────────────────────────────────────────────────────
quit := make(chan os.Signal, 1)
signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
sig := <-quit

logger.Info("shutdown signal received — draining requests",
zap.String("signal", sig.String()),
)

close(runtimeStop) // stop runtime metric collector

ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
defer cancel()

if err := server.Shutdown(ctx); err != nil {
logger.Fatal("graceful shutdown failed", zap.Error(err))
}

logger.Info("✅ target-service shutdown complete")
}

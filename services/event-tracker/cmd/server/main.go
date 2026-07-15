package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.uber.org/zap"

	"github.com/jnzm02/aether-guard/event-tracker/internal/github"
	"github.com/jnzm02/aether-guard/event-tracker/internal/handlers"
	"github.com/jnzm02/aether-guard/event-tracker/internal/metrics"
)

func main() {
	// Initialize structured logger
	logger, err := zap.NewProduction()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to initialize logger: %v\n", err)
		os.Exit(1)
	}
	defer logger.Sync()

	// Configuration
	githubToken := os.Getenv("GITHUB_TOKEN")
	if githubToken == "" {
		logger.Warn("GITHUB_TOKEN not set - rate limit will be 60 req/hour (vs 5000 with token)")
	}

	port := os.Getenv("PORT")
	if port == "" {
		port = "8083"
	}

	logger.Info("Starting event-tracker service",
		zap.String("port", port),
		zap.Bool("has_github_token", githubToken != ""),
	)

	// Initialize cache
	cache := github.NewCache(logger)

	// Initialize GitHub client with rate limit callback
	rateLimitCallback := func(remaining, limit int, reset time.Time) {
		metrics.UpdateRateLimit(remaining, limit)
	}
	client := github.NewClient(githubToken, logger, rateLimitCallback)

	// Start background poller
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go cache.StartPoller(ctx, client)

	// HTTP handlers
	mux := http.NewServeMux()
	mux.Handle("/api/events", handlers.NewEventsHandler(cache))
	mux.Handle("/health", handlers.NewHealthHandler(cache))
	mux.Handle("/metrics", promhttp.Handler())
	mux.Handle("/", handlers.NewIndexHandler(cache))

	// Wrap with Prometheus middleware
	instrumentedMux := metrics.Middleware(mux)

	// HTTP server
	server := &http.Server{
		Addr:         ":" + port,
		Handler:      instrumentedMux,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
		IdleTimeout:  30 * time.Second,
	}

	// Start server in goroutine
	go func() {
		logger.Info("HTTP server listening", zap.String("addr", server.Addr))
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Fatal("HTTP server failed", zap.Error(err))
		}
	}()

	// Graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	<-sigChan

	logger.Info("Shutdown signal received, stopping gracefully...")

	// Stop poller
	cancel()

	// Shutdown HTTP server
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()

	if err := server.Shutdown(shutdownCtx); err != nil {
		logger.Error("HTTP server shutdown failed", zap.Error(err))
	}

	logger.Info("Service stopped")
}

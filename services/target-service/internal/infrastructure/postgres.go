// Package infrastructure provides minimal health-check-only connections to
// external dependencies for Pattern 6 (Dependency Failure) validation.
// Phase B: Adds circuit breaker and timeout tracking.
package infrastructure

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/aether-guard/target-service/internal/circuitbreaker"
	"github.com/aether-guard/target-service/internal/metrics"
	"github.com/jackc/pgx/v5/pgxpool"
	"go.uber.org/zap"
)

// PostgresHealth provides a minimal Postgres connection for health checking only.
// NO queries, NO repositories, just connection + ping for Pattern 6 validation.
// Phase B: Includes circuit breaker.
type PostgresHealth struct {
	pool           *pgxpool.Pool
	logger         *zap.Logger
	circuitBreaker *circuitbreaker.CircuitBreaker
}

func NewPostgresHealth(ctx context.Context, url string, logger *zap.Logger) (*PostgresHealth, error) {
	// Service Contract requirement: explicit pool configuration for stable saturation_ratio denominator
	// Default: 15 connections (reasonable for a demo service under moderate load)
	// Justification: Small enough to avoid resource waste, large enough to handle concurrent health checks + chaos endpoints
	maxConns := 15
	if envMax := os.Getenv("POSTGRES_POOL_MAX_CONNS"); envMax != "" {
		if parsed, err := strconv.Atoi(envMax); err == nil && parsed > 0 {
			maxConns = parsed
		}
	}

	config, err := pgxpool.ParseConfig(url)
	if err != nil {
		logger.Error("postgres config parse failed", zap.Error(err))
		return nil, fmt.Errorf("postgres config parse failed: %w", err)
	}

	// Set explicit MaxConns for Service Contract compliance
	config.MaxConns = int32(maxConns)

	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		// Pattern 6 validation: This error will contain stdlib messages like:
		// "failed to connect to `host=localhost`: dial tcp 127.0.0.1:5432: connect: connection refused"
		logger.Error("postgres connection failed", zap.Error(err))
		return nil, fmt.Errorf("postgres connection failed: %w", err)
	}

	if err := pool.Ping(ctx); err != nil {
		// Pattern 6 validation: errors like "dial tcp ... i/o timeout"
		logger.Error("postgres ping failed", zap.Error(err))
		pool.Close()
		return nil, fmt.Errorf("postgres ping failed: %w", err)
	}

	// Phase B: Create circuit breaker (5 failures, 30 second reset)
	cb := circuitbreaker.New("postgres", 5, 30*time.Second)

	return &PostgresHealth{
		pool:           pool,
		logger:         logger,
		circuitBreaker: cb,
	}, nil
}

func (p *PostgresHealth) Ping(ctx context.Context) error {
	// Phase B: Use circuit breaker
	return p.circuitBreaker.Call(ctx, func(ctx context.Context) error {
		if err := p.pool.Ping(ctx); err != nil {
			// Pattern 6 validation: these errors contain "connection refused", "timeout", etc.
			p.logger.Error("postgres health check failed", zap.Error(err))

			// Phase B: Track timeout errors via Service Contract SDK
			if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
				if metrics.ContractMetrics != nil {
					metrics.ContractMetrics.RecordTimeoutError("postgres")
				}
			}

			return err
		}
		return nil
	})
}

// GetPool returns the underlying connection pool for metrics collection.
// Phase B: Exposes pool stats for db_connections_* metrics.
func (p *PostgresHealth) GetPool() *pgxpool.Pool {
	return p.pool
}

func (p *PostgresHealth) Close() {
	p.pool.Close()
}

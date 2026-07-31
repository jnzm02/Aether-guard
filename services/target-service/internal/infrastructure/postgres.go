// Package infrastructure provides minimal health-check-only connections to
// external dependencies for Pattern 6 (Dependency Failure) validation.
package infrastructure

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
	"go.uber.org/zap"
)

// PostgresHealth provides a minimal Postgres connection for health checking only.
// NO queries, NO repositories, just connection + ping for Pattern 6 validation.
type PostgresHealth struct {
	pool   *pgxpool.Pool
	logger *zap.Logger
}

func NewPostgresHealth(ctx context.Context, url string, logger *zap.Logger) (*PostgresHealth, error) {
	pool, err := pgxpool.New(ctx, url)
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

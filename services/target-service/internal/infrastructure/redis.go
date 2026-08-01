package infrastructure

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/aether-guard/target-service/internal/circuitbreaker"
	"github.com/aether-guard/target-service/internal/metrics"
	"github.com/redis/go-redis/v9"
	"go.uber.org/zap"
)

// RedisHealth provides a minimal Redis connection for health checking only.
// NO caching logic, just connection + ping for Pattern 6 validation.
// Phase B: Includes circuit breaker.
type RedisHealth struct {
	client         *redis.Client
	logger         *zap.Logger
	circuitBreaker *circuitbreaker.CircuitBreaker
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

	// Phase B: Create circuit breaker (5 failures, 30 second reset)
	cb := circuitbreaker.New("redis", 5, 30*time.Second)

	return &RedisHealth{
		client:         client,
		logger:         logger,
		circuitBreaker: cb,
	}, nil
}

func (r *RedisHealth) Ping(ctx context.Context) error {
	// Phase B: Use circuit breaker
	return r.circuitBreaker.Call(ctx, func(ctx context.Context) error {
		if err := r.client.Ping(ctx).Err(); err != nil {
			// Pattern 6 validation: stdlib error messages appear here
			r.logger.Error("redis health check failed", zap.Error(err))

			// Phase B: Track timeout errors via Service Contract SDK
			if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
				if metrics.ContractMetrics != nil {
					metrics.ContractMetrics.RecordTimeoutError("redis")
				}
			}

			return err
		}
		return nil
	})
}

func (r *RedisHealth) Close() error {
	return r.client.Close()
}

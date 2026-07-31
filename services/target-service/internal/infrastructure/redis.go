package infrastructure

import (
	"context"
	"fmt"

	"github.com/redis/go-redis/v9"
	"go.uber.org/zap"
)

// RedisHealth provides a minimal Redis connection for health checking only.
// NO caching logic, just connection + ping for Pattern 6 validation.
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
		// Pattern 6 validation: stdlib error messages appear here
		r.logger.Error("redis health check failed", zap.Error(err))
		return err
	}
	return nil
}

func (r *RedisHealth) Close() error {
	return r.client.Close()
}

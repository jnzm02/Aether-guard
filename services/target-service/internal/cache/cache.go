// Package cache provides a Redis-backed cache with Prometheus metrics.
// Phase B: Cache hit rate and eviction tracking.
package cache

import (
	"context"
	"fmt"
	"sync/atomic"
	"time"

	"github.com/aether-guard/target-service/internal/metrics"
	"github.com/redis/go-redis/v9"
	"go.uber.org/zap"
)

// Cache wraps a Redis client and tracks hit/miss metrics.
type Cache struct {
	client    *redis.Client
	logger    *zap.Logger
	cacheType string

	// In-memory counters for hit rate calculation
	hits   atomic.Uint64
	misses atomic.Uint64
}

// New creates a new Cache instance.
// If addr is empty, returns a nil cache (no-op mode).
func New(ctx context.Context, addr, password, cacheType string, logger *zap.Logger) (*Cache, error) {
	if addr == "" {
		logger.Info("cache disabled (no REDIS_ADDR)")
		return nil, nil
	}

	client := redis.NewClient(&redis.Options{
		Addr:         addr,
		Password:     password,
		DB:           0,
		DialTimeout:  5 * time.Second,
		ReadTimeout:  3 * time.Second,
		WriteTimeout: 3 * time.Second,
	})

	// Test connection
	if err := client.Ping(ctx).Err(); err != nil {
		return nil, fmt.Errorf("redis ping failed: %w", err)
	}

	c := &Cache{
		client:    client,
		logger:    logger,
		cacheType: cacheType,
	}

	// Start background metrics updater
	go c.updateMetrics()

	return c, nil
}

// Get retrieves a value from the cache.
// Returns empty string if not found or cache is disabled.
func (c *Cache) Get(ctx context.Context, key string) (string, error) {
	if c == nil {
		return "", nil
	}

	val, err := c.client.Get(ctx, key).Result()
	if err == redis.Nil {
		c.misses.Add(1)
		return "", nil
	}
	if err != nil {
		c.misses.Add(1)
		return "", err
	}

	c.hits.Add(1)
	return val, nil
}

// Set stores a value in the cache with TTL.
func (c *Cache) Set(ctx context.Context, key, value string, ttl time.Duration) error {
	if c == nil {
		return nil
	}

	return c.client.Set(ctx, key, value, ttl).Err()
}

// Delete removes a key from the cache (counts as eviction).
func (c *Cache) Delete(ctx context.Context, key string) error {
	if c == nil {
		return nil
	}

	deleted, err := c.client.Del(ctx, key).Result()
	if err != nil {
		return err
	}

	if deleted > 0 {
		metrics.CacheEvictionsTotal.WithLabelValues(c.cacheType, "manual").Add(float64(deleted))
	}

	return nil
}

// Close closes the Redis connection.
func (c *Cache) Close() error {
	if c == nil {
		return nil
	}
	return c.client.Close()
}

// updateMetrics periodically calculates and updates cache hit rate.
func (c *Cache) updateMetrics() {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		hits := c.hits.Load()
		misses := c.misses.Load()
		total := hits + misses

		if total > 0 {
			hitRate := float64(hits) / float64(total)
			metrics.CacheHitRate.WithLabelValues(c.cacheType).Set(hitRate)
		}

		// Track evictions from Redis INFO stats (approximation)
		// In production, you'd track this more accurately via Redis events
		// For now, we'll just emit the metric when we manually delete
	}
}

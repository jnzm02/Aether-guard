package github

import (
	"context"
	"sync"
	"time"

	"go.uber.org/zap"
)

const (
	maxCacheSize    = 100 // Keep last 100 events
	maxCacheAge     = 30 * time.Minute
	pollInterval    = 2 * time.Minute
)

// Cache stores GitHub events with staleness tracking.
type Cache struct {
	events    []Event
	fetchedAt time.Time
	mu        sync.RWMutex
	logger    *zap.Logger
}

// NewCache creates an event cache.
func NewCache(logger *zap.Logger) *Cache {
	return &Cache{
		events: []Event{},
		logger: logger,
	}
}

// Get returns cached events and age in seconds.
// Returns empty slice if cache is empty (not yet populated).
func (c *Cache) Get() (events []Event, ageSeconds int) {
	c.mu.RLock()
	defer c.mu.RUnlock()

	if c.fetchedAt.IsZero() {
		return []Event{}, 0
	}

	age := time.Since(c.fetchedAt)
	return c.events, int(age.Seconds())
}

// Update replaces the cache with fresh events.
func (c *Cache) Update(events []Event) {
	c.mu.Lock()
	defer c.mu.Unlock()

	// Limit cache size
	if len(events) > maxCacheSize {
		events = events[:maxCacheSize]
	}

	c.events = events
	c.fetchedAt = time.Now()
	c.logger.Info("Cache updated",
		zap.Int("event_count", len(events)),
		zap.Time("fetched_at", c.fetchedAt),
	)
}

// IsStale returns true if cache is older than maxCacheAge.
func (c *Cache) IsStale() bool {
	c.mu.RLock()
	defer c.mu.RUnlock()

	if c.fetchedAt.IsZero() {
		return true
	}

	return time.Since(c.fetchedAt) > maxCacheAge
}

// StartPoller starts background polling of GitHub API.
// Updates cache on success, logs error and serves stale data on failure.
func (c *Cache) StartPoller(ctx context.Context, client *Client) {
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	// Initial fetch (blocking)
	c.logger.Info("Starting initial GitHub API fetch")
	if events, err := client.FetchEvents(ctx); err == nil {
		c.Update(events)
	} else {
		c.logger.Error("Initial fetch failed - will retry on next poll",
			zap.Error(err),
		)
	}

	// Background polling
	for {
		select {
		case <-ctx.Done():
			c.logger.Info("Poller stopped")
			return
		case <-ticker.C:
			events, err := client.FetchEvents(ctx)
			if err != nil {
				_, age := c.Get()
				c.logger.Error("GitHub API poll failed - serving stale cache",
					zap.Error(err),
					zap.Int("cache_age_seconds", age),
					zap.Bool("serving_stale_data", true),
				)
				// Don't update cache - serve stale data
				continue
			}
			c.Update(events)
		}
	}
}

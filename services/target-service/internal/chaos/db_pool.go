package chaos

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/aether-guard/target-service/internal/infrastructure"
	"go.uber.org/zap"
)

// DBPoolPressureHandler holds Postgres connections for a sustained period
// to make pool metrics (especially in_use) visible during the 5s collector interval.
//
// Query parameters:
//   - connections: number of concurrent connections to hold (default: 5, max: 10)
//   - duration_seconds: how long to hold each connection (default: 10, max: 30)
//
// This chaos endpoint is safe for production testing because:
// - It's bounded (max 10 connections, max 30 seconds)
// - Connections are properly released via defer
// - Context cancellation is respected
// - Part of target-service's test/chaos API
func DBPoolPressureHandler(pg *infrastructure.PostgresHealth, logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if pg == nil {
			http.Error(w, "postgres not configured", http.StatusServiceUnavailable)
			return
		}

		connections := queryInt(r, "connections", 5, 1, 10)
		if connections < 0 {
			http.Error(w, "invalid 'connections' parameter — must be 1..10", http.StatusBadRequest)
			return
		}

		durationSec := queryInt(r, "duration_seconds", 10, 1, 30)
		if durationSec < 0 {
			http.Error(w, "invalid 'duration_seconds' parameter — must be 1..30", http.StatusBadRequest)
			return
		}

		duration := time.Duration(durationSec) * time.Second
		pool := pg.GetPool()

		logger.Info("🌀 chaos/db-pool-pressure: acquiring connections",
			zap.Int("connections", connections),
			zap.Duration("duration", duration),
		)

		var wg sync.WaitGroup
		successCount := 0
		var mu sync.Mutex

		// Spawn N goroutines, each acquires a connection and holds it
		for i := 0; i < connections; i++ {
			wg.Add(1)
			go func(id int) {
				defer wg.Done()

				// Acquire connection with 5s timeout
				ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
				defer cancel()

				conn, err := pool.Acquire(ctx)
				if err != nil {
					logger.Warn("chaos/db-pool-pressure: acquire failed",
						zap.Int("worker_id", id),
						zap.Error(err),
					)
					return
				}
				defer conn.Release()

				mu.Lock()
				successCount++
				mu.Unlock()

				logger.Debug("chaos/db-pool-pressure: connection acquired",
					zap.Int("worker_id", id),
				)

				// Hold the connection for the specified duration
				select {
				case <-time.After(duration):
					logger.Debug("chaos/db-pool-pressure: releasing connection",
						zap.Int("worker_id", id),
					)
				case <-r.Context().Done():
					logger.Debug("chaos/db-pool-pressure: cancelled",
						zap.Int("worker_id", id),
					)
				}
			}(i)
		}

		wg.Wait()

		logger.Info("✅ chaos/db-pool-pressure: complete",
			zap.Int("requested", connections),
			zap.Int("acquired", successCount),
			zap.Duration("held_for", duration),
		)

		respondJSON(w, http.StatusOK, map[string]any{
			"event":              "db_pool_pressure",
			"requested":          connections,
			"acquired":           successCount,
			"duration_seconds":   durationSec,
			"note":               "check db_connections_in_use metric during this window",
		})
	})
}

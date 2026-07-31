package chaos

import (
	"net/http"
	"time"

	"github.com/aether-guard/target-service/internal/metrics"
	"go.uber.org/zap"
)

// GoroutineLeakHandler spawns count goroutines that block for duration seconds.
// If duration=0, goroutines block forever (intentional leak).
//
// NEW: Pattern 8 (Goroutine Leak) requires this endpoint for validation.
// The existing /chaos/cpu endpoint properly cleans up goroutines (defer),
// so it cannot trigger Pattern 8. This endpoint intentionally leaks.
//
//	POST /chaos/goroutine-leak?count=500&duration=0
func GoroutineLeakHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		count := queryInt(r, "count", 100, 1, 10000)
		duration := queryInt(r, "duration", 0, 0, 3600) // 0 = infinite

		if count < 0 {
			http.Error(w, "invalid 'count' parameter — must be 1..10000", http.StatusBadRequest)
			return
		}
		if duration < 0 {
			http.Error(w, "invalid 'duration' parameter — must be 0..3600", http.StatusBadRequest)
			return
		}

		for i := 0; i < count; i++ {
			leakedGoroutines.Add(1)
			go func() {
				// INTENTIONAL LEAK: No defer, no cleanup
				if duration == 0 {
					select {} // block forever
				} else {
					time.Sleep(time.Duration(duration) * time.Second)
					leakedGoroutines.Add(-1)
				}
			}()
		}

		metrics.ChaosErrorsInjected.WithLabelValues("goroutine_leak").Inc()

		logger.Warn("⚠️  chaos/goroutine-leak: goroutine leak injected",
			zap.Int("count", count),
			zap.Int("duration_seconds", duration),
			zap.Int32("total_leaked", leakedGoroutines.Load()),
		)

		respondJSON(w, http.StatusOK, map[string]any{
			"event":        "goroutine_leak_injected",
			"count":        count,
			"duration":     duration,
			"total_leaked": leakedGoroutines.Load(),
		})
	})
}

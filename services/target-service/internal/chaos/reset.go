package chaos

import (
	"net/http"

	"github.com/aether-guard/target-service/internal/metrics"
	"go.uber.org/zap"
)

// ResetHandler releases all leaked memory and zeroes out chaos counters.
// The GC will reclaim memory on the next collection cycle after this call.
//
// PRESERVED: Part of existing chaos API for cleanup between tests.
func ResetHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		memLeakMu.Lock()
		freed := totalLeakedBytes.Load()
		memLeakStore = nil // drop all references → eligible for GC
		memLeakMu.Unlock()

		totalLeakedBytes.Store(0)
		metrics.MemLeakBytesAllocated.Set(0)

		// Stop any active CPU spike.
		cpuMu.Lock()
		if cpuCancel != nil {
			cpuCancel()
			cpuCancel = nil
		}
		cpuMu.Unlock()
		metrics.ChaosCPUCoresActive.Set(0)

		// Note: We don't kill leaked goroutines (they're blocked forever).
		// This is intentional — to recover from goroutine leak, restart the service.
		leakedCount := leakedGoroutines.Load()

		logger.Info("✅  chaos/reset: all chaos state cleared",
			zap.Int64("bytes_freed", freed),
			zap.Int32("goroutines_still_leaked", leakedCount),
		)

		respondJSON(w, http.StatusOK, map[string]any{
			"status":                  "reset",
			"freed_bytes":             freed,
			"freed_mb":                freed / (1024 * 1024),
			"goroutines_still_leaked": leakedCount,
		})
	})
}

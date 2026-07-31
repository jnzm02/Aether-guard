package chaos

import (
	"net/http"

	"go.uber.org/zap"
)

// StatusHandler returns a JSON snapshot of all active chaos injections.
//
// PRESERVED: Part of existing chaos API for observability.
//
//	GET /chaos/status
func StatusHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		leakedBytes := totalLeakedBytes.Load()
		activeCores := cpuActive.Load()
		leakedGoros := leakedGoroutines.Load()

		respondJSON(w, http.StatusOK, map[string]any{
			"memory_leak_active":     leakedBytes > 0,
			"memory_leaked_bytes":    leakedBytes,
			"memory_leaked_mb":       leakedBytes / (1024 * 1024),
			"cpu_spike_active":       activeCores > 0,
			"cpu_cores_active":       activeCores,
			"goroutine_leak_active":  leakedGoros > 0,
			"goroutines_leaked":      leakedGoros,
		})
	})
}

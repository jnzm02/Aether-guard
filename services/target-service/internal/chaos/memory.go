package chaos

import (
	"net/http"

	"github.com/aether-guard/target-service/internal/metrics"
	"go.uber.org/zap"
)

// MemLeakHandler allocates mb MiB of memory per call and intentionally keeps
// it alive. Each allocation touches every byte to ensure physical pages are
// committed (not just virtual address space reserved).
//
// PRESERVED: Pattern 3 (Memory Leak) depends on this exact behavior.
func MemLeakHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mb := queryInt(r, "mb", 10, 1, 500)
		if mb < 0 {
			http.Error(w, "invalid 'mb' parameter — must be 1..500", http.StatusBadRequest)
			return
		}

		// Allocate and dirty every byte to guarantee physical page commitment.
		chunk := make([]byte, mb*1024*1024)
		for i := range chunk {
			chunk[i] = byte(i)
		}

		memLeakMu.Lock()
		memLeakStore = append(memLeakStore, chunk)
		memLeakMu.Unlock()

		total := totalLeakedBytes.Add(int64(mb * 1024 * 1024))

		// Update Prometheus saturation signal.
		metrics.MemLeakBytesAllocated.Set(float64(total))
		metrics.ChaosErrorsInjected.WithLabelValues("memleak").Inc()

		logger.Warn("⚠️  chaos/memleak: memory leak injected",
			zap.Int("mb_this_call", mb),
			zap.Int64("total_leaked_bytes", total),
		)

		respondJSON(w, http.StatusOK, map[string]any{
			"event":              "leak_injected",
			"mb_this_call":       mb,
			"total_leaked_bytes": total,
			"total_leaked_mb":    total / (1024 * 1024),
		})
	})
}

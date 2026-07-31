package chaos

import (
	"context"
	"net/http"
	"runtime"
	"time"

	"github.com/aether-guard/target-service/internal/metrics"
	"go.uber.org/zap"
)

// CPUSpikeHandler spins `cores` goroutines executing compute-intensive work for
// `ms` milliseconds. Closing the previous spike before starting a new one
// prevents unbounded goroutine accumulation.
//
// PRESERVED: Patterns 4a, 4b (CPU Saturation) depend on this exact behavior.
//
//	GET /chaos/cpu?cores=2&ms=30000
func CPUSpikeHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cores := queryInt(r, "cores", 1, 1, runtime.NumCPU()*4)
		durationMs := queryInt(r, "ms", 30_000, 100, 300_000)
		if cores < 0 || durationMs < 0 {
			http.Error(w, "invalid parameters: cores 1..NumCPU*4, ms 100..300000", http.StatusBadRequest)
			return
		}

		// Cancel any running spike before starting a new one.
		cpuMu.Lock()
		if cpuCancel != nil {
			cpuCancel()
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Duration(durationMs)*time.Millisecond)
		cpuCancel = cancel
		cpuMu.Unlock()

		for i := 0; i < cores; i++ {
			cpuActive.Add(1)
			go func() {
				defer cpuActive.Add(-1)
				burnCPU(ctx)
			}()
		}

		metrics.ChaosCPUCoresActive.Set(float64(cores))
		metrics.ChaosErrorsInjected.WithLabelValues("cpu_spike").Inc()

		logger.Warn("⚠️  chaos/cpu: CPU spike injected",
			zap.Int("cores", cores),
			zap.Int("duration_ms", durationMs),
		)

		respondJSON(w, http.StatusOK, map[string]any{
			"event":       "cpu_spike_injected",
			"cores":       cores,
			"duration_ms": durationMs,
		})
	})
}

// burnCPU runs a tight XOR-hash loop until ctx is cancelled.
// Each outer iteration does 500 k multiplies — hard for the compiler to
// eliminate but still yields to the scheduler via runtime.Gosched.
func burnCPU(ctx context.Context) {
	var sink uint64
	for {
		select {
		case <-ctx.Done():
			return
		default:
			for i := uint64(0); i < 500_000; i++ {
				sink ^= i*6364136223846793005 + 1442695040888963407
			}
			_ = sink
			runtime.Gosched() // allow context check by Go scheduler
		}
	}
}

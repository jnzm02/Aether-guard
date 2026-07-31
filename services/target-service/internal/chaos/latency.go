package chaos

import (
	"net/http"
	"time"

	"github.com/aether-guard/target-service/internal/metrics"
	"go.uber.org/zap"
)

// LatencyHandler sleeps for ms milliseconds before responding.
// It respects context cancellation so client-side timeouts surface correctly
// in the metrics (the request will appear as a non-200 in the SLI).
//
// PRESERVED: Not used by RCA patterns, but part of existing chaos API.
func LatencyHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ms := queryInt(r, "ms", 2000, 0, 30000)
		if ms < 0 {
			http.Error(w, "invalid 'ms' parameter — must be 0..30000", http.StatusBadRequest)
			return
		}

		delay := time.Duration(ms) * time.Millisecond
		start := time.Now()

		select {
		case <-time.After(delay):
			actual := time.Since(start)

			metrics.ChaosLatencyInjected.Observe(actual.Seconds())
			metrics.ChaosErrorsInjected.WithLabelValues("latency_spike").Inc()

			logger.Warn("⚠️  chaos/latency: latency spike injected",
				zap.Duration("requested_delay", delay),
				zap.Duration("actual_delay", actual),
			)

			respondJSON(w, http.StatusOK, map[string]any{
				"event":     "latency_injected",
				"delay_ms":  ms,
				"actual_ms": actual.Milliseconds(),
			})

		case <-r.Context().Done():
			// The client (or upstream proxy) cancelled — this counts against
			// our error budget because we failed to serve the request.
			actual := time.Since(start)
			metrics.ChaosErrorsInjected.WithLabelValues("latency_timeout").Inc()

			logger.Warn("⚠️  chaos/latency: client cancelled during induced delay",
				zap.Error(r.Context().Err()),
				zap.Duration("elapsed_before_cancel", actual),
			)
			// Cannot write a response; connection is gone.
		}
	})
}

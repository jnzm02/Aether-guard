package chaos

import (
	"math/rand"
	"net/http"
	"strconv"

	"github.com/aether-guard/target-service/internal/metrics"
	"go.uber.org/zap"
)

// errorMessages is a corpus of realistic-sounding 500 error messages.
var errorMessages = []string{
	"database connection pool exhausted after 30s wait",
	"upstream payment-service: context deadline exceeded (timeout=5s)",
	"nil pointer dereference in OrderProcessor.Commit()",
	"redis cluster: CLUSTERDOWN — hash slot not served",
	"OOM kill: kernel out of memory: killed process 4421 (target-svc)",
	"pq: too many connections for role 'app_user' (max=100)",
	"gRPC: code=Unavailable desc=transport is closing",
}

// ErrorHandler returns HTTP 500 responses at the given probability rate.
// rate=1.0 means every request fails; rate=0.1 means 10% fail.
//
// PRESERVED: Pattern 7 (Bad Deployment) depends on this exact behavior.
func ErrorHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rateStr := r.URL.Query().Get("rate")
		rate := 1.0
		if rateStr != "" {
			parsed, err := strconv.ParseFloat(rateStr, 64)
			if err != nil || parsed < 0 || parsed > 1 {
				http.Error(w, "invalid 'rate' parameter — must be 0.0..1.0", http.StatusBadRequest)
				return
			}
			rate = parsed
		}

		if rand.Float64() < rate {
			msg := errorMessages[rand.Intn(len(errorMessages))]

			metrics.ChaosErrorsInjected.WithLabelValues("http_500").Inc()

			logger.Error("⚠️  chaos/error: injecting HTTP 500",
				zap.String("simulated_error", msg),
				zap.Float64("configured_rate", rate),
			)

			respondJSON(w, http.StatusInternalServerError, map[string]any{
				"error":   msg,
				"code":    500,
				"service": "aether-guard/target-service",
			})
			return
		}

		respondJSON(w, http.StatusOK, map[string]any{
			"status":          "no_error_this_time",
			"configured_rate": rate,
		})
	})
}

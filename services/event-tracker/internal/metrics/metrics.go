// Package metrics defines Prometheus instrumentation for event-tracker.
// Matches target-service conventions for consistency with existing monitoring.
package metrics

import (
	"net/http"
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	// HTTPRequestsTotal tracks our own API endpoint traffic.
	HTTPRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "aether_guard",
			Subsystem: "http",
			Name:      "requests_total",
			Help:      "Total HTTP requests partitioned by method, path, and HTTP status code.",
		},
		[]string{"method", "path", "status_code"},
	)

	// HTTPRequestDuration tracks our own API endpoint latency.
	HTTPRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "aether_guard",
			Subsystem: "http",
			Name:      "request_duration_seconds",
			Help:      "HTTP request latency histogram.",
			Buckets:   []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.5, 5.0},
		},
		[]string{"method", "path"},
	)

	// ─────────────────────────────────────────────────────────────────────
	// External dependency metrics — KEY SIGNALS for incident detection
	// ─────────────────────────────────────────────────────────────────────

	// GitHubAPICallDuration tracks GitHub API call latency (the external dependency).
	GitHubAPICallDuration = promauto.NewHistogram(
		prometheus.HistogramOpts{
			Namespace: "aether_guard",
			Subsystem: "github_api",
			Name:      "call_duration_seconds",
			Help:      "GitHub API call latency histogram (external dependency).",
			Buckets:   []float64{0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0},
		},
	)

	// GitHubAPICallsTotal counts GitHub API call outcomes.
	// status="success" | "error"
	// http_code=200|403|500|0 (0 = network error)
	GitHubAPICallsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "aether_guard",
			Subsystem: "github_api",
			Name:      "calls_total",
			Help:      "Total GitHub API calls partitioned by status and HTTP code.",
		},
		[]string{"status", "http_code"},
	)

	// GitHubAPIRateLimitRemaining tracks remaining API calls before rate limit reset.
	GitHubAPIRateLimitRemaining = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "github_api",
			Name:      "rate_limit_remaining",
			Help:      "Remaining GitHub API calls before rate limit reset.",
		},
	)

	// GitHubAPIRateLimitTotal tracks the total rate limit (usually 5000 with token, 60 without).
	GitHubAPIRateLimitTotal = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "github_api",
			Name:      "rate_limit_total",
			Help:      "Total GitHub API rate limit.",
		},
	)

	// GitHubCacheAge tracks seconds since last successful fetch.
	// High values indicate prolonged API failure (alert-worthy).
	GitHubCacheAge = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "github_api",
			Name:      "cache_age_seconds",
			Help:      "Seconds since last successful GitHub API fetch.",
		},
	)

	// GitHubCachedEventCount tracks number of events in cache.
	GitHubCachedEventCount = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "github_api",
			Name:      "cached_event_count",
			Help:      "Number of events currently cached.",
		},
	)
)

// RecordGitHubAPICall records a GitHub API call outcome with latency.
func RecordGitHubAPICall(duration time.Duration, statusCode int, err error) {
	GitHubAPICallDuration.Observe(duration.Seconds())

	status := "success"
	code := strconv.Itoa(statusCode)

	if err != nil || statusCode >= 400 {
		status = "error"
		if statusCode == 0 {
			code = "0" // Network error (no HTTP response)
		}
	}

	GitHubAPICallsTotal.WithLabelValues(status, code).Inc()
}

// UpdateRateLimit updates GitHub rate limit gauges.
func UpdateRateLimit(remaining, total int) {
	GitHubAPIRateLimitRemaining.Set(float64(remaining))
	GitHubAPIRateLimitTotal.Set(float64(total))
}

// UpdateCacheMetrics updates cache age and event count.
func UpdateCacheMetrics(ageSeconds, eventCount int) {
	GitHubCacheAge.Set(float64(ageSeconds))
	GitHubCachedEventCount.Set(float64(eventCount))
}

// Middleware wraps an http.Handler to instrument requests (matches target-service pattern).
func Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()

		// Wrap ResponseWriter to capture status code
		wrapped := &responseWriter{ResponseWriter: w, statusCode: http.StatusOK}

		next.ServeHTTP(wrapped, r)

		duration := time.Since(start)
		HTTPRequestDuration.WithLabelValues(r.Method, r.URL.Path).Observe(duration.Seconds())
		HTTPRequestsTotal.WithLabelValues(r.Method, r.URL.Path, strconv.Itoa(wrapped.statusCode)).Inc()
	})
}

// responseWriter wraps http.ResponseWriter to capture status code.
type responseWriter struct {
	http.ResponseWriter
	statusCode int
}

func (rw *responseWriter) WriteHeader(code int) {
	rw.statusCode = code
	rw.ResponseWriter.WriteHeader(code)
}

// Package metrics defines all Prometheus instrumentation for the target-service.
// We follow Google SRE golden-signal conventions:
//   - Latency  → http_request_duration_seconds histogram
//   - Traffic  → http_requests_total counter
//   - Errors   → included via status_code label + chaos_errors_injected_total
//   - Saturation → chaos_memleak_bytes_allocated gauge
package metrics

import (
	"net/http"
	"runtime"
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	// HTTPRequestsTotal tracks total requests — the primary Traffic SLI.
	HTTPRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "aether_guard",
			Subsystem: "http",
			Name:      "requests_total",
			Help:      "Total HTTP requests partitioned by method, path, and HTTP status code.",
		},
		[]string{"method", "path", "status_code"},
	)

	// HTTPRequestDuration is the core Latency SLI.
	// Buckets are tuned to our SLO: 99% of requests must complete in < 200ms.
	HTTPRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "aether_guard",
			Subsystem: "http",
			Name:      "request_duration_seconds",
			Help:      "HTTP request latency histogram. SLO: p99 < 200ms.",
			Buckets:   []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.5, 5.0, 10.0},
		},
		[]string{"method", "path"},
	)

	// MemLeakBytesAllocated tracks the Saturation signal from chaos injection.
	MemLeakBytesAllocated = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "chaos",
			Name:      "memleak_bytes_allocated",
			Help:      "Total bytes intentionally leaked by the chaos memory-leak endpoint.",
		},
	)

	// ChaosErrorsInjected counts synthetic failure events by failure type.
	ChaosErrorsInjected = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "aether_guard",
			Subsystem: "chaos",
			Name:      "errors_injected_total",
			Help:      "Total chaos errors injected, partitioned by failure type.",
		},
		[]string{"type"},
	)

	// ChaosLatencyInjected tracks the actual injected delay distribution.
	ChaosLatencyInjected = promauto.NewHistogram(
		prometheus.HistogramOpts{
			Namespace: "aether_guard",
			Subsystem: "chaos",
			Name:      "latency_injected_seconds",
			Help:      "Distribution of artificial latency injected by the chaos latency endpoint.",
			Buckets:   []float64{0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0},
		},
	)

	// ErrorBudgetConsumed is a gauge representing the fraction of error budget burned.
	// Updated externally; drives the alerting SLO burn-rate rule.
	ErrorBudgetConsumed = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Name:      "error_budget_consumed_ratio",
			Help:      "Fraction of the 30-day error budget consumed (0.0 = full budget, 1.0 = depleted).",
		},
	)

	// ── Runtime / Saturation golden signals ──────────────────────────────────────

	// RuntimeGoroutines is a gauge tracking the live goroutine count.
	// A monotonic rise indicates goroutine leaks.
	RuntimeGoroutines = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "runtime",
			Name:      "goroutines",
			Help:      "Number of goroutines currently in existence.",
		},
	)

	// RuntimeHeapBytes tracks in-use heap bytes — the primary memory saturation SLI.
	RuntimeHeapBytes = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "runtime",
			Name:      "heap_inuse_bytes",
			Help:      "Bytes of in-use heap spans reported by runtime.MemStats.HeapInuse.",
		},
	)

	// RuntimeHeapObjects tracks live heap object count — rises during memory leaks.
	RuntimeHeapObjects = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "runtime",
			Name:      "heap_objects",
			Help:      "Number of allocated heap objects.",
		},
	)

	// RuntimeGCPauseMicros records the duration of the most recent GC stop-the-world pause.
	// Buckets cover 1 µs to 100 ms to catch both fast and pathological pauses.
	RuntimeGCPauseMicros = promauto.NewHistogram(
		prometheus.HistogramOpts{
			Namespace: "aether_guard",
			Subsystem: "runtime",
			Name:      "gc_pause_microseconds",
			Help:      "Duration of the last GC stop-the-world pause in microseconds.",
			Buckets:   []float64{1, 10, 50, 100, 500, 1_000, 5_000, 10_000, 50_000, 100_000},
		},
	)

	// ── DB query latency ─────────────────────────────────────────────────────────

	// DBQueryDuration measures real SQLite query latency, partitioned by table and operation.
	DBQueryDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "aether_guard",
			Subsystem: "db",
			Name:      "query_duration_seconds",
			Help:      "SQLite query latency, partitioned by table and operation.",
			Buckets:   []float64{0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5},
		},
		[]string{"table", "operation"},
	)

	// ChaosCPUCoresActive tracks the number of CPU-burning goroutines injected by chaos/cpu.
	ChaosCPUCoresActive = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "chaos",
			Name:      "cpu_cores_active",
			Help:      "Number of goroutines currently burning CPU via the chaos/cpu endpoint.",
		},
	)

	// ── Phase B: RED metrics (missing in-flight gauge) ──────────────────────────

	// HTTPRequestsInFlight tracks concurrent requests being processed.
	HTTPRequestsInFlight = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "http",
			Name:      "requests_in_flight",
			Help:      "Number of HTTP requests currently being processed.",
		},
		[]string{"endpoint"},
	)

	// ── Phase B: Resource metrics ────────────────────────────────────────────────

	// ProcessOpenFDs tracks the number of open file descriptors.
	ProcessOpenFDs = promauto.NewGauge(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "process",
			Name:      "open_fds",
			Help:      "Number of open file descriptors.",
		},
	)

	// NOTE: Database connection metrics migrated to SDK (db_connections_max, db_connections_in_use, db_connections_idle).
	// Updated via ContractMetrics.UpdateDBPoolMetrics() in UpdateDBPoolMetrics().

	// CacheHitRate tracks the cache hit rate as a ratio (0.0 to 1.0).
	CacheHitRate = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "cache",
			Name:      "hit_rate",
			Help:      "Cache hit rate ratio (hits / (hits + misses)) per cache type.",
		},
		[]string{"cache_type"},
	)

	// CacheEvictionsTotal counts cache evictions.
	CacheEvictionsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "aether_guard",
			Subsystem: "cache",
			Name:      "evictions_total",
			Help:      "Total number of cache evictions.",
		},
		[]string{"cache_type", "reason"},
	)

	// ── Phase B: Business metrics ────────────────────────────────────────────────

	// OrdersTotal counts orders by status.
	OrdersTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "aether_guard",
			Subsystem: "business",
			Name:      "orders_total",
			Help:      "Total number of orders processed, partitioned by status.",
		},
		[]string{"status"},
	)

	// PaymentProcessingDuration tracks payment processing latency.
	PaymentProcessingDuration = promauto.NewHistogram(
		prometheus.HistogramOpts{
			Namespace: "aether_guard",
			Subsystem: "business",
			Name:      "payment_processing_duration_seconds",
			Help:      "Payment processing latency distribution.",
			Buckets:   []float64{0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0},
		},
	)

	// InventoryChecksTotal counts inventory check results.
	InventoryChecksTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "aether_guard",
			Subsystem: "business",
			Name:      "inventory_checks_total",
			Help:      "Total inventory checks performed, partitioned by result.",
		},
		[]string{"result"},
	)

	// BackgroundJobsQueueLength tracks the depth of background job queues.
	BackgroundJobsQueueLength = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Namespace: "aether_guard",
			Subsystem: "business",
			Name:      "background_jobs_queue_length",
			Help:      "Number of jobs waiting in background queues.",
		},
		[]string{"queue_name"},
	)

	// ── Phase B: Error tracking ──────────────────────────────────────────────────

	// NOTE: Service Contract metrics (errors_total, circuit_breaker_state, timeout_errors_total,
	// db_connections_*) have been migrated to the SDK in internal/metrics/contract.go.
	// Use ContractMetrics.RecordError(), SetCircuitBreakerState(), etc. instead.
)

// statusRecorder wraps http.ResponseWriter to capture the status code written
// by downstream handlers — necessary for accurate SLI labelling.
type statusRecorder struct {
	http.ResponseWriter
	statusCode int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.statusCode = code
	r.ResponseWriter.WriteHeader(code)
}

// Middleware is an http.Handler decorator that records golden-signal metrics
// for every request that passes through it.
func Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Track in-flight requests (Phase B: RED metrics)
		HTTPRequestsInFlight.WithLabelValues(r.URL.Path).Inc()
		defer HTTPRequestsInFlight.WithLabelValues(r.URL.Path).Dec()

		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, statusCode: http.StatusOK}

		next.ServeHTTP(rec, r)

		duration := time.Since(start).Seconds()
		statusCode := strconv.Itoa(rec.statusCode)

		HTTPRequestsTotal.WithLabelValues(r.Method, r.URL.Path, statusCode).Inc()
		HTTPRequestDuration.WithLabelValues(r.Method, r.URL.Path).Observe(duration)

		// Track errors via Service Contract SDK (Phase B: Error tracking)
		if ContractMetrics != nil {
			if rec.statusCode >= 500 {
				ContractMetrics.RecordError("target-service", "internal", r.URL.Path, statusCode, "HTTPError")
			} else if rec.statusCode >= 400 {
				ContractMetrics.RecordError("target-service", "validation", r.URL.Path, statusCode, "HTTPError")
			}
		}
	})
}

// StartRuntimeCollector launches a background goroutine that samples Go runtime
// statistics every 5 seconds and publishes them to Prometheus.
// Call once from main; pass a channel that is closed on shutdown.
func StartRuntimeCollector(stop <-chan struct{}) {
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				RuntimeGoroutines.Set(float64(runtime.NumGoroutine()))

				var ms runtime.MemStats
				runtime.ReadMemStats(&ms)

				RuntimeHeapBytes.Set(float64(ms.HeapInuse))
				RuntimeHeapObjects.Set(float64(ms.HeapObjects))

				if ms.NumGC > 0 {
					lastPauseNs := ms.PauseNs[(ms.NumGC+255)%256]
					RuntimeGCPauseMicros.Observe(float64(lastPauseNs) / 1_000)
				}

				// Phase B: Process metrics (file descriptors)
				updateProcessMetrics()
			case <-stop:
				return
			}
		}
	}()
}

// UpdateDBPoolMetrics updates database connection pool metrics.
// Call this periodically with stats from sql.DB.Stats().
func UpdateDBPoolMetrics(stats struct {
	OpenConnections int
	InUse           int
	Idle            int
	MaxOpen         int
}) {
	// Service Contract metrics via SDK
	if ContractMetrics != nil {
		ContractMetrics.UpdateDBPoolMetrics("target-service", stats.MaxOpen, stats.InUse, stats.Idle)
	}
}

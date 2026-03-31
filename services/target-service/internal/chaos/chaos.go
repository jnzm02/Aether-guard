// Package chaos implements the canonical failure modes used in Aether-Guard:
//
//  1. Memory Leak  — /chaos/memleak?mb=<N>
//     Allocates N MiB of heap and retains a live reference, preventing GC.
//     Simulates a common production issue: buffer accumulation, cache with no eviction.
//
//  2. Latency Spike — /chaos/latency?ms=<N>
//     Blocks the response goroutine for N milliseconds.
//     Simulates upstream dependency timeouts / slow DB queries.
//
//  3. Logic Error   — /chaos/error?rate=<0.0–1.0>
//     Returns HTTP 500 with a realistic-looking error message at the given rate.
//     Simulates transient application panics, nil-pointer derefs, etc.
//
//  4. Reset         — /chaos/reset
//     Releases leaked memory and resets all chaos state. Useful during demos.
//
//  5. CPU Spike     — /chaos/cpu?cores=<N>&ms=<N>
//     Spins N goroutines executing compute-intensive work for N milliseconds.
//     Simulates runaway CPU consumers, hot loops, or missing rate limits.
//
//  6. Status        — /chaos/status
//     Returns a JSON snapshot of all active chaos injections.
package chaos

import (
	"context"
	"encoding/json"
	"math/rand"
	"net/http"
	"runtime"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/aether-guard/target-service/internal/metrics"
	"go.uber.org/zap"
)

// ──────────────────────────────────────────────────────────────────────────────
// Shared chaos state
// ──────────────────────────────────────────────────────────────────────────────

var (
	// memLeakStore holds live references to leaked byte slices so the GC
	// cannot reclaim them. This is intentional and the entire point.
	memLeakStore [][]byte
	memLeakMu    sync.Mutex

	// totalLeakedBytes tracks the cumulative bytes never freed; exported to
	// Prometheus as a gauge so the alert fires when RSS grows unbounded.
	totalLeakedBytes atomic.Int64
)

// cpuMu protects cpuCancel; never held while doing I/O.
var (
	cpuMu     sync.Mutex
	cpuCancel context.CancelFunc
	cpuActive atomic.Int32 // goroutines currently burning CPU
)

// respondJSON writes v as a JSON body. Errors are intentionally swallowed —
// if the connection is broken mid-chaos that is fine.
func respondJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

// ──────────────────────────────────────────────────────────────────────────────
// 1. Memory Leak
// ──────────────────────────────────────────────────────────────────────────────

// MemLeakHandler allocates mb MiB of memory per call and intentionally keeps
// it alive. Each allocation touches every byte to ensure physical pages are
// committed (not just virtual address space reserved).
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

// ──────────────────────────────────────────────────────────────────────────────
// 2. Latency Spike
// ──────────────────────────────────────────────────────────────────────────────

// LatencyHandler sleeps for ms milliseconds before responding.
// It respects context cancellation so client-side timeouts surface correctly
// in the metrics (the request will appear as a non-200 in the SLI).
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
				"event":      "latency_injected",
				"delay_ms":   ms,
				"actual_ms":  actual.Milliseconds(),
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

// ──────────────────────────────────────────────────────────────────────────────
// 3. Logic Error (HTTP 500)
// ──────────────────────────────────────────────────────────────────────────────

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
			"status":         "no_error_this_time",
			"configured_rate": rate,
		})
	})
}

// ──────────────────────────────────────────────────────────────────────────────
// 4. Reset
// ──────────────────────────────────────────────────────────────────────────────

// ResetHandler releases all leaked memory and zeroes out chaos counters.
// The GC will reclaim memory on the next collection cycle after this call.
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

		logger.Info("✅  chaos/reset: all chaos state cleared",
			zap.Int64("bytes_freed", freed),
		)

		respondJSON(w, http.StatusOK, map[string]any{
			"status":      "reset",
			"freed_bytes": freed,
			"freed_mb":    freed / (1024 * 1024),
		})
	})
}

// ──────────────────────────────────────────────────────────────────────────────
// 5. CPU Spike
// ──────────────────────────────────────────────────────────────────────────────

// CPUSpikeHandler spins `cores` goroutines executing compute-intensive work for
// `ms` milliseconds. Closing the previous spike before starting a new one
// prevents unbounded goroutine accumulation.
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

// ──────────────────────────────────────────────────────────────────────────────
// 6. Status
// ──────────────────────────────────────────────────────────────────────────────

// StatusHandler returns a JSON snapshot of all active chaos injections.
//
//	GET /chaos/status
func StatusHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		leakedBytes := totalLeakedBytes.Load()
		activeCores := cpuActive.Load()

		respondJSON(w, http.StatusOK, map[string]any{
			"memory_leak_active":  leakedBytes > 0,
			"memory_leaked_bytes": leakedBytes,
			"memory_leaked_mb":    leakedBytes / (1024 * 1024),
			"cpu_spike_active":    activeCores > 0,
			"cpu_cores_active":    activeCores,
		})
	})
}

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

// queryInt parses a query parameter as an integer.
// Returns defaultVal if the key is absent, or -1 (sentinel for invalid) if
// the value is present but out of [min, max].
func queryInt(r *http.Request, key string, defaultVal, min, max int) int {
	s := r.URL.Query().Get(key)
	if s == "" {
		return defaultVal
	}
	v, err := strconv.Atoi(s)
	if err != nil || v < min || v > max {
		return -1 // caller should return 400
	}
	return v
}

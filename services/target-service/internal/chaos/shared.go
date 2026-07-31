// Package chaos implements the canonical failure modes used in Aether-Guard.
// This file contains shared state and utilities used by all chaos handlers.
package chaos

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
)

// ──────────────────────────────────────────────────────────────────────────────
// Shared chaos state
// ──────────────────────────────────────────────────────────────────────────────

var (
	// Memory leak state
	memLeakStore     [][]byte
	memLeakMu        sync.Mutex
	totalLeakedBytes atomic.Int64

	// CPU spike state
	cpuMu     sync.Mutex
	cpuCancel context.CancelFunc
	cpuActive atomic.Int32

	// Goroutine leak state (NEW for Pattern 8)
	leakedGoroutines atomic.Int32
)

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

// respondJSON writes v as a JSON body. Errors are intentionally swallowed —
// if the connection is broken mid-chaos that is fine.
func respondJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

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

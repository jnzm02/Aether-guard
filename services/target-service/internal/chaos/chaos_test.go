package chaos

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.uber.org/zap"
)

// newLogger returns a no-op logger so test output stays clean.
func newLogger() *zap.Logger { return zap.NewNop() }

// ─────────────────────────────────────────────────────────────────────────────
// MemLeakHandler
// ─────────────────────────────────────────────────────────────────────────────

func TestMemLeakHandler(t *testing.T) {
	// Reset global state before each test run to avoid cross-test pollution.
	memLeakMu.Lock()
	memLeakStore = nil
	memLeakMu.Unlock()
	totalLeakedBytes.Store(0)

	tests := []struct {
		name       string
		query      string
		wantStatus int
		wantEvent  string
	}{
		{
			name:       "valid 1 MiB allocation",
			query:      "?mb=1",
			wantStatus: http.StatusOK,
			wantEvent:  "leak_injected",
		},
		{
			name:       "default allocation (no param)",
			query:      "",
			wantStatus: http.StatusOK,
			wantEvent:  "leak_injected",
		},
		{
			name:       "mb exceeds max (501)",
			query:      "?mb=501",
			wantStatus: http.StatusBadRequest,
		},
		{
			name:       "non-numeric mb param",
			query:      "?mb=bad",
			wantStatus: http.StatusBadRequest,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/chaos/memleak"+tc.query, nil)
			rec := httptest.NewRecorder()

			MemLeakHandler(newLogger()).ServeHTTP(rec, req)

			if rec.Code != tc.wantStatus {
				t.Errorf("status = %d, want %d", rec.Code, tc.wantStatus)
			}

			if tc.wantEvent != "" {
				var body map[string]any
				if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
					t.Fatalf("failed to decode body: %v", err)
				}
				if got, _ := body["event"].(string); got != tc.wantEvent {
					t.Errorf("event = %q, want %q", got, tc.wantEvent)
				}
			}
		})
	}
}

func TestMemLeakHandler_AccumulatesBytes(t *testing.T) {
	memLeakMu.Lock()
	memLeakStore = nil
	memLeakMu.Unlock()
	totalLeakedBytes.Store(0)

	handler := MemLeakHandler(newLogger())

	for i := 0; i < 3; i++ {
		req := httptest.NewRequest(http.MethodGet, "/chaos/memleak?mb=1", nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("iteration %d: unexpected status %d", i, rec.Code)
		}
	}

	total := totalLeakedBytes.Load()
	want := int64(3 * 1024 * 1024)
	if total != want {
		t.Errorf("totalLeakedBytes = %d, want %d", total, want)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// LatencyHandler
// ─────────────────────────────────────────────────────────────────────────────

func TestLatencyHandler(t *testing.T) {
	tests := []struct {
		name       string
		query      string
		wantStatus int
		wantEvent  string
	}{
		{
			name:       "zero delay (fast path)",
			query:      "?ms=0",
			wantStatus: http.StatusOK,
			wantEvent:  "latency_injected",
		},
		{
			name:       "default delay (no param) — capped at 2000ms but we don't wait",
			query:      "?ms=1",
			wantStatus: http.StatusOK,
			wantEvent:  "latency_injected",
		},
		{
			name:       "ms exceeds max (30001)",
			query:      "?ms=30001",
			wantStatus: http.StatusBadRequest,
		},
		{
			name:       "non-numeric ms param",
			query:      "?ms=abc",
			wantStatus: http.StatusBadRequest,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/chaos/latency"+tc.query, nil)
			rec := httptest.NewRecorder()

			LatencyHandler(newLogger()).ServeHTTP(rec, req)

			if rec.Code != tc.wantStatus {
				t.Errorf("status = %d, want %d", rec.Code, tc.wantStatus)
			}

			if tc.wantEvent != "" {
				var body map[string]any
				if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
					t.Fatalf("body decode error: %v", err)
				}
				if got, _ := body["event"].(string); got != tc.wantEvent {
					t.Errorf("event = %q, want %q", got, tc.wantEvent)
				}
			}
		})
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// ErrorHandler
// ─────────────────────────────────────────────────────────────────────────────

func TestErrorHandler(t *testing.T) {
	tests := []struct {
		name       string
		query      string
		wantStatus int
	}{
		{name: "rate=1.0 always 500", query: "?rate=1.0", wantStatus: http.StatusInternalServerError},
		{name: "rate=0.0 always 200", query: "?rate=0.0", wantStatus: http.StatusOK},
		{name: "invalid rate string", query: "?rate=bad", wantStatus: http.StatusBadRequest},
		{name: "rate > 1.0", query: "?rate=1.5", wantStatus: http.StatusBadRequest},
		{name: "rate < 0.0", query: "?rate=-0.1", wantStatus: http.StatusBadRequest},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/chaos/error"+tc.query, nil)
			rec := httptest.NewRecorder()

			ErrorHandler(newLogger()).ServeHTTP(rec, req)

			if rec.Code != tc.wantStatus {
				t.Errorf("status = %d, want %d", rec.Code, tc.wantStatus)
			}
		})
	}
}

func TestErrorHandler_ResponseBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/chaos/error?rate=1.0", nil)
	rec := httptest.NewRecorder()
	ErrorHandler(newLogger()).ServeHTTP(rec, req)

	var body map[string]any
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatalf("body decode error: %v", err)
	}
	if body["error"] == nil || body["error"] == "" {
		t.Error("expected non-empty error message in body")
	}
	if code, _ := body["code"].(float64); int(code) != 500 {
		t.Errorf("body.code = %v, want 500", body["code"])
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// ResetHandler
// ─────────────────────────────────────────────────────────────────────────────

func TestResetHandler(t *testing.T) {
	// First, inject a 1 MiB leak.
	leakReq := httptest.NewRequest(http.MethodGet, "/chaos/memleak?mb=1", nil)
	leakRec := httptest.NewRecorder()
	MemLeakHandler(newLogger()).ServeHTTP(leakRec, leakReq)
	if leakRec.Code != http.StatusOK {
		t.Fatalf("memleak setup failed: %d", leakRec.Code)
	}

	if totalLeakedBytes.Load() == 0 {
		t.Fatal("expected non-zero leak before reset")
	}

	// Now reset.
	req := httptest.NewRequest(http.MethodPost, "/chaos/reset", nil)
	rec := httptest.NewRecorder()
	ResetHandler(newLogger()).ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("reset status = %d, want 200", rec.Code)
	}

	var body map[string]any
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatalf("body decode error: %v", err)
	}
	if got, _ := body["status"].(string); got != "reset" {
		t.Errorf("body.status = %q, want %q", got, "reset")
	}
	if totalLeakedBytes.Load() != 0 {
		t.Errorf("expected totalLeakedBytes=0 after reset, got %d", totalLeakedBytes.Load())
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// queryInt helper
// ─────────────────────────────────────────────────────────────────────────────

func TestQueryInt(t *testing.T) {
	makeReq := func(query string) *http.Request {
		r := httptest.NewRequest(http.MethodGet, "/"+query, nil)
		return r
	}

	tests := []struct {
		name  string
		query string
		key   string
		def   int
		min   int
		max   int
		want  int
	}{
		{name: "present valid", query: "?mb=5", key: "mb", def: 10, min: 1, max: 500, want: 5},
		{name: "absent uses default", query: "", key: "mb", def: 10, min: 1, max: 500, want: 10},
		{name: "below min → -1", query: "?mb=0", key: "mb", def: 10, min: 1, max: 500, want: -1},
		{name: "above max → -1", query: "?mb=501", key: "mb", def: 10, min: 1, max: 500, want: -1},
		{name: "non-numeric → -1", query: "?mb=x", key: "mb", def: 10, min: 1, max: 500, want: -1},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := queryInt(makeReq(tc.query), tc.key, tc.def, tc.min, tc.max)
			if got != tc.want {
				t.Errorf("queryInt = %d, want %d", got, tc.want)
			}
		})
	}
}

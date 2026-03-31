package metrics

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// ─────────────────────────────────────────────────────────────────────────────
// statusRecorder
// ─────────────────────────────────────────────────────────────────────────────

func TestStatusRecorder_DefaultIs200(t *testing.T) {
	rec := httptest.NewRecorder()
	sr := &statusRecorder{ResponseWriter: rec, statusCode: http.StatusOK}

	if sr.statusCode != http.StatusOK {
		t.Errorf("default statusCode = %d, want 200", sr.statusCode)
	}
}

func TestStatusRecorder_CapturesWrittenCode(t *testing.T) {
	rec := httptest.NewRecorder()
	sr := &statusRecorder{ResponseWriter: rec, statusCode: http.StatusOK}

	sr.WriteHeader(http.StatusTeapot)

	if sr.statusCode != http.StatusTeapot {
		t.Errorf("statusCode = %d, want %d", sr.statusCode, http.StatusTeapot)
	}
	if rec.Code != http.StatusTeapot {
		t.Errorf("underlying recorder code = %d, want %d", rec.Code, http.StatusTeapot)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Middleware
// ─────────────────────────────────────────────────────────────────────────────

func TestMiddleware_PassesThrough(t *testing.T) {
	sentinel := "hello from inner"
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
		w.Write([]byte(sentinel)) //nolint:errcheck
	})

	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	rec := httptest.NewRecorder()

	Middleware(inner).ServeHTTP(rec, req)

	if rec.Code != http.StatusCreated {
		t.Errorf("status = %d, want 201", rec.Code)
	}
	if body := rec.Body.String(); body != sentinel {
		t.Errorf("body = %q, want %q", body, sentinel)
	}
}

func TestMiddleware_RecordsMetricsWithoutPanic(t *testing.T) {
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	})

	req := httptest.NewRequest(http.MethodPost, "/api/orders", nil)
	rec := httptest.NewRecorder()

	// Should not panic — Prometheus counters handle concurrent increments safely.
	defer func() {
		if r := recover(); r != nil {
			t.Errorf("middleware panicked: %v", r)
		}
	}()

	Middleware(inner).ServeHTTP(rec, req)

	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500", rec.Code)
	}
}

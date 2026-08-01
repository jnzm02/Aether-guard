package contract

import (
	"testing"

	"github.com/prometheus/client_golang/prometheus"
)

// TestZeroValueNoOp verifies that a zero-value Metrics struct never panics.
// This guards against the typed-nil-interface bug: methods must check for nil
// receivers/fields and no-op gracefully rather than dereferencing nil pointers.
func TestZeroValueNoOp(t *testing.T) {
	var m Metrics // zero value, no New() call

	// All methods should be safe to call on zero value
	m.SetServiceInfo("x", "1.0", "", "", "L1")
	m.RecordError("x", "internal", "/foo", "500", "Error")
	m.SetCircuitBreakerState("x", "postgres", 2)
	m.RecordTimeoutError("postgres")
	m.UpdateDBPoolMetrics("x", 10, 5, 5)

	// If we got here without panicking, the zero value is safe
	t.Log("Zero value Metrics safely handled all method calls")
}

// TestNewWithRegisterer verifies that New() creates a working Metrics instance.
func TestNewWithRegisterer(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := New(reg, "test-service", "1.0.0")

	if m == nil {
		t.Fatal("New() returned nil")
	}

	// Set service info
	m.SetServiceInfo("test-service", "1.0.0", "test-team", "", "L1")

	// Record some metrics
	m.RecordError("test-service", "dependency", "/api/test", "503", "ConnectionError")
	m.SetCircuitBreakerState("test-service", "postgres", 2)
	m.RecordTimeoutError("postgres")
	m.UpdateDBPoolMetrics("test-service", 15, 3, 12)

	// Verify metrics were registered
	metrics, err := reg.Gather()
	if err != nil {
		t.Fatalf("Failed to gather metrics: %v", err)
	}

	if len(metrics) == 0 {
		t.Fatal("No metrics were registered")
	}

	t.Logf("Successfully registered %d metric families", len(metrics))
}

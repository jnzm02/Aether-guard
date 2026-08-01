// Package contract provides Aether-Guard Service Contract v1.0 compliant metrics for Go services.
//
// This SDK implements the language-agnostic interface defined in Service Contract §3.3,
// enabling autonomous SRE agent observability without coupling to agent internals.
//
// Usage:
//
//	import "github.com/jnzm02/aether-guard/sdk/go"
//	import "github.com/prometheus/client_golang/prometheus"
//
//	m := contract.New(prometheus.DefaultRegisterer, "my-service", "1.0.0")
//	m.SetServiceInfo("my-team", "https://runbook.example.com", "L1")
//	m.RecordError("my-service", "dependency", "/api/users", "503", "ConnectionRefused")
package contract

import (
	"github.com/prometheus/client_golang/prometheus"
)

// Metrics holds all Service Contract v1.0 metric collectors.
// Create via New() with explicit Registerer for testability.
type Metrics struct {
	// Service metadata carrier (always 1)
	serviceInfo *prometheus.GaugeVec

	// Application errors by kind (§3.3 enum: timeout, dependency, internal, validation, saturation)
	errorsTotal *prometheus.CounterVec

	// Circuit breaker state: 0=closed, 1=half_open, 2=open
	circuitBreakerState *prometheus.GaugeVec

	// Timeout errors by dependency (Phase B requirement)
	timeoutErrorsTotal *prometheus.CounterVec

	// Database connection pool metrics (Phase B requirement)
	dbConnectionsMax   *prometheus.GaugeVec
	dbConnectionsInUse *prometheus.GaugeVec
	dbConnectionsIdle  *prometheus.GaugeVec
}

// New creates a Service Contract v1.0 metrics collector.
//
// Parameters:
//   - reg: Prometheus Registerer (use prometheus.DefaultRegisterer or custom for tests)
//   - serviceName: Unique service identifier (e.g., "target-service")
//   - version: Service version (e.g., "1.2.0")
//
// Returns initialized metrics ready for use. Call SetServiceInfo() to populate metadata.
func New(reg prometheus.Registerer, serviceName, version string) *Metrics {
	m := &Metrics{
		serviceInfo: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{
				Namespace: "aether_guard",
				Name:      "service_info",
				Help:      "Service metadata for Aether-Guard agent discovery (always 1).",
			},
			[]string{"service", "version", "contract_version", "owner", "runbook", "compliance_level"},
		),

		errorsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Namespace: "aether_guard",
				Name:      "errors_total",
				Help:      "Total application errors by kind (timeout|dependency|internal|validation|saturation).",
			},
			[]string{"service", "kind", "endpoint", "status_code", "error_type"},
		),

		circuitBreakerState: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{
				Namespace: "aether_guard",
				Name:      "circuit_breaker_state",
				Help:      "Circuit breaker state: 0=closed, 1=half_open, 2=open.",
			},
			[]string{"service", "dependency"},
		),

		timeoutErrorsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Namespace: "aether_guard",
				Name:      "timeout_errors_total",
				Help:      "Total timeout errors by dependency.",
			},
			[]string{"dependency"},
		),

		dbConnectionsMax: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{
				Namespace: "aether_guard",
				Name:      "db_connections_max",
				Help:      "Maximum number of database connections allowed.",
			},
			[]string{"service"},
		),

		dbConnectionsInUse: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{
				Namespace: "aether_guard",
				Name:      "db_connections_in_use",
				Help:      "Number of database connections currently in use.",
			},
			[]string{"service"},
		),

		dbConnectionsIdle: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{
				Namespace: "aether_guard",
				Name:      "db_connections_idle",
				Help:      "Number of idle database connections in the pool.",
			},
			[]string{"service"},
		),
	}

	// Register all metrics with the provided Registerer
	reg.MustRegister(m.serviceInfo)
	reg.MustRegister(m.errorsTotal)
	reg.MustRegister(m.circuitBreakerState)
	reg.MustRegister(m.timeoutErrorsTotal)
	reg.MustRegister(m.dbConnectionsMax)
	reg.MustRegister(m.dbConnectionsInUse)
	reg.MustRegister(m.dbConnectionsIdle)

	return m
}

// SetServiceInfo sets the service metadata gauge to 1 (required by Service Contract §3.3).
//
// Parameters:
//   - owner: Team or owner identifier (e.g., "platform-team")
//   - runbook: URL to incident runbook (empty string if none)
//   - complianceLevel: "L1" (observe+declare) or "L2" (observe+declare+act)
//
// Call this once at service startup.
func (m *Metrics) SetServiceInfo(serviceName, version, owner, runbook, complianceLevel string) {
	if m == nil || m.serviceInfo == nil {
		return
	}
	m.serviceInfo.WithLabelValues(serviceName, version, "1.0", owner, runbook, complianceLevel).Set(1)
}

// RecordError increments the errors_total counter.
//
// Parameters:
//   - service: Service name (e.g., "target-service")
//   - kind: Error classification per §3.3 enum: "timeout", "dependency", "internal", "validation", "saturation"
//   - endpoint: HTTP endpoint path (e.g., "/api/users")
//   - statusCode: HTTP status code as string (e.g., "500", "503")
//   - errorType: Exception/error type name (e.g., "ConnectionRefused", "TimeoutError")
func (m *Metrics) RecordError(service, kind, endpoint, statusCode, errorType string) {
	if m == nil || m.errorsTotal == nil {
		return
	}
	m.errorsTotal.WithLabelValues(service, kind, endpoint, statusCode, errorType).Inc()
}

// SetCircuitBreakerState sets the circuit breaker state gauge.
//
// Parameters:
//   - service: Service name (e.g., "target-service")
//   - dependency: Dependency name (e.g., "postgres", "redis")
//   - state: Breaker state: 0=closed (healthy), 1=half_open (testing), 2=open (failing)
func (m *Metrics) SetCircuitBreakerState(service, dependency string, state int) {
	if m == nil || m.circuitBreakerState == nil {
		return
	}
	m.circuitBreakerState.WithLabelValues(service, dependency).Set(float64(state))
}

// RecordTimeoutError increments the timeout_errors_total counter.
//
// Parameters:
//   - dependency: Dependency that timed out (e.g., "postgres", "redis")
func (m *Metrics) RecordTimeoutError(dependency string) {
	if m == nil || m.timeoutErrorsTotal == nil {
		return
	}
	m.timeoutErrorsTotal.WithLabelValues(dependency).Inc()
}

// UpdateDBPoolMetrics sets database connection pool gauges.
//
// Parameters:
//   - service: Service name (e.g., "target-service")
//   - maxConns: Maximum connections allowed (from explicit pool config)
//   - inUse: Connections currently in use
//   - idle: Connections idle in pool
//
// Call this periodically (e.g., every 5 seconds) from a background collector.
func (m *Metrics) UpdateDBPoolMetrics(service string, maxConns, inUse, idle int) {
	if m == nil {
		return
	}
	if m.dbConnectionsMax != nil {
		m.dbConnectionsMax.WithLabelValues(service).Set(float64(maxConns))
	}
	if m.dbConnectionsInUse != nil {
		m.dbConnectionsInUse.WithLabelValues(service).Set(float64(inUse))
	}
	if m.dbConnectionsIdle != nil {
		m.dbConnectionsIdle.WithLabelValues(service).Set(float64(idle))
	}
}

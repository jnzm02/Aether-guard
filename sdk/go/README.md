# Aether-Guard Service Contract SDK for Go

**Language-agnostic observability interface for autonomous SRE agents.**

This SDK implements the [Aether-Guard Service Contract v1.0](../../docs/SERVICE_CONTRACT_v1.md)
in Go, enabling services to expose standardized metrics for autonomous incident detection,
root-cause analysis, and remediation.

## What is the Service Contract?

The Service Contract is a **language-agnostic interface** that defines:
1. **Required metrics** (errors, circuit breaker state, resource saturation)
2. **Standard label schemas** (service, kind, dependency, endpoint)
3. **Manifest endpoint** (`/aetherguard/v1/manifest`) for service discovery
4. **Compliance levels** (L1: observe+declare, L2: observe+declare+act)

By adopting this contract, your service becomes observable to Aether-Guard's AI agent
without coupling to Python-specific agent internals.

## Installation

```bash
go get github.com/jnzm02/aether-guard/sdk/go
```

## Usage

```go
package main

import (
    "github.com/jnzm02/aether-guard/sdk/go"
    "github.com/prometheus/client_golang/prometheus"
    "github.com/prometheus/client_golang/prometheus/promhttp"
    "net/http"
)

func main() {
    // 1. Create metrics collector (pass custom Registerer for tests)
    metrics := contract.New(prometheus.DefaultRegisterer, "my-service", "1.0.0")

    // 2. Set service metadata (required by contract §3.3)
    metrics.SetServiceInfo("my-service", "1.0.0", "platform-team", "https://runbook.example.com", "L1")

    // 3. Record errors with standardized labels
    metrics.RecordError("my-service", "dependency", "/api/users", "503", "ConnectionRefused")

    // 4. Track circuit breaker state (0=closed, 1=half_open, 2=open)
    metrics.SetCircuitBreakerState("my-service", "postgres", 2)

    // 5. Record timeout errors
    metrics.RecordTimeoutError("postgres")

    // 6. Update DB pool metrics (call periodically, e.g., every 5s)
    metrics.UpdateDBPoolMetrics("my-service", 15, 3, 12)

    // 7. Expose /metrics endpoint
    http.Handle("/metrics", promhttp.Handler())
    http.ListenAndServe(":8080", nil)
}
```

## API Reference

### Constructor

```go
func New(reg prometheus.Registerer, serviceName, version string) *Metrics
```

Creates a new metrics collector with explicit Registerer (use `prometheus.DefaultRegisterer`
or custom registries for testing).

### Methods

#### `SetServiceInfo(serviceName, version, owner, runbook, complianceLevel string)`
Sets service metadata gauge to 1 (required by contract §3.3). Call once at startup.

#### `RecordError(service, kind, endpoint, statusCode, errorType string)`
Increments errors_total counter. `kind` must be one of:
- `"timeout"` - Request/operation timeout
- `"dependency"` - External service failure
- `"internal"` - Server-side error
- `"validation"` - Input validation failure
- `"saturation"` - Resource exhaustion

#### `SetCircuitBreakerState(service, dependency string, state int)`
Sets circuit breaker state:
- `0` = closed (healthy)
- `1` = half_open (testing recovery)
- `2` = open (failing, requests blocked)

#### `RecordTimeoutError(dependency string)`
Increments timeout_errors_total counter for a dependency.

#### `UpdateDBPoolMetrics(service string, maxConns, inUse, idle int)`
Updates database connection pool gauges. Call periodically (e.g., every 5 seconds)
from a background goroutine.

## Design Principles

1. **No init() side effects** - Explicit Registerer parameter for testability
2. **No promauto** - All metrics registered via provided Registerer
3. **Zero coupling** - No dependencies on agent or service-specific code
4. **Standard labels** - Matches Service Contract §3.3 schema exactly

## Compliance Levels

- **L1 (Observe + Declare)**: Metrics + manifest endpoint. Agent observes but doesn't act.
- **L2 (Observe + Declare + Act)**: L1 + remediation action handlers (future).

This SDK supports L1 compliance out of the box.

## License

MIT

// Package metrics - Service Contract v1.0 integration via SDK.
//
// This file wraps the contract SDK to provide Service Contract compliant metrics
// while maintaining backward compatibility with existing code.
package metrics

import (
	contract "github.com/jnzm02/aether-guard/sdk/go"
)

// ContractMetrics holds the SDK-provided Service Contract metrics.
// Initialized via InitContract() in main.go.
var ContractMetrics *contract.Metrics

// InitContract initializes the Service Contract metrics via SDK.
// Must be called once at service startup before any metrics are recorded.
//
// Parameters:
//   - m: SDK metrics instance created via contract.New()
func InitContract(m *contract.Metrics) {
	ContractMetrics = m
}

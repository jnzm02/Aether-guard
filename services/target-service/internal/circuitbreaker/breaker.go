// Package circuitbreaker implements a simple circuit breaker pattern
// with Prometheus metrics for Phase B error tracking.
package circuitbreaker

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/aether-guard/target-service/internal/metrics"
)

// State represents the circuit breaker state.
type State int

const (
	StateClosed   State = 0 // Normal operation
	StateHalfOpen State = 1 // Testing if service recovered
	StateOpen     State = 2 // Failing fast
)

// CircuitBreaker implements the circuit breaker pattern.
type CircuitBreaker struct {
	name           string
	mu             sync.RWMutex
	state          State
	failures       int
	successes      int
	lastFailTime   time.Time
	failureThreshold int
	resetTimeout   time.Duration
}

// New creates a new circuit breaker.
func New(name string, failureThreshold int, resetTimeout time.Duration) *CircuitBreaker {
	cb := &CircuitBreaker{
		name:             name,
		state:            StateClosed,
		failureThreshold: failureThreshold,
		resetTimeout:     resetTimeout,
	}
	cb.updateMetric()
	return cb
}

// Call executes the given function with circuit breaker protection.
func (cb *CircuitBreaker) Call(ctx context.Context, fn func(context.Context) error) error {
	if err := cb.beforeCall(); err != nil {
		return err
	}

	err := fn(ctx)

	cb.afterCall(err)
	return err
}

func (cb *CircuitBreaker) beforeCall() error {
	cb.mu.RLock()
	state := cb.state
	cb.mu.RUnlock()

	if state == StateOpen {
		cb.mu.Lock()
		defer cb.mu.Unlock()

		// Check if we should transition to half-open
		if time.Since(cb.lastFailTime) > cb.resetTimeout {
			cb.state = StateHalfOpen
			cb.successes = 0
			cb.updateMetric()
		} else {
			return fmt.Errorf("circuit breaker %s is open", cb.name)
		}
	}

	return nil
}

func (cb *CircuitBreaker) afterCall(err error) {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	if err != nil {
		cb.failures++
		cb.lastFailTime = time.Now()

		if cb.state == StateHalfOpen {
			// Failure in half-open -> back to open
			cb.state = StateOpen
			cb.failures = 0
			cb.updateMetric()
		} else if cb.failures >= cb.failureThreshold {
			// Too many failures -> open
			cb.state = StateOpen
			cb.failures = 0
			cb.updateMetric()
		}
	} else {
		// Success
		cb.failures = 0

		if cb.state == StateHalfOpen {
			cb.successes++
			if cb.successes >= 2 {
				// Recovered -> close
				cb.state = StateClosed
				cb.successes = 0
				cb.updateMetric()
			}
		}
	}
}

func (cb *CircuitBreaker) updateMetric() {
	// Service Contract SDK with correct labels: {service, dependency}
	if metrics.ContractMetrics != nil {
		metrics.ContractMetrics.SetCircuitBreakerState("target-service", cb.name, int(cb.state))
	}
}

// GetState returns the current state.
func (cb *CircuitBreaker) GetState() State {
	cb.mu.RLock()
	defer cb.mu.RUnlock()
	return cb.state
}

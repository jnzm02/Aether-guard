// Package jobs simulates a background job queue for Phase B business metrics.
package jobs

import (
	"math/rand"
	"time"

	"github.com/aether-guard/target-service/internal/metrics"
)

// Manager simulates a background job processing system.
type Manager struct {
	queueName   string
	queueLength int
	stopChan    chan struct{}
}

// NewManager creates a new job queue manager.
func NewManager(queueName string) *Manager {
	return &Manager{
		queueName:   queueName,
		queueLength: 0,
		stopChan:    make(chan struct{}),
	}
}

// Start begins simulating job queue activity.
// Jobs are randomly added and processed to create realistic queue depth fluctuations.
func (m *Manager) Start() {
	go func() {
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()

		for {
			select {
			case <-ticker.C:
				// Simulate random job additions (0-5 jobs)
				added := rand.Intn(6)
				m.queueLength += added

				// Simulate random job processing (0-3 jobs)
				processed := rand.Intn(4)
				if processed > m.queueLength {
					processed = m.queueLength
				}
				m.queueLength -= processed

				// Ensure queue doesn't go negative
				if m.queueLength < 0 {
					m.queueLength = 0
				}

				// Update metric
				metrics.BackgroundJobsQueueLength.WithLabelValues(m.queueName).Set(float64(m.queueLength))

			case <-m.stopChan:
				return
			}
		}
	}()
}

// Stop halts the job queue simulation.
func (m *Manager) Stop() {
	close(m.stopChan)
}

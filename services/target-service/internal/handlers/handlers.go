// Package handlers provides normal production-like API endpoints used to
// generate baseline traffic so we have meaningful SLI baselines to compare
// against when chaos is injected.
package handlers

import (
	"encoding/json"
	"math/rand"
	"net/http"
	"time"

	"go.uber.org/zap"
)

// respondJSON is a helper that serialises v to JSON and sets Content-Type.
func respondJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

// UsersHandler simulates a read-heavy user-lookup endpoint with realistic
// p50 ~20ms / p99 ~80ms latency to establish a healthy SLI baseline.
func UsersHandler(logger *zap.Logger) http.Handler {
	type User struct {
		ID    int    `json:"id"`
		Name  string `json:"name"`
		Email string `json:"email"`
	}

	users := []User{
		{ID: 1, Name: "Alice Zhao", Email: "alice@corp.example.com"},
		{ID: 2, Name: "Bob Patel", Email: "bob@corp.example.com"},
		{ID: 3, Name: "Cara Müller", Email: "cara@corp.example.com"},
	}

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate realistic DB + serialisation latency (5–50ms).
		time.Sleep(time.Duration(5+rand.Intn(45)) * time.Millisecond)

		respondJSON(w, map[string]any{"users": users, "count": len(users)})

		logger.Debug("GET /api/users served",
			zap.String("remote_addr", r.RemoteAddr),
		)
	})
}

// OrdersHandler simulates a slightly heavier join-query endpoint (10–60ms).
func OrdersHandler(logger *zap.Logger) http.Handler {
	type Order struct {
		ID     int     `json:"id"`
		UserID int     `json:"user_id"`
		Total  float64 `json:"total"`
		Status string  `json:"status"`
	}

	orders := []Order{
		{ID: 101, UserID: 1, Total: 99.99, Status: "shipped"},
		{ID: 102, UserID: 2, Total: 149.50, Status: "processing"},
		{ID: 103, UserID: 3, Total: 19.00, Status: "delivered"},
	}

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(time.Duration(10+rand.Intn(50)) * time.Millisecond)

		respondJSON(w, map[string]any{"orders": orders, "count": len(orders)})
	})
}

// HealthHandler is the Kubernetes liveness probe equivalent.
// Returns 200 as long as the process is alive and the event loop is not blocked.
func HealthHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		respondJSON(w, map[string]string{
			"status":  "ok",
			"service": "aether-guard/target-service",
			"version": "1.0.0",
		})
	})
}

// ReadyHandler is the Kubernetes readiness probe equivalent.
// In a real service this would check DB connectivity, cache warm-up, etc.
func ReadyHandler(logger *zap.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		respondJSON(w, map[string]string{"status": "ready"})
	})
}

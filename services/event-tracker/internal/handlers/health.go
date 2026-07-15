package handlers

import (
	"encoding/json"
	"net/http"

	"github.com/jnzm02/aether-guard/event-tracker/internal/github"
)

// HealthHandler returns 200 OK if cache exists (even if stale), 503 otherwise.
type HealthHandler struct {
	cache *github.Cache
}

// NewHealthHandler creates a health check handler.
func NewHealthHandler(cache *github.Cache) *HealthHandler {
	return &HealthHandler{cache: cache}
}

func (h *HealthHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	events, ageSeconds := h.cache.Get()

	status := "healthy"
	code := http.StatusOK

	// Service unavailable if cache not yet populated
	if len(events) == 0 {
		status = "unavailable - cache not yet populated"
		code = http.StatusServiceUnavailable
	}

	// Warning if cache is stale (but still return 200 - serving stale data is acceptable)
	if h.cache.IsStale() && len(events) > 0 {
		status = "degraded - serving stale cache"
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":           status,
		"cached_events":    len(events),
		"cache_age_seconds": ageSeconds,
	})
}

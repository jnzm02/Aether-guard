package handlers

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/jnzm02/aether-guard/event-tracker/internal/github"
	"github.com/jnzm02/aether-guard/event-tracker/internal/metrics"
)

// EventsHandler serves cached GitHub events as JSON.
// Returns stale data with X-Data-Staleness header if cache is old.
type EventsHandler struct {
	cache *github.Cache
}

// NewEventsHandler creates a JSON events handler.
func NewEventsHandler(cache *github.Cache) *EventsHandler {
	return &EventsHandler{cache: cache}
}

func (h *EventsHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	events, ageSeconds := h.cache.Get()

	// Update cache metrics
	metrics.UpdateCacheMetrics(ageSeconds, len(events))

	// Add staleness header if cache is old
	if ageSeconds > 0 {
		w.Header().Set("X-Data-Staleness", strconv.Itoa(ageSeconds)+"s")
	}

	// Return empty array if cache not yet populated
	if len(events) == 0 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode([]github.Event{})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(events)
}

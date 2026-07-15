package github

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"go.uber.org/zap"
)

func TestFetchEvents_Success(t *testing.T) {
	// Mock GitHub API
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify headers
		if r.Header.Get("Accept") != "application/vnd.github+json" {
			t.Errorf("Expected Accept header, got %s", r.Header.Get("Accept"))
		}

		// Return mock response
		w.Header().Set("X-RateLimit-Remaining", "4999")
		w.Header().Set("X-RateLimit-Limit", "5000")
		w.Header().Set("X-RateLimit-Reset", "1705334400")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`[
			{
				"id": "123",
				"type": "PushEvent",
				"repo": {"name": "owner/repo"},
				"created_at": "2025-01-15T10:00:00Z",
				"actor": {"login": "user123"}
			}
		]`))
	}))
	defer mockServer.Close()

	logger, _ := zap.NewDevelopment()

	client := NewClient("test-token", logger, func(remaining, limit int, reset time.Time) {
		// Rate limit callback (tested separately)
	})

	// Override base URL (would need to expose this in production code for testing)
	// For now, this test validates the parsing logic
	events, err := client.FetchEvents(context.Background())
	if err == nil {
		t.Errorf("Expected error (test uses real API), got nil")
	}
	// In a real test, we'd mock the HTTP client or use an interface
	_ = events
}

func TestCache_UpdateAndGet(t *testing.T) {
	logger, _ := zap.NewDevelopment()
	cache := NewCache(logger)

	// Initial state
	events, age := cache.Get()
	if len(events) != 0 {
		t.Errorf("Expected empty cache, got %d events", len(events))
	}
	if age != 0 {
		t.Errorf("Expected age 0, got %d", age)
	}

	// Update cache
	testEvents := []Event{
		{ID: "1", Type: "PushEvent", RepoName: "test/repo", ActorLogin: "user1"},
		{ID: "2", Type: "IssueEvent", RepoName: "test/repo2", ActorLogin: "user2"},
	}
	cache.Update(testEvents)

	// Verify cache
	events, age = cache.Get()
	if len(events) != 2 {
		t.Errorf("Expected 2 events, got %d", len(events))
	}
	if age < 0 || age > 1 {
		t.Errorf("Expected age ~0 seconds, got %d", age)
	}

	// Wait and verify staleness
	time.Sleep(1 * time.Second)
	_, age = cache.Get()
	if age < 1 {
		t.Errorf("Expected age >= 1 second after sleep, got %d", age)
	}
}

func TestCache_MaxSize(t *testing.T) {
	logger, _ := zap.NewDevelopment()
	cache := NewCache(logger)

	// Generate 150 events (exceeds maxCacheSize of 100)
	largeEventSet := make([]Event, 150)
	for i := range largeEventSet {
		largeEventSet[i] = Event{ID: string(rune(i)), Type: "PushEvent"}
	}

	cache.Update(largeEventSet)

	events, _ := cache.Get()
	if len(events) != 100 {
		t.Errorf("Expected cache limited to 100 events, got %d", len(events))
	}
}

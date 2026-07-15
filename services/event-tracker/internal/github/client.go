package github

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/jnzm02/aether-guard/event-tracker/internal/metrics"
	"go.uber.org/zap"
)

// Event represents a GitHub public event (minimal fields).
type Event struct {
	ID        string    `json:"id"`
	Type      string    `json:"type"`
	RepoName  string    `json:"-"` // Populated from nested Repo.Name
	CreatedAt time.Time `json:"created_at"`
	ActorLogin string   `json:"-"` // Populated from nested Actor.Login
}

// rawEvent is the GitHub API response structure (nested fields).
type rawEvent struct {
	ID        string    `json:"id"`
	Type      string    `json:"type"`
	Repo      repo      `json:"repo"`
	CreatedAt time.Time `json:"created_at"`
	Actor     actor     `json:"actor"`
}

type repo struct {
	Name string `json:"name"`
}

type actor struct {
	Login string `json:"login"`
}

// Client polls the GitHub Events API and tracks rate limits.
type Client struct {
	httpClient    *http.Client
	token         string
	logger        *zap.Logger
	rateLimitFunc func(remaining, limit int, reset time.Time) // For metrics callback
}

// NewClient creates a GitHub API client.
func NewClient(token string, logger *zap.Logger, rateLimitFunc func(int, int, time.Time)) *Client {
	return &Client{
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
		token:         token,
		logger:        logger,
		rateLimitFunc: rateLimitFunc,
	}
}

// FetchEvents retrieves recent public events from GitHub API.
// Returns events, error. On error, caller should serve stale cache.
func (c *Client) FetchEvents(ctx context.Context) ([]Event, error) {
	startTime := time.Now()

	req, err := http.NewRequestWithContext(ctx, "GET", "https://api.github.com/events", nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	// No scopes needed for public events - token only raises rate limit
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		duration := time.Since(startTime)
		metrics.RecordGitHubAPICall(duration, 0, err) // 0 = network error, no HTTP response
		c.logger.Error("GitHub API call failed",
			zap.Error(err),
			zap.Int("latency_ms", int(duration.Milliseconds())),
			zap.String("error_type", "network"),
		)
		return nil, fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()

	duration := time.Since(startTime)

	// Extract rate limit headers
	c.parseRateLimitHeaders(resp.Header)

	// Handle non-200 responses
	if resp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		metrics.RecordGitHubAPICall(duration, resp.StatusCode, fmt.Errorf("HTTP %d", resp.StatusCode))
		c.logger.Error("GitHub API returned non-200 status",
			zap.Int("status_code", resp.StatusCode),
			zap.Int("latency_ms", int(duration.Milliseconds())),
			zap.String("response_body", string(bodyBytes)),
		)
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(bodyBytes))
	}

	// Parse JSON response
	var rawEvents []rawEvent
	if err := json.NewDecoder(resp.Body).Decode(&rawEvents); err != nil {
		metrics.RecordGitHubAPICall(duration, resp.StatusCode, err)
		c.logger.Error("GitHub API response parse failed",
			zap.Error(err),
			zap.Int("latency_ms", int(duration.Milliseconds())),
		)
		return nil, fmt.Errorf("JSON decode: %w", err)
	}

	// Flatten nested fields
	events := make([]Event, len(rawEvents))
	for i, raw := range rawEvents {
		events[i] = Event{
			ID:         raw.ID,
			Type:       raw.Type,
			RepoName:   raw.Repo.Name,
			CreatedAt:  raw.CreatedAt,
			ActorLogin: raw.Actor.Login,
		}
	}

	metrics.RecordGitHubAPICall(duration, resp.StatusCode, nil) // Success

	c.logger.Info("GitHub API poll succeeded",
		zap.Int("events_fetched", len(events)),
		zap.Int("latency_ms", int(duration.Milliseconds())),
	)

	return events, nil
}

// parseRateLimitHeaders extracts X-RateLimit-* headers and invokes callback.
func (c *Client) parseRateLimitHeaders(headers http.Header) {
	if c.rateLimitFunc == nil {
		return
	}

	remaining, _ := strconv.Atoi(headers.Get("X-RateLimit-Remaining"))
	limit, _ := strconv.Atoi(headers.Get("X-RateLimit-Limit"))
	resetUnix, _ := strconv.ParseInt(headers.Get("X-RateLimit-Reset"), 10, 64)

	var reset time.Time
	if resetUnix > 0 {
		reset = time.Unix(resetUnix, 0)
	}

	c.rateLimitFunc(remaining, limit, reset)

	// Log warning if rate limit is low
	if remaining > 0 && remaining < 100 {
		c.logger.Warn("GitHub API rate limit low",
			zap.Int("rate_limit_remaining", remaining),
			zap.Time("rate_limit_reset", reset),
		)
	}
}

package handlers

import (
	"fmt"
	"html"
	"net/http"

	"github.com/jnzm02/aether-guard/event-tracker/internal/github"
)

// IndexHandler serves a minimal HTML page listing recent events.
type IndexHandler struct {
	cache *github.Cache
}

// NewIndexHandler creates an HTML index handler.
func NewIndexHandler(cache *github.Cache) *IndexHandler {
	return &IndexHandler{cache: cache}
}

func (h *IndexHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	events, ageSeconds := h.cache.Get()

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusOK)

	fmt.Fprint(w, `<!DOCTYPE html>
<html>
<head>
    <title>Event Tracker - GitHub Public Events</title>
    <style>
        body { font-family: monospace; margin: 20px; background: #f5f5f5; }
        h1 { color: #333; }
        .warning { color: #ff6600; font-weight: bold; }
        table { border-collapse: collapse; width: 100%; background: white; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #333; color: white; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        .footer { margin-top: 20px; color: #666; font-size: 0.9em; }
    </style>
</head>
<body>
    <h1>GitHub Public Events Feed</h1>
`)

	// Staleness warning
	if ageSeconds > 300 {
		fmt.Fprintf(w, `<p class="warning">⚠️ Warning: Data is stale (%d seconds old). GitHub API may be unavailable.</p>`, ageSeconds)
	} else if ageSeconds > 0 {
		fmt.Fprintf(w, `<p>Cache age: %d seconds</p>`, ageSeconds)
	}

	// Event table
	if len(events) == 0 {
		fmt.Fprint(w, `<p>No events cached yet. Waiting for initial GitHub API fetch...</p>`)
	} else {
		fmt.Fprintf(w, `<p>Showing %d recent events:</p>`, len(events))
		fmt.Fprint(w, `<table>
    <tr>
        <th>Type</th>
        <th>Repository</th>
        <th>Actor</th>
        <th>Timestamp</th>
    </tr>
`)
		for _, event := range events {
			fmt.Fprintf(w, `    <tr>
        <td>%s</td>
        <td>%s</td>
        <td>%s</td>
        <td>%s</td>
    </tr>
`,
				html.EscapeString(event.Type),
				html.EscapeString(event.RepoName),
				html.EscapeString(event.ActorLogin),
				event.CreatedAt.Format("2006-01-02 15:04:05 UTC"),
			)
		}
		fmt.Fprint(w, `</table>`)
	}

	fmt.Fprint(w, `
    <div class="footer">
        <p><strong>API Endpoints:</strong></p>
        <ul>
            <li><code>GET /api/events</code> - JSON feed</li>
            <li><code>GET /health</code> - Health check</li>
            <li><code>GET /metrics</code> - Prometheus metrics</li>
        </ul>
        <p>Part of Aether-Guard Priority 9: Real-Traffic Validation Service</p>
    </div>
</body>
</html>
`)
}

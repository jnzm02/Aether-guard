// Package handlers provides normal production-like API endpoints used to
// generate baseline traffic so we have meaningful SLI baselines to compare
// against when chaos is injected.
package handlers

import (
"database/sql"
"encoding/json"
"net/http"
"time"

"github.com/aether-guard/target-service/internal/metrics"
"go.uber.org/zap"
)

// respondJSON is a helper that serialises v to JSON and sets Content-Type.
func respondJSON(w http.ResponseWriter, v any) {
w.Header().Set("Content-Type", "application/json")
json.NewEncoder(w).Encode(v) //nolint:errcheck
}

// UsersHandler queries SQLite for all users and returns them as JSON.
// Real query latency is observed via DBQueryDuration so Prometheus reflects
// actual I/O instead of synthetic time.Sleep.
func UsersHandler(logger *zap.Logger, db *sql.DB) http.Handler {
type User struct {
ID    int    `json:"id"`
Name  string `json:"name"`
Email string `json:"email"`
}

return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
start := time.Now()

rows, err := db.QueryContext(r.Context(),
"SELECT id, name, email FROM users ORDER BY id")
if err != nil {
logger.Error("users query failed", zap.Error(err))
http.Error(w, "database error", http.StatusInternalServerError)
return
}
defer rows.Close()

var users []User
for rows.Next() {
var u User
if err := rows.Scan(&u.ID, &u.Name, &u.Email); err != nil {
logger.Warn("row scan error", zap.Error(err))
continue
}
users = append(users, u)
}
if err := rows.Err(); err != nil {
logger.Error("rows iteration error", zap.Error(err))
}

metrics.DBQueryDuration.WithLabelValues("users", "select_all").
Observe(time.Since(start).Seconds())

respondJSON(w, map[string]any{"users": users, "count": len(users)})

logger.Debug("GET /api/users served",
zap.Int("count", len(users)),
zap.String("remote_addr", r.RemoteAddr),
)
})
}

// OrdersHandler queries SQLite for all orders (with user name via JOIN) and
// returns them as JSON. Uses a JOIN so latency is realistic.
func OrdersHandler(logger *zap.Logger, db *sql.DB) http.Handler {
type Order struct {
ID       int     `json:"id"`
UserID   int     `json:"user_id"`
UserName string  `json:"user_name"`
Product  string  `json:"product"`
Total    float64 `json:"total"`
Status   string  `json:"status"`
}

return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
start := time.Now()

rows, err := db.QueryContext(r.Context(), `
SELECT o.id, o.user_id, u.name, o.product, o.total, o.status
FROM orders o
JOIN users u ON u.id = o.user_id
ORDER BY o.id`)
if err != nil {
logger.Error("orders query failed", zap.Error(err))
http.Error(w, "database error", http.StatusInternalServerError)
return
}
defer rows.Close()

var orders []Order
for rows.Next() {
var o Order
if err := rows.Scan(&o.ID, &o.UserID, &o.UserName, &o.Product, &o.Total, &o.Status); err != nil {
logger.Warn("row scan error", zap.Error(err))
continue
}
orders = append(orders, o)
}
if err := rows.Err(); err != nil {
logger.Error("rows iteration error", zap.Error(err))
}

metrics.DBQueryDuration.WithLabelValues("orders", "select_join").
Observe(time.Since(start).Seconds())

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

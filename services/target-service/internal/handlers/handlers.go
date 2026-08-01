// Package handlers provides normal production-like API endpoints used to
// generate baseline traffic so we have meaningful SLI baselines to compare
// against when chaos is injected.
package handlers

import (
"context"
"database/sql"
"encoding/json"
"math/rand"
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
// Phase B: Emits business metrics (orders_total, inventory_checks, payment_processing).
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

// Phase B: Track orders by status
metrics.OrdersTotal.WithLabelValues(o.Status).Inc()

// Phase B: Simulate inventory check (80% success rate)
inventoryCheckStart := time.Now()
time.Sleep(time.Duration(rand.Intn(10)) * time.Millisecond)
if rand.Float64() < 0.8 {
metrics.InventoryChecksTotal.WithLabelValues("success").Inc()
} else {
metrics.InventoryChecksTotal.WithLabelValues("failure").Inc()
}

// Phase B: Simulate payment processing for non-delivered orders
if o.Status == "processing" || o.Status == "pending" {
paymentStart := time.Now()
time.Sleep(time.Duration(50+rand.Intn(200)) * time.Millisecond)
metrics.PaymentProcessingDuration.Observe(time.Since(paymentStart).Seconds())
}

logger.Debug("order processed",
zap.Int("order_id", o.ID),
zap.String("status", o.Status),
zap.Float64("total", o.Total),
zap.Duration("inventory_check_duration", time.Since(inventoryCheckStart)),
)
}
if err := rows.Err(); err != nil {
logger.Error("rows iteration error", zap.Error(err))
}

metrics.DBQueryDuration.WithLabelValues("orders", "select_join").
Observe(time.Since(start).Seconds())

respondJSON(w, map[string]any{"orders": orders, "count": len(orders)})
})
}

// DependencyPinger is an interface for health-checkable dependencies.
type DependencyPinger interface {
Ping(ctx context.Context) error
}

// HealthHandler checks service health and optionally pings dependencies.
// PRESERVED: Pattern 6 (Dependency Failure) requires this to log connection errors.
func HealthHandler(pg, rdb DependencyPinger, logger *zap.Logger) http.Handler {
return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
defer cancel()

status := "healthy"
deps := map[string]string{}

if pg != nil {
if err := pg.Ping(ctx); err != nil {
status = "unhealthy"
deps["postgres"] = "down"
} else {
deps["postgres"] = "up"
}
}

if rdb != nil {
if err := rdb.Ping(ctx); err != nil {
status = "unhealthy"
deps["redis"] = "down"
} else {
deps["redis"] = "up"
}
}

code := http.StatusOK
if status == "unhealthy" {
code = http.StatusServiceUnavailable
}

w.Header().Set("Content-Type", "application/json")
w.WriteHeader(code)
json.NewEncoder(w).Encode(map[string]any{
"service":      "aether-guard/target-service",
"status":       status,
"version":      "1.2.0",
"dependencies": deps,
})
})
}

// ReadyHandler is identical to HealthHandler for now.
func ReadyHandler(pg, rdb DependencyPinger, logger *zap.Logger) http.Handler {
return HealthHandler(pg, rdb, logger)
}

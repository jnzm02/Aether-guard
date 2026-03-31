package handlers

import (
"database/sql"
"encoding/json"
"net/http"
"net/http/httptest"
"testing"

"go.uber.org/zap"
_ "modernc.org/sqlite"
)

func nop() *zap.Logger { return zap.NewNop() }

// testDB opens a fresh in-memory SQLite database with schema and seed data.
func testDB(t *testing.T) *sql.DB {
t.Helper()
db, err := sql.Open("sqlite", ":memory:")
if err != nil {
t.Fatalf("open sqlite: %v", err)
}
db.SetMaxOpenConns(1)

_, err = db.Exec(`
CREATE TABLE users (
id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE
);
CREATE TABLE orders (
id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, product TEXT NOT NULL,
total REAL NOT NULL, status TEXT NOT NULL
);
`)
if err != nil {
t.Fatalf("create schema: %v", err)
}

_, err = db.Exec(`
INSERT INTO users (id, name, email) VALUES
(1, 'Alice Zhao', 'alice@corp.example.com'),
(2, 'Bob Patel', 'bob@corp.example.com');
INSERT INTO orders (id, user_id, product, total, status) VALUES
(101, 1, 'Laptop Pro', 1299.99, 'shipped'),
(102, 2, 'Keyboard', 149.50, 'processing');
`)
if err != nil {
t.Fatalf("seed data: %v", err)
}

t.Cleanup(func() { db.Close() })
return db
}

// ─────────────────────────────────────────────────────────────────────────────
// HealthHandler
// ─────────────────────────────────────────────────────────────────────────────

func TestHealthHandler_Returns200(t *testing.T) {
req := httptest.NewRequest(http.MethodGet, "/health", nil)
rec := httptest.NewRecorder()

HealthHandler(nop()).ServeHTTP(rec, req)

if rec.Code != http.StatusOK {
t.Errorf("status = %d, want 200", rec.Code)
}
}

func TestHealthHandler_ResponseBody(t *testing.T) {
req := httptest.NewRequest(http.MethodGet, "/health", nil)
rec := httptest.NewRecorder()

HealthHandler(nop()).ServeHTTP(rec, req)

var body map[string]string
if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
t.Fatalf("body decode error: %v", err)
}
if body["status"] != "ok" {
t.Errorf("body.status = %q, want %q", body["status"], "ok")
}
if body["service"] == "" {
t.Error("expected non-empty body.service")
}
}

func TestHealthHandler_ContentType(t *testing.T) {
req := httptest.NewRequest(http.MethodGet, "/health", nil)
rec := httptest.NewRecorder()

HealthHandler(nop()).ServeHTTP(rec, req)

ct := rec.Header().Get("Content-Type")
if ct != "application/json" {
t.Errorf("Content-Type = %q, want application/json", ct)
}
}

// ─────────────────────────────────────────────────────────────────────────────
// ReadyHandler
// ─────────────────────────────────────────────────────────────────────────────

func TestReadyHandler_Returns200(t *testing.T) {
req := httptest.NewRequest(http.MethodGet, "/ready", nil)
rec := httptest.NewRecorder()

ReadyHandler(nop()).ServeHTTP(rec, req)

if rec.Code != http.StatusOK {
t.Errorf("status = %d, want 200", rec.Code)
}
}

func TestReadyHandler_StatusIsReady(t *testing.T) {
req := httptest.NewRequest(http.MethodGet, "/ready", nil)
rec := httptest.NewRecorder()

ReadyHandler(nop()).ServeHTTP(rec, req)

var body map[string]string
if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
t.Fatalf("body decode: %v", err)
}
if body["status"] != "ready" {
t.Errorf("body.status = %q, want %q", body["status"], "ready")
}
}

// ─────────────────────────────────────────────────────────────────────────────
// UsersHandler
// ─────────────────────────────────────────────────────────────────────────────

func TestUsersHandler_Returns200(t *testing.T) {
req := httptest.NewRequest(http.MethodGet, "/api/users", nil)
rec := httptest.NewRecorder()

UsersHandler(nop(), testDB(t)).ServeHTTP(rec, req)

if rec.Code != http.StatusOK {
t.Errorf("status = %d, want 200", rec.Code)
}
}

func TestUsersHandler_ResponseShape(t *testing.T) {
req := httptest.NewRequest(http.MethodGet, "/api/users", nil)
rec := httptest.NewRecorder()

UsersHandler(nop(), testDB(t)).ServeHTTP(rec, req)

var body map[string]any
if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
t.Fatalf("body decode: %v", err)
}

users, ok := body["users"].([]any)
if !ok || len(users) == 0 {
t.Error("expected non-empty users array in response")
}

count, ok := body["count"].(float64)
if !ok || int(count) != len(users) {
t.Errorf("count=%v does not match len(users)=%d", body["count"], len(users))
}
}

// ─────────────────────────────────────────────────────────────────────────────
// OrdersHandler
// ─────────────────────────────────────────────────────────────────────────────

func TestOrdersHandler_Returns200(t *testing.T) {
req := httptest.NewRequest(http.MethodGet, "/api/orders", nil)
rec := httptest.NewRecorder()

OrdersHandler(nop(), testDB(t)).ServeHTTP(rec, req)

if rec.Code != http.StatusOK {
t.Errorf("status = %d, want 200", rec.Code)
}
}

func TestOrdersHandler_ResponseShape(t *testing.T) {
req := httptest.NewRequest(http.MethodGet, "/api/orders", nil)
rec := httptest.NewRecorder()

OrdersHandler(nop(), testDB(t)).ServeHTTP(rec, req)

var body map[string]any
if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
t.Fatalf("body decode: %v", err)
}

orders, ok := body["orders"].([]any)
if !ok || len(orders) == 0 {
t.Error("expected non-empty orders array in response")
}

// Validate first order has expected fields.
first, ok := orders[0].(map[string]any)
if !ok {
t.Fatal("first order is not an object")
}
for _, field := range []string{"id", "user_id", "total", "status"} {
if first[field] == nil {
t.Errorf("expected field %q in first order", field)
}
}
}

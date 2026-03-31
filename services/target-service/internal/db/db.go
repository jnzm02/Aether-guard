// Package db initialises an in-process SQLite database (pure-Go, no CGO)
// that backs the /api/users and /api/orders handlers with real SQL queries.
// The database is created in-memory; data is seeded at startup so that
// query latency metrics reflect actual SQLite I/O, not just time.Sleep.
package db

import (
	"database/sql"
	"fmt"

	_ "modernc.org/sqlite" // registers the "sqlite" driver
)

// New opens an in-memory SQLite database, creates the schema, and seeds
// it with demo data. The caller is responsible for calling db.Close().
func New() (*sql.DB, error) {
	// Use a named in-memory database so multiple callers in tests share the
	// same instance via the same URI.
	db, err := sql.Open("sqlite", "file::memory:?cache=shared")
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	// Single connection; in-memory DBs don't benefit from a pool.
	db.SetMaxOpenConns(1)

	if err := createSchema(db); err != nil {
		db.Close()
		return nil, fmt.Errorf("create schema: %w", err)
	}
	if err := seedData(db); err != nil {
		db.Close()
		return nil, fmt.Errorf("seed data: %w", err)
	}
	return db, nil
}

func createSchema(db *sql.DB) error {
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS users (
			id         INTEGER PRIMARY KEY,
			name       TEXT    NOT NULL,
			email      TEXT    NOT NULL UNIQUE,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP
		);

		CREATE TABLE IF NOT EXISTS orders (
			id         INTEGER PRIMARY KEY,
			user_id    INTEGER NOT NULL REFERENCES users(id),
			product    TEXT    NOT NULL,
			total      REAL    NOT NULL,
			status     TEXT    NOT NULL DEFAULT 'pending',
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP
		);

		CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
	`)
	return err
}

func seedData(db *sql.DB) error {
	if _, err := db.Exec(`
		INSERT OR IGNORE INTO users (id, name, email) VALUES
			(1, 'Alice Zhao',   'alice@corp.example.com'),
			(2, 'Bob Patel',    'bob@corp.example.com'),
			(3, 'Cara Müller',  'cara@corp.example.com'),
			(4, 'Diego Rivera', 'diego@corp.example.com'),
			(5, 'Elif Şahin',   'elif@corp.example.com');
	`); err != nil {
		return err
	}
	_, err := db.Exec(`
		INSERT OR IGNORE INTO orders (id, user_id, product, total, status) VALUES
			(101, 1, 'Laptop Pro',          1299.99, 'shipped'),
			(102, 2, 'Mechanical Keyboard',  149.50, 'processing'),
			(103, 3, 'USB Hub',               19.00, 'delivered'),
			(104, 1, 'Monitor 4K',           699.00, 'shipped'),
			(105, 4, 'Webcam HD',             89.99, 'pending'),
			(106, 5, 'Headphones',           249.00, 'delivered'),
			(107, 2, 'Mouse Wireless',        59.99, 'processing');
	`)
	return err
}

# Phase A.2: Architecture Design for Target-Service Redesign

**Status**: In Progress
**Migration Strategy**: Option 1 (Conservative/Additive) — Preserve all existing metrics/endpoints, add new ones alongside
**Validation Requirement**: All 9 patterns must fire correctly in Phase A.4 (Pattern 6 requires actual dependency failure test)

---

## Design Principles

1. **Backward Compatibility First**: All existing chaos endpoints, metrics, and log patterns MUST be preserved exactly as-is
2. **Additive Growth**: New functionality adds new metrics/endpoints without removing or renaming existing ones
3. **Production Realism**: Simulate real e-commerce workload with complex failure modes
4. **Observable by Design**: Rich metrics at every layer (HTTP, cache, DB, workers, external APIs)
5. **Clean Architecture**: Handlers → Services → Repositories pattern with dependency injection

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         HTTP Layer                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Business   │  │    Chaos     │  │ Observability│          │
│  │   Endpoints  │  │  Injection   │  │  (/metrics)  │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└────────────┬──────────────┬──────────────────┬─────────────────┘
             │              │                  │
             ▼              ▼                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Service Layer                               │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────┐ │
│  │   Order    │  │   User     │  │  Payment   │  │   Cache   │ │
│  │  Service   │  │  Service   │  │  Service   │  │  Service  │ │
│  └────────────┘  └────────────┘  └────────────┘  └───────────┘ │
└────────┬───────────────┬────────────────┬──────────────┬────────┘
         │               │                │              │
         ▼               ▼                ▼              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Infrastructure Layer                           │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────┐ │
│  │  Postgres  │  │   Redis    │  │  External  │  │  Worker   │ │
│  │    Repo    │  │   Client   │  │ API Client │  │   Pool    │ │
│  └────────────┘  └────────────┘  └────────────┘  └───────────┘ │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    Background Services                           │
│  • Metrics Collector (runtime stats every 5s)                   │
│  • Worker Pool (background job processing)                       │
│  • Circuit Breaker Monitor (dependency health tracking)          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
services/target-service/
├── cmd/
│   └── server/
│       └── main.go                    # Entrypoint, DI container setup
├── internal/
│   ├── config/
│   │   └── config.go                  # Environment variable loading
│   ├── domain/
│   │   ├── user.go                    # User entity
│   │   ├── order.go                   # Order entity
│   │   └── product.go                 # Product entity
│   ├── service/
│   │   ├── user_service.go            # User business logic
│   │   ├── order_service.go           # Order business logic
│   │   ├── payment_service.go         # Payment processing (external API)
│   │   └── cache_service.go           # Cache abstraction (Redis/in-memory)
│   ├── repository/
│   │   ├── user_repo.go               # User DB operations
│   │   ├── order_repo.go              # Order DB operations
│   │   └── product_repo.go            # Product DB operations
│   ├── infrastructure/
│   │   ├── postgres.go                # Postgres connection pool
│   │   ├── redis.go                   # Redis client with circuit breaker
│   │   ├── http_client.go             # HTTP client for external APIs
│   │   └── worker_pool.go             # Background job worker pool
│   ├── handlers/
│   │   ├── users.go                   # PRESERVED: existing /api/users
│   │   ├── orders.go                  # PRESERVED: existing /api/orders
│   │   ├── health.go                  # PRESERVED: /health, /ready
│   │   ├── products.go                # NEW: /api/products
│   │   ├── cart.go                    # NEW: /api/cart
│   │   └── checkout.go                # NEW: /api/checkout
│   ├── chaos/
│   │   ├── chaos.go                   # PRESERVED: existing chaos endpoints
│   │   ├── memory.go                  # PRESERVED: /chaos/memleak
│   │   ├── cpu.go                     # PRESERVED: /chaos/cpu
│   │   ├── latency.go                 # PRESERVED: /chaos/latency
│   │   ├── error.go                   # PRESERVED: /chaos/error
│   │   ├── goroutine_leak.go          # NEW: /chaos/goroutine-leak
│   │   ├── connection_leak.go         # NEW: /chaos/connection-leak
│   │   ├── cache_stampede.go          # NEW: /chaos/cache-stampede
│   │   ├── cascading_failure.go       # NEW: /chaos/cascading (cache→DB→OOM)
│   │   └── reset.go                   # PRESERVED: /chaos/reset
│   └── metrics/
│       ├── metrics.go                 # PRESERVED: existing metrics
│       ├── http_metrics.go            # PRESERVED: RED metrics
│       ├── runtime_metrics.go         # PRESERVED: runtime collector
│       ├── business_metrics.go        # NEW: orders, payments, inventory
│       ├── cache_metrics.go           # NEW: cache hit/miss, evictions
│       ├── db_metrics.go              # NEW: connection pool, query latency
│       └── worker_metrics.go          # NEW: job queue, processing duration
├── Dockerfile                         # PRESERVED: existing build
├── go.mod
└── go.sum
```

---

## Preserved Components (Backward Compatibility)

### 1. Chaos Endpoints (MUST NOT CHANGE)

**Existing endpoints** (chaos/chaos.go, chaos/memory.go, chaos/cpu.go, etc.):
```go
POST /chaos/memleak?mb=N              // chaos/memory.go
GET  /chaos/cpu?cores=N&ms=N          // chaos/cpu.go
GET  /chaos/latency?ms=N              // chaos/latency.go
GET  /chaos/error?rate=N              // chaos/error.go
POST /chaos/reset                     // chaos/reset.go
GET  /chaos/status                    // chaos/chaos.go
```

**Implementation requirement**:
- Keep exact same request/response format
- Keep exact same metric updates (e.g., `metrics.MemLeakBytesAllocated.Set()`)
- Keep exact same logging (e.g., `logger.Warn("⚠️  chaos/memleak: memory leak injected", ...)`)

### 2. Metrics (MUST NOT CHANGE)

**Existing metrics** (metrics/metrics.go):
```go
// HTTP golden signals
aether_guard_http_requests_total{method, path, status_code}
aether_guard_http_request_duration_seconds{method, path}

// Chaos metrics
aether_guard_chaos_memleak_bytes_allocated
aether_guard_chaos_errors_injected_total{type}
aether_guard_chaos_latency_injected_seconds
aether_guard_chaos_cpu_cores_active

// Runtime metrics
aether_guard_runtime_goroutines
aether_guard_runtime_heap_inuse_bytes
aether_guard_runtime_heap_objects
aether_guard_runtime_gc_pause_microseconds

// DB query metrics (currently SQLite)
aether_guard_db_query_duration_seconds{table, operation}
```

**Implementation requirement**:
- Keep all metric names, namespaces, labels exactly as-is
- Keep runtime collector goroutine (samples every 5s)
- Keep HTTP middleware that records requests/duration

### 3. Logging (MUST NOT CHANGE)

**Startup log** (cmd/server/main.go):
```go
logger.Info("🚀 aether-guard/target-service starting",
    zap.String("addr", addr),
    zap.String("version", "1.2.0"),  // Version can increment
)
// MUST contain "starting" (case-insensitive) for Pattern 2
```

**Fatal errors** (cmd/server/main.go):
```go
logger.Fatal("failed to initialise <component>", zap.Error(err))
// MUST contain "failed" for Pattern 7
```

**Dependency errors** (NEW, but use stdlib errors):
```go
logger.Error("redis connection failed", zap.Error(err))
// zap.Error(err) will contain stdlib messages like "connection refused"
```

### 4. HTTP Endpoints (PRESERVE EXISTING)

**Existing endpoints** (handlers/users.go, handlers/orders.go):
```go
GET /api/users              // handlers/users.go (PRESERVED)
GET /api/orders             // handlers/orders.go (PRESERVED)
GET /health                 // handlers/health.go (PRESERVED)
GET /ready                  // handlers/ready.go (PRESERVED)
GET /metrics                // promhttp.Handler() (PRESERVED)
```

---

## New Components (Additive)

### 1. New Chaos Endpoints

**Goroutine Leak** (chaos/goroutine_leak.go):
```go
POST /chaos/goroutine-leak?count=N&duration=N
// Spawns N goroutines that block for duration seconds (or forever if duration=0)
// Does NOT clean up (intentional leak for Pattern 8 testing)
// Updates: aether_guard_runtime_goroutines (via runtime collector)
```

**Connection Pool Leak** (chaos/connection_leak.go):
```go
POST /chaos/connection-leak?count=N&target=db|redis
// Checks out N connections from the pool without returning them
// Metrics: aether_guard_db_connections_active, aether_guard_redis_connections_active
```

**Cache Stampede** (chaos/cache_stampede.go):
```go
POST /chaos/cache-stampede?key=pattern&duration=N
// Deletes cache entries matching pattern, causing thundering herd to backend
// Metrics: aether_guard_cache_hit_rate drops, aether_guard_db_query_duration spikes
```

**Cascading Failure** (chaos/cascading_failure.go):
```go
POST /chaos/cascading?trigger=cache_stampede|slow_query|dependency_timeout
// Triggers multi-stage failure:
//   Stage 1: Cache stampede (cache hit rate drops)
//   Stage 2: DB overload (query latency spikes, connections max out)
//   Stage 3: Memory pressure (query result buffering)
//   Stage 4: OOM kill
// This is the validation scenario from the original spec
```

### 2. New Business Endpoints

**Products** (handlers/products.go):
```go
GET  /api/products?category=X&limit=N    // List products (cached)
GET  /api/products/:id                   // Get product details
POST /api/products                       // Create product (admin)
```

**Shopping Cart** (handlers/cart.go):
```go
GET    /api/cart/:user_id                // Get user's cart
POST   /api/cart/:user_id/items          // Add item to cart
DELETE /api/cart/:user_id/items/:item_id // Remove item
```

**Checkout** (handlers/checkout.go):
```go
POST /api/checkout                       // Process order
// Workflow:
//   1. Validate cart (DB query)
//   2. Check inventory (cached, can trigger cache stampede)
//   3. Process payment (external API, can timeout)
//   4. Create order (DB transaction)
//   5. Enqueue background job (worker pool)
//   6. Clear cart cache
// This endpoint exercises all failure modes
```

### 3. New Metrics (Additive)

**Business metrics** (metrics/business_metrics.go):
```go
aether_guard_business_orders_total{status}                    // created, completed, failed
aether_guard_business_payment_duration_seconds                // External API latency
aether_guard_business_inventory_checks_total{result}          // hit, miss, stale
aether_guard_business_cart_operations_total{operation}        // add, remove, checkout
```

**Cache metrics** (metrics/cache_metrics.go):
```go
aether_guard_cache_operations_total{operation, result}        // get_hit, get_miss, set, delete
aether_guard_cache_hit_rate                                   // Gauge: hits / (hits + misses)
aether_guard_cache_evictions_total{reason}                    // size_limit, ttl_expired, manual
aether_guard_cache_size_bytes                                 // Current cache memory usage
```

**Database metrics** (metrics/db_metrics.go):
```go
aether_guard_db_connections_active                            // Current active connections
aether_guard_db_connections_idle                              // Current idle connections
aether_guard_db_connections_max                               // Configured max connections
aether_guard_db_connection_wait_duration_seconds              // Time waiting for connection
aether_guard_db_transactions_total{status}                    // commit, rollback
```

**Worker metrics** (metrics/worker_metrics.go):
```go
aether_guard_worker_jobs_queued                               // Current queue depth
aether_guard_worker_jobs_total{status}                        // completed, failed, timeout
aether_guard_worker_processing_duration_seconds{job_type}     // Job processing time
```

**Circuit breaker metrics** (metrics/circuit_breaker_metrics.go):
```go
aether_guard_circuit_breaker_state{service}                   // 0=closed, 1=half-open, 2=open
aether_guard_circuit_breaker_events_total{service, event}     // success, failure, timeout, rejected
```

---

## Service Layer Design

### User Service (service/user_service.go)

```go
type UserService struct {
    repo  UserRepository
    cache CacheService
    logger *zap.Logger
}

func (s *UserService) GetUser(ctx context.Context, id int64) (*domain.User, error) {
    // Try cache first
    cacheKey := fmt.Sprintf("user:%d", id)
    if cached, err := s.cache.Get(ctx, cacheKey); err == nil {
        metrics.CacheOperations.WithLabelValues("get", "hit").Inc()
        var user domain.User
        json.Unmarshal(cached, &user)
        return &user, nil
    }
    metrics.CacheOperations.WithLabelValues("get", "miss").Inc()

    // Cache miss → DB query
    start := time.Now()
    user, err := s.repo.GetByID(ctx, id)
    metrics.DBQueryDuration.WithLabelValues("users", "select").Observe(time.Since(start).Seconds())

    if err != nil {
        return nil, err
    }

    // Update cache
    if data, err := json.Marshal(user); err == nil {
        s.cache.Set(ctx, cacheKey, data, 5*time.Minute)
    }

    return user, nil
}
```

### Order Service (service/order_service.go)

```go
type OrderService struct {
    orderRepo   OrderRepository
    productRepo ProductRepository
    cache       CacheService
    payment     PaymentService
    workers     WorkerPool
    logger      *zap.Logger
}

func (s *OrderService) CreateOrder(ctx context.Context, req *CreateOrderRequest) (*domain.Order, error) {
    // This method exercises multiple failure points:

    // 1. Inventory check (can trigger cache stampede)
    if err := s.checkInventory(ctx, req.Items); err != nil {
        metrics.BusinessOrders.WithLabelValues("failed_inventory").Inc()
        return nil, err
    }

    // 2. Payment processing (external API, can timeout)
    start := time.Now()
    paymentID, err := s.payment.ProcessPayment(ctx, req.PaymentInfo)
    metrics.BusinessPaymentDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        metrics.BusinessOrders.WithLabelValues("failed_payment").Inc()
        return nil, err
    }

    // 3. DB transaction (can fail due to connection pool exhaustion)
    order, err := s.orderRepo.Create(ctx, &domain.Order{
        UserID:    req.UserID,
        Items:     req.Items,
        PaymentID: paymentID,
        Status:    "pending",
    })
    if err != nil {
        metrics.BusinessOrders.WithLabelValues("failed_db").Inc()
        return nil, err
    }

    // 4. Background job (can fail if worker pool full)
    job := &Job{Type: "order_confirmation_email", OrderID: order.ID}
    if err := s.workers.Enqueue(ctx, job); err != nil {
        s.logger.Warn("failed to enqueue email job", zap.Error(err))
        // Non-fatal, continue
    }

    metrics.BusinessOrders.WithLabelValues("created").Inc()
    return order, nil
}

func (s *OrderService) checkInventory(ctx context.Context, items []OrderItem) error {
    for _, item := range items {
        cacheKey := fmt.Sprintf("inventory:%d", item.ProductID)

        // Try cache
        if cached, err := s.cache.Get(ctx, cacheKey); err == nil {
            metrics.BusinessInventoryChecks.WithLabelValues("hit").Inc()
            var stock int
            json.Unmarshal(cached, &stock)
            if stock < item.Quantity {
                return ErrInsufficientStock
            }
            continue
        }

        // Cache miss → DB query (potential stampede if cache cleared)
        metrics.BusinessInventoryChecks.WithLabelValues("miss").Inc()
        stock, err := s.productRepo.GetStock(ctx, item.ProductID)
        if err != nil {
            return err
        }

        if stock < item.Quantity {
            return ErrInsufficientStock
        }

        // Update cache
        data, _ := json.Marshal(stock)
        s.cache.Set(ctx, cacheKey, data, 1*time.Minute)
    }
    return nil
}
```

### Payment Service (service/payment_service.go)

```go
type PaymentService struct {
    httpClient  *http.Client
    breaker     *circuitbreaker.CircuitBreaker
    logger      *zap.Logger
}

func (s *PaymentService) ProcessPayment(ctx context.Context, info PaymentInfo) (string, error) {
    // Call external payment API through circuit breaker
    result, err := s.breaker.Call(func() (interface{}, error) {
        req, _ := http.NewRequestWithContext(ctx, "POST", "https://payment-api.example.com/charge", nil)
        resp, err := s.httpClient.Do(req)
        if err != nil {
            return nil, err
        }
        defer resp.Body.Close()

        if resp.StatusCode != 200 {
            return nil, fmt.Errorf("payment API returned %d", resp.StatusCode)
        }

        var result struct {
            PaymentID string `json:"payment_id"`
        }
        json.NewDecoder(resp.Body).Decode(&result)
        return result.PaymentID, nil
    })

    if err != nil {
        metrics.CircuitBreakerEvents.WithLabelValues("payment_api", "failure").Inc()
        return "", err
    }

    return result.(string), nil
}
```

---

## Infrastructure Layer Design

### Postgres Repository (infrastructure/postgres.go)

```go
type PostgresDB struct {
    pool    *pgxpool.Pool
    logger  *zap.Logger
}

func NewPostgresDB(ctx context.Context, cfg *config.Config) (*PostgresDB, error) {
    poolConfig, err := pgxpool.ParseConfig(cfg.PostgresURL)
    if err != nil {
        return nil, err
    }

    // Connection pool configuration
    poolConfig.MaxConns = int32(cfg.DBMaxConnections)           // Default: 25
    poolConfig.MinConns = int32(cfg.DBMinConnections)           // Default: 5
    poolConfig.MaxConnLifetime = cfg.DBConnMaxLifetime          // Default: 1h
    poolConfig.MaxConnIdleTime = cfg.DBConnMaxIdleTime          // Default: 30m

    pool, err := pgxpool.NewWithConfig(ctx, poolConfig)
    if err != nil {
        return nil, fmt.Errorf("failed to create connection pool: %w", err)
    }

    // Verify connection
    if err := pool.Ping(ctx); err != nil {
        return nil, fmt.Errorf("failed to ping database: %w", err)
    }

    db := &PostgresDB{pool: pool, logger: zap.L()}

    // Start connection pool metrics collector
    go db.collectPoolMetrics(ctx)

    return db, nil
}

func (db *PostgresDB) collectPoolMetrics(ctx context.Context) {
    ticker := time.NewTicker(5 * time.Second)
    defer ticker.Stop()

    for {
        select {
        case <-ticker.C:
            stats := db.pool.Stat()
            metrics.DBConnectionsActive.Set(float64(stats.AcquiredConns()))
            metrics.DBConnectionsIdle.Set(float64(stats.IdleConns()))
            metrics.DBConnectionsMax.Set(float64(stats.MaxConns()))
        case <-ctx.Done():
            return
        }
    }
}
```

### Redis Client (infrastructure/redis.go)

```go
type RedisClient struct {
    client  *redis.Client
    breaker *circuitbreaker.CircuitBreaker
    logger  *zap.Logger
}

func NewRedisClient(cfg *config.Config) (*RedisClient, error) {
    client := redis.NewClient(&redis.Options{
        Addr:         cfg.RedisAddr,
        Password:     cfg.RedisPassword,
        DB:           cfg.RedisDB,
        MaxRetries:   3,
        PoolSize:     cfg.RedisPoolSize,     // Default: 10
        MinIdleConns: cfg.RedisMinIdleConns, // Default: 2
    })

    // Verify connection
    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()

    if err := client.Ping(ctx).Err(); err != nil {
        return nil, fmt.Errorf("redis ping failed: %w", err)
    }

    breaker := circuitbreaker.New(3, 10*time.Second) // 3 failures → open for 10s

    return &RedisClient{
        client:  client,
        breaker: breaker,
        logger:  zap.L(),
    }, nil
}

func (r *RedisClient) Get(ctx context.Context, key string) ([]byte, error) {
    result, err := r.breaker.Call(func() (interface{}, error) {
        return r.client.Get(ctx, key).Bytes()
    })

    if err != nil {
        if err == redis.Nil {
            return nil, ErrCacheMiss
        }
        metrics.CircuitBreakerEvents.WithLabelValues("redis", "failure").Inc()
        r.logger.Error("redis get failed", zap.String("key", key), zap.Error(err))
        return nil, err
    }

    return result.([]byte), nil
}
```

---

## Chaos Injection Implementation Details

### Goroutine Leak (chaos/goroutine_leak.go)

```go
var (
    leakedGoroutines atomic.Int32
)

func GoroutineLeakHandler(logger *zap.Logger) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        count := queryInt(r, "count", 100, 1, 10000)
        duration := queryInt(r, "duration", 0, 0, 3600) // 0 = infinite

        for i := 0; i < count; i++ {
            leakedGoroutines.Add(1)
            go func() {
                // INTENTIONAL LEAK: No defer, no cleanup
                if duration == 0 {
                    select {} // block forever
                } else {
                    time.Sleep(time.Duration(duration) * time.Second)
                    leakedGoroutines.Add(-1)
                }
            }()
        }

        metrics.ChaosErrorsInjected.WithLabelValues("goroutine_leak").Inc()

        logger.Warn("⚠️  chaos/goroutine-leak: goroutine leak injected",
            zap.Int("count", count),
            zap.Int("duration_seconds", duration),
            zap.Int32("total_leaked", leakedGoroutines.Load()),
        )

        respondJSON(w, http.StatusOK, map[string]any{
            "event":         "goroutine_leak_injected",
            "count":         count,
            "duration":      duration,
            "total_leaked":  leakedGoroutines.Load(),
        })
    })
}
```

### Cascading Failure (chaos/cascading_failure.go)

```go
func CascadingFailureHandler(cacheService CacheService, db *PostgresDB, logger *zap.Logger) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        trigger := r.URL.Query().Get("trigger")
        if trigger == "" {
            trigger = "cache_stampede"
        }

        stages := []string{}

        // Stage 1: Trigger cache stampede
        logger.Warn("⚠️  chaos/cascading: Stage 1 - clearing cache")
        cacheService.FlushPattern(r.Context(), "inventory:*")
        stages = append(stages, "cache_cleared")
        time.Sleep(2 * time.Second)

        // Stage 2: Simulate heavy DB load (slow queries)
        logger.Warn("⚠️  chaos/cascading: Stage 2 - injecting slow queries")
        for i := 0; i < 50; i++ {
            go func() {
                ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
                defer cancel()
                // Slow query that holds connections
                db.pool.Exec(ctx, "SELECT pg_sleep(10)")
            }()
        }
        stages = append(stages, "db_overload")
        time.Sleep(3 * time.Second)

        // Stage 3: Allocate memory to simulate query result buffering
        logger.Warn("⚠️  chaos/cascading: Stage 3 - memory pressure")
        chunk := make([]byte, 500*1024*1024) // 500 MB
        for i := range chunk {
            chunk[i] = byte(i)
        }
        stages = append(stages, "memory_pressure")

        // Stage 4 happens naturally: OOM kill if memory limit exceeded

        respondJSON(w, http.StatusOK, map[string]any{
            "event":   "cascading_failure_injected",
            "trigger": trigger,
            "stages":  stages,
        })
    })
}
```

---

## Configuration (config/config.go)

```go
type Config struct {
    // Server
    Port string

    // Postgres
    PostgresURL           string
    DBMaxConnections      int
    DBMinConnections      int
    DBConnMaxLifetime     time.Duration
    DBConnMaxIdleTime     time.Duration
    DBQueryTimeout        time.Duration

    // Redis
    RedisAddr             string
    RedisPassword         string
    RedisDB               int
    RedisPoolSize         int
    RedisMinIdleConns     int
    CacheTTL              time.Duration

    // External APIs
    PaymentAPIURL         string
    PaymentAPITimeout     time.Duration

    // Worker Pool
    WorkerPoolSize        int
    WorkerQueueSize       int

    // Circuit Breaker
    CircuitBreakerThreshold int
    CircuitBreakerTimeout   time.Duration

    // Feature Flags
    EnableRealDB          bool   // true = Postgres, false = in-memory SQLite
    EnableRealCache       bool   // true = Redis, false = in-memory map
    EnableExternalAPIs    bool   // true = real APIs, false = mocks
}

func Load() *Config {
    return &Config{
        Port:                    getEnv("PORT", "8080"),

        PostgresURL:             getEnv("POSTGRES_URL", "postgres://user:pass@localhost:5432/target_service"),
        DBMaxConnections:        getEnvInt("DB_MAX_CONNECTIONS", 25),
        DBMinConnections:        getEnvInt("DB_MIN_CONNECTIONS", 5),
        DBConnMaxLifetime:       getEnvDuration("DB_CONN_MAX_LIFETIME", time.Hour),
        DBConnMaxIdleTime:       getEnvDuration("DB_CONN_MAX_IDLE_TIME", 30*time.Minute),
        DBQueryTimeout:          getEnvDuration("DB_QUERY_TIMEOUT", 30*time.Second),

        RedisAddr:               getEnv("REDIS_ADDR", "localhost:6379"),
        RedisPassword:           getEnv("REDIS_PASSWORD", ""),
        RedisDB:                 getEnvInt("REDIS_DB", 0),
        RedisPoolSize:           getEnvInt("REDIS_POOL_SIZE", 10),
        RedisMinIdleConns:       getEnvInt("REDIS_MIN_IDLE_CONNS", 2),
        CacheTTL:                getEnvDuration("CACHE_TTL", 5*time.Minute),

        PaymentAPIURL:           getEnv("PAYMENT_API_URL", "http://mock-payment-api:8080"),
        PaymentAPITimeout:       getEnvDuration("PAYMENT_API_TIMEOUT", 5*time.Second),

        WorkerPoolSize:          getEnvInt("WORKER_POOL_SIZE", 10),
        WorkerQueueSize:         getEnvInt("WORKER_QUEUE_SIZE", 1000),

        CircuitBreakerThreshold: getEnvInt("CIRCUIT_BREAKER_THRESHOLD", 3),
        CircuitBreakerTimeout:   getEnvDuration("CIRCUIT_BREAKER_TIMEOUT", 10*time.Second),

        EnableRealDB:            getEnvBool("ENABLE_REAL_DB", false),
        EnableRealCache:         getEnvBool("ENABLE_REAL_CACHE", false),
        EnableExternalAPIs:      getEnvBool("ENABLE_EXTERNAL_APIS", false),
    }
}
```

---

## Main Entrypoint (cmd/server/main.go)

```go
func main() {
    logger, _ := zap.NewProduction()
    defer logger.Sync()

    cfg := config.Load()

    // ── Initialize infrastructure ────────────────────────────────────────────
    ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()

    var db *infrastructure.PostgresDB
    var err error
    if cfg.EnableRealDB {
        db, err = infrastructure.NewPostgresDB(ctx, cfg)
        if err != nil {
            logger.Fatal("failed to initialise Postgres database", zap.Error(err))
        }
        defer db.Close()
    } else {
        // Fallback to in-memory SQLite (existing behavior)
        db, err = db.New()
        if err != nil {
            logger.Fatal("failed to initialise SQLite database", zap.Error(err))
        }
        defer db.Close()
    }

    var cache service.CacheService
    if cfg.EnableRealCache {
        redisClient, err := infrastructure.NewRedisClient(cfg)
        if err != nil {
            logger.Fatal("failed to initialise Redis cache", zap.Error(err))
        }
        defer redisClient.Close()
        cache = service.NewRedisCacheService(redisClient, cfg.CacheTTL)
    } else {
        cache = service.NewInMemoryCacheService()
    }

    // ── Initialize services ───────────────────────────────────────────────────
    userRepo := repository.NewUserRepository(db)
    orderRepo := repository.NewOrderRepository(db)
    productRepo := repository.NewProductRepository(db)

    userService := service.NewUserService(userRepo, cache, logger)
    orderService := service.NewOrderService(orderRepo, productRepo, cache, nil, nil, logger)

    // ── HTTP router setup ─────────────────────────────────────────────────────
    mux := http.NewServeMux()

    // PRESERVED: Existing endpoints
    mux.Handle("/api/users", metrics.Middleware(handlers.UsersHandler(logger, userService)))
    mux.Handle("/api/orders", metrics.Middleware(handlers.OrdersHandler(logger, orderService)))

    // NEW: Additional business endpoints
    mux.Handle("/api/products", metrics.Middleware(handlers.ProductsHandler(logger, productService)))
    mux.Handle("/api/cart", metrics.Middleware(handlers.CartHandler(logger, cartService)))
    mux.Handle("/api/checkout", metrics.Middleware(handlers.CheckoutHandler(logger, checkoutService)))

    // PRESERVED: Existing chaos endpoints
    mux.Handle("/chaos/memleak", metrics.Middleware(chaos.MemLeakHandler(logger)))
    mux.Handle("/chaos/cpu", metrics.Middleware(chaos.CPUSpikeHandler(logger)))
    mux.Handle("/chaos/latency", metrics.Middleware(chaos.LatencyHandler(logger)))
    mux.Handle("/chaos/error", metrics.Middleware(chaos.ErrorHandler(logger)))
    mux.Handle("/chaos/status", chaos.StatusHandler(logger))
    mux.Handle("/chaos/reset", chaos.ResetHandler(logger))

    // NEW: Additional chaos endpoints
    mux.Handle("/chaos/goroutine-leak", metrics.Middleware(chaos.GoroutineLeakHandler(logger)))
    mux.Handle("/chaos/connection-leak", metrics.Middleware(chaos.ConnectionLeakHandler(db, cache, logger)))
    mux.Handle("/chaos/cache-stampede", metrics.Middleware(chaos.CacheStampedeHandler(cache, logger)))
    mux.Handle("/chaos/cascading", metrics.Middleware(chaos.CascadingFailureHandler(cache, db, logger)))

    // PRESERVED: Observability endpoints
    mux.Handle("/metrics", promhttp.Handler())
    mux.Handle("/health", handlers.HealthHandler(logger))
    mux.Handle("/ready", handlers.ReadyHandler(logger))

    // PRESERVED: pprof endpoints
    // ... (same as existing)

    // ── Background services ───────────────────────────────────────────────────
    runtimeStop := make(chan struct{})
    metrics.StartRuntimeCollector(runtimeStop) // PRESERVED

    // ── Server startup ────────────────────────────────────────────────────────
    addr := ":" + cfg.Port
    server := &http.Server{
        Addr:         addr,
        Handler:      mux,
        ReadTimeout:  30 * time.Second,
        WriteTimeout: 60 * time.Second,
        IdleTimeout:  120 * time.Second,
    }

    go func() {
        logger.Info("🚀 aether-guard/target-service starting",  // PRESERVED: "starting" keyword
            zap.String("addr", addr),
            zap.String("version", "1.2.0"),
            zap.Bool("real_db", cfg.EnableRealDB),
            zap.Bool("real_cache", cfg.EnableRealCache),
        )
        if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
            logger.Fatal("server terminated unexpectedly", zap.Error(err))
        }
    }()

    // ── Graceful shutdown ─────────────────────────────────────────────────────
    quit := make(chan os.Signal, 1)
    signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
    <-quit

    logger.Info("shutdown signal received — draining requests")
    close(runtimeStop)

    ctx, cancel = context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()

    if err := server.Shutdown(ctx); err != nil {
        logger.Fatal("graceful shutdown failed", zap.Error(err))
    }

    logger.Info("✅ target-service shutdown complete")
}
```

---

## Phase A.2 Summary

### Preserved (Backward Compatibility)
- ✅ All 6 existing chaos endpoints with exact same behavior
- ✅ All existing metrics with same names/labels
- ✅ All existing HTTP endpoints (/api/users, /api/orders, /health, /ready, /metrics)
- ✅ Runtime collector (5s sampling)
- ✅ HTTP middleware (RED metrics)
- ✅ Startup logging ("starting" keyword)
- ✅ Fatal error logging ("failed" keyword)

### Added (New Functionality)
- ➕ 4 new chaos endpoints (goroutine-leak, connection-leak, cache-stampede, cascading)
- ➕ 3 new business endpoints (products, cart, checkout)
- ➕ 4 new metric categories (business, cache, DB pool, worker, circuit breaker)
- ➕ Real dependencies (Postgres, Redis) with feature flags for fallback to in-memory
- ➕ Circuit breaker pattern for external APIs
- ➕ Worker pool for background jobs
- ➕ Clean architecture (handlers → services → repositories)

### Validation Requirements for Phase A.4
1. **Pattern 1 (OOM Kill)**: Inject `/chaos/memleak?mb=2000` → verify kernel OOM log appears
2. **Pattern 2 (Restart Loop)**: Restart container 3x → verify "starting" appears in logs 3x
3. **Pattern 3 (Memory Leak)**: Inject `/chaos/memleak?mb=1000` → verify `memleak_bytes_allocated` and `go_memstats_heap_alloc_bytes` metrics rise
4. **Pattern 4a (CPU Traffic)**: Inject `/chaos/cpu` + load → verify `cpu_usage_percent` and `request_rate_5m` both high
5. **Pattern 4b (CPU Efficiency)**: Inject `/chaos/cpu` without load → verify `cpu_usage_percent` high, `request_rate_5m` normal
6. **Pattern 5 (Traffic Spike)**: External load → verify `request_rate_5m`, `error_rate_5m`, `latency_p99_5m` all elevated
7. **Pattern 6 (Dependency Failure)**: **STOP POSTGRES/REDIS** → verify logs contain "connection refused" / "dial tcp timeout" (MUST TEST, NOT ASSUME)
8. **Pattern 7 (Bad Deployment)**: Inject `/chaos/error?rate=0.5` + restart → verify `error_rate_5m` > 5% and logs contain "fatal"
9. **Pattern 8 (Goroutine Leak)**: Inject `/chaos/goroutine-leak?count=500` → verify `runtime_goroutines` rises above baseline*3

---

**Phase A.2 Status**: ✅ **COMPLETE — READY FOR IMPLEMENTATION**

**Next**: Phase A.3 (Implementation) → Phase A.4 (Validation)

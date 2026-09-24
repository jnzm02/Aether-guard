# target-service-java

A **JVM analog** of the Go [`services/target-service`](../target-service): an
intentionally *breakable* Spring Boot 3 microservice used to validate that the
Aether-Guard SRE agent can detect, diagnose, and remediate incidents against a
JVM workload — not just Go.

It exposes the same chaos surface and emits Prometheus metrics whose names match
the Aether-Guard metric contract **exactly**, so the agent's rules and PromQL
work unchanged. The manifest advertises `runtime: "jvm"` so the agent can select
JVM-aware RCA patterns.

- **Stack:** Spring Boot 3.3.x, Java 21, Maven.
- **Port:** `8085`.
- **Data:** in-memory (plain Java lists — no JDBC/H2). Reads are still timed into
  the `db_query_duration` timer for metric parity.

## Build & run

```bash
# Requires Java 21 + Maven. (On this repo's macOS setup:
#   export JAVA_HOME=/opt/homebrew/opt/openjdk@21 )
mvn -DskipTests compile      # compile
mvn test                     # unit + integration tests (incl. metric-scrape assertions)
mvn -DskipTests package      # -> target/target-service-java-1.0.0.jar
java -jar target/target-service-java-1.0.0.jar

# Docker (build context is THIS directory only — no repo root / SDK needed):
docker build -t aether-guard/target-service-java .
docker run -p 8085:8085 aether-guard/target-service-java
```

## Endpoints

| Method     | Path                          | Description |
|------------|-------------------------------|-------------|
| `GET`      | `/metrics`                    | Prometheus text exposition (delegates to the Micrometer `PrometheusMeterRegistry`). |
| `GET`      | `/api/users`                  | `{"users":[{id,name,email}],"count":5}`. Timed → `db_query_duration{table="users",operation="select_all"}`. |
| `GET`      | `/api/orders`                 | `{"orders":[{id,user_id,user_name,product,total,status}],"count":7}`. Timed → `db_query_duration{table="orders",operation="select_join"}`. |
| `GET`      | `/health`, `/ready`           | `200` `{"service":"aether-guard/target-service-java","status":"healthy","version":"1.0.0","dependencies":{...}}` when healthy; `503` `"unhealthy"` when a *configured* dependency's ping fails. |
| `GET`      | `/aetherguard/v1/manifest`    | Service Contract v1.0 manifest; `compliance_level: "L1"`, `runtime: "jvm"`. |

### Health / dependencies

Postgres and Redis are **optional** and unconfigured by default
(`dependencies.postgres.url` / `dependencies.redis.url` blank), so `/health`
returns `200` with an empty `dependencies` map. The check is **null-safe**: only
configured dependencies are ever pinged. A `DependencyPinger` interface lets
tests inject a failing dependency (→ `503`) and confirm an absent one is never
pinged.

## Chaos endpoints

All accept `GET` or `POST`. Numeric params are range-validated → **HTTP 400** on
out-of-range.

| Path                    | Params (range) | Effect |
|-------------------------|----------------|--------|
| `/chaos/memleak`        | `mb` (1..**4096**, default 50) | Retains `mb` MiB in a static list (leak), updates `chaos_memleak_bytes_allocated`. Cap is deliberately larger than Go's 500 so a single call can exceed the agent's 1 GB MEMORY_LEAK gate. |
| `/chaos/cpu`            | `cores` (1..CPUs×4), `ms` (100..300000) | Spins `cores` busy threads for `ms`, sets `chaos_cpu_cores_active`. |
| `/chaos/latency`        | `ms` (0..30000) | Sleeps `ms`, records `chaos_latency_injected_seconds`. |
| `/chaos/error`          | `rate` (0.0..1.0) | Returns HTTP 500 with a realistic error body with probability `rate`, else 200. |
| `/chaos/thread-leak`    | `count` (1..10000), `duration` (0..3600s, default 0), `hard` (default false) | Spawns `count` threads, raising `jvm_threads_live_threads`. `hard=true` → uninterruptible threads that survive until restart. `hard=false` → interruptible threads that `/chaos/reset` reclaims. |
| `/chaos/gc-pressure`    | `duration_seconds` (1..300, default 30) | Rapidly allocates/discards arrays in a background thread to drive `jvm_gc_pause_seconds` up. |
| `/chaos/status`         | — | JSON snapshot: `memory_leaked_bytes/mb`, `cpu_cores_active`, `threads_leaked`, `gc_pressure_active`. |
| `/chaos/reset`          | — | Frees leaked memory, stops the CPU spike, **interrupts the non-hard leaked threads** (JVM improvement over Go: reset actually recovers interruptible leaks). Reports `hard_threads_still_leaked`. |

### JVM vs Go differences (by design)

- **Memory leak cap** is 4096 MB (Go: 500) so one call can breach the 1 GB gate.
- **Thread leak** distinguishes *soft* (interruptible, reclaimed by `/chaos/reset`)
  from *hard* (uninterruptible, cleared only by restart), preserving
  RESTART-recovery semantics while also demonstrating in-place recovery.

## Metric contract

Micrometer maps dotted meter names to Prometheus snake_case and appends suffixes
(`_total` for counters, `_seconds` + `_bucket`/`_count`/`_sum` for timers). The
scrape at `/metrics` includes exactly:

| Prometheus name | Type | Labels |
|-----------------|------|--------|
| `aether_guard_http_requests_total` | Counter | `method`, `path`, `status_code` |
| `aether_guard_http_request_duration_seconds` | Histogram | `method`, `path` — buckets `[0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.5, 5.0, 10.0]` |
| `aether_guard_chaos_memleak_bytes_allocated` | Gauge | — |
| `aether_guard_chaos_errors_injected_total` | Counter | `type` |
| `aether_guard_chaos_latency_injected_seconds` | Histogram | — |
| `aether_guard_chaos_cpu_cores_active` | Gauge | — |
| `aether_guard_db_query_duration_seconds` | Timer | `table`, `operation` |

Plus JVM/process metrics from Micrometer binders (+ two published explicitly):
`jvm_memory_used_bytes{area="heap"}`, `jvm_threads_live_threads`,
`jvm_gc_pause_seconds_*`, `process_cpu_seconds_total`, `process_open_fds`.

> `process_cpu_seconds_total` and `process_open_fds` are published directly from
> the JVM `OperatingSystemMXBean` (Micrometer's own binders emit `process_cpu_usage`,
> not the Prometheus-standard counter), guaranteeing the contract names appear on
> every platform.

## Tests

`mvn test` runs:

- **HealthControllerTest** — unconfigured deps → 200 + empty map; a
  configured-but-failing `DependencyPinger` → 503; an absent pinger is never
  invoked (no NPE).
- **ManifestControllerTest** — manifest reports `runtime="jvm"`, `compliance_level="L1"`.
- **ChaosControllerTest** — memleak accumulates and the gauge rises; `rate=1.0` → 500,
  `0.0` → 200; `mb=4097` → 400; thread-leak raises `Thread.activeCount()` and
  `/chaos/reset` (non-hard) brings it back down.
- **MetricsScrapeTest** — `GET /metrics` contains all required contract names and
  the exact HTTP histogram buckets.

> Integration tests that assert on the scrape are annotated with
> `@AutoConfigureObservability`, because Spring Boot disables metrics export in
> `@SpringBootTest` contexts by default. The running application always exports.

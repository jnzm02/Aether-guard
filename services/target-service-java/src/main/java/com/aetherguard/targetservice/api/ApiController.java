package com.aetherguard.targetservice.api;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Normal production-like API endpoints, used to generate baseline traffic so
 * there are meaningful SLI baselines to compare against when chaos is injected.
 * Each read is timed into {@code aether_guard_db_query_duration_seconds} with
 * {@code table}/{@code operation} labels, mirroring the Go service.
 */
@RestController
public class ApiController {

    private final InMemoryStore store;
    private final MeterRegistry registry;

    public ApiController(InMemoryStore store, MeterRegistry registry) {
        this.store = store;
        this.registry = registry;
    }

    @GetMapping("/api/users")
    public Map<String, Object> users() {
        Timer.Sample sample = Timer.start(registry);
        try {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("users", store.users());
            body.put("count", store.users().size());
            return body;
        } finally {
            sample.stop(dbTimer("users", "select_all"));
        }
    }

    @GetMapping("/api/orders")
    public Map<String, Object> orders() {
        Timer.Sample sample = Timer.start(registry);
        try {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("orders", store.orders());
            body.put("count", store.orders().size());
            return body;
        } finally {
            sample.stop(dbTimer("orders", "select_join"));
        }
    }

    private Timer dbTimer(String table, String operation) {
        return Timer.builder("aether_guard.db.query.duration")
                .description("In-memory query latency, partitioned by table and operation.")
                .tags("table", table, "operation", operation)
                .register(registry);
    }
}

package com.aetherguard.targetservice.api;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.composite.CompositeMeterRegistry;
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Serves the Prometheus exposition at the plain {@code /metrics} path (the Go
 * service's convention) by delegating to the {@link PrometheusMeterRegistry}'s
 * own scrape. Actuator remains available for the JVM/process binders but its
 * web exposure is restricted to health.
 */
@RestController
public class MetricsController {

    private final MeterRegistry registry;

    public MetricsController(MeterRegistry registry) {
        this.registry = registry;
    }

    private static PrometheusMeterRegistry resolvePrometheus(MeterRegistry registry) {
        if (registry instanceof PrometheusMeterRegistry p) {
            return p;
        }
        if (registry instanceof CompositeMeterRegistry composite) {
            for (MeterRegistry child : composite.getRegistries()) {
                if (child instanceof PrometheusMeterRegistry p) {
                    return p;
                }
            }
        }
        throw new IllegalStateException(
                "No PrometheusMeterRegistry found (actual: " + registry.getClass().getName() + ")");
    }

    @GetMapping(value = "/metrics", produces = MediaType.TEXT_PLAIN_VALUE)
    public ResponseEntity<String> scrape() {
        return ResponseEntity.ok()
                .contentType(MediaType.valueOf("text/plain; version=0.0.4; charset=utf-8"))
                .body(resolvePrometheus(registry).scrape());
    }
}

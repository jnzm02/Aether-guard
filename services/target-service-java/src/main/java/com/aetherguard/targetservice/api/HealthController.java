package com.aetherguard.targetservice.api;

import com.aetherguard.targetservice.health.DependencyPinger;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Health / readiness endpoints. Returns 200 {@code "healthy"} when every
 * CONFIGURED dependency pings successfully; 503 {@code "unhealthy"} only when a
 * configured dependency's ping fails.
 *
 * <p>NULL-SAFE by construction: only configured dependencies are present in the
 * injected list, so an unconfigured (absent) dependency is never pinged. With no
 * dependencies configured the map is empty and the service reports healthy.
 */
@RestController
public class HealthController {

    private final List<DependencyPinger> dependencies;

    public HealthController(List<DependencyPinger> dependencies) {
        this.dependencies = dependencies;
    }

    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        return buildHealth();
    }

    @GetMapping("/ready")
    public ResponseEntity<Map<String, Object>> ready() {
        return buildHealth();
    }

    private ResponseEntity<Map<String, Object>> buildHealth() {
        String status = "healthy";
        Map<String, String> deps = new LinkedHashMap<>();

        for (DependencyPinger dep : dependencies) {
            try {
                dep.ping();
                deps.put(dep.name(), "up");
            } catch (Exception e) {
                deps.put(dep.name(), "down");
                status = "unhealthy";
            }
        }

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("service", "aether-guard/target-service-java");
        body.put("status", status);
        body.put("version", "1.0.0");
        body.put("dependencies", deps);

        HttpStatus code = "healthy".equals(status) ? HttpStatus.OK : HttpStatus.SERVICE_UNAVAILABLE;
        return ResponseEntity.status(code).body(body);
    }
}

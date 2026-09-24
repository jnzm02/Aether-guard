package com.aetherguard.targetservice;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Aether-Guard target-service-java — the intentionally "breakable" JVM
 * microservice. A functional analog of the Go {@code services/target-service}:
 * it exposes the same chaos surface and emits Prometheus metrics whose names
 * match the Aether-Guard contract so the agent can monitor and remediate it.
 */
@SpringBootApplication
public class TargetServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(TargetServiceApplication.class, args);
    }
}

package com.aetherguard.targetservice.metrics;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * Records the two golden-signal HTTP meters on every request:
 * <ul>
 *   <li>{@code aether_guard_http_requests_total{method,path,status_code}} — a counter</li>
 *   <li>{@code aether_guard_http_request_duration_seconds{method,path}} — a histogram
 *       whose buckets are pinned by {@link MetricsConfig}</li>
 * </ul>
 *
 * <p>The scrape endpoint ({@code /metrics}) and actuator paths are skipped, so a
 * Prometheus scrape does not inflate the request counters — matching the Go
 * service, which does not wrap {@code /metrics} in its metrics middleware.
 */
@Component
@Order(1)
public class HttpMetricsFilter extends OncePerRequestFilter {

    private final MeterRegistry registry;

    public HttpMetricsFilter(MeterRegistry registry) {
        this.registry = registry;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.equals("/metrics") || path.startsWith("/actuator");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        long start = System.nanoTime();
        try {
            filterChain.doFilter(request, response);
        } finally {
            long elapsedNanos = System.nanoTime() - start;
            String method = request.getMethod();
            String path = request.getRequestURI();
            String status = Integer.toString(response.getStatus());

            registry.counter("aether_guard.http.requests",
                    "method", method,
                    "path", path,
                    "status_code", status).increment();

            Timer.builder(MetricsConfig.HTTP_REQUEST_DURATION)
                    .description("HTTP request latency histogram. SLO: p99 < 200ms.")
                    .tags("method", method, "path", path)
                    .register(registry)
                    .record(elapsedNanos, java.util.concurrent.TimeUnit.NANOSECONDS);
        }
    }
}

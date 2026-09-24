package com.aetherguard.targetservice;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Scrapes {@code GET /metrics} and asserts the exact Prometheus metric names the
 * Aether-Guard agent contract requires are present. Baseline traffic is
 * generated first so the lazily-created HTTP meters are registered.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureObservability
class MetricsScrapeTest {

    @Autowired
    private TestRestTemplate rest;

    @Test
    void scrapeContainsAllContractMetricNames() {
        // Generate traffic so aether_guard_http_* meters exist, and touch chaos
        // so the memleak gauge has a non-trivial lifecycle.
        rest.getForObject("/api/users", String.class);
        rest.getForObject("/api/orders", String.class);
        rest.getForObject("/health", String.class);
        rest.postForObject("/chaos/memleak?mb=1", null, String.class);

        String scrape = rest.getForObject("/metrics", String.class);
        assertThat(scrape).isNotBlank();

        // ── Aether-Guard contract metrics ──────────────────────────────────
        assertThat(scrape).contains("aether_guard_http_requests_total");
        assertThat(scrape).contains("aether_guard_http_request_duration_seconds_bucket");
        assertThat(scrape).contains("aether_guard_http_request_duration_seconds_count");
        assertThat(scrape).contains("aether_guard_http_request_duration_seconds_sum");
        assertThat(scrape).contains("aether_guard_chaos_memleak_bytes_allocated");
        assertThat(scrape).contains("aether_guard_chaos_errors_injected_total");
        assertThat(scrape).contains("aether_guard_chaos_cpu_cores_active");
        assertThat(scrape).contains("aether_guard_db_query_duration_seconds");

        // ── Exact histogram bucket boundaries for HTTP latency ─────────────
        for (String le : new String[]{"0.005", "0.01", "0.025", "0.05", "0.1",
                "0.2", "0.5", "1.0", "2.5", "5.0", "10.0"}) {
            assertThat(scrape)
                    .as("HTTP duration histogram must have le=\"%s\" bucket", le)
                    .contains("aether_guard_http_request_duration_seconds_bucket{")
                    .containsPattern("le=\"" + java.util.regex.Pattern.quote(le) + "\"");
        }

        // ── JVM / process binder metrics ───────────────────────────────────
        assertThat(scrape).contains("jvm_memory_used_bytes");
        assertThat(scrape).containsPattern("jvm_memory_used_bytes\\{[^}]*area=\"heap\"");
        assertThat(scrape).contains("jvm_threads_live_threads");
        assertThat(scrape).contains("process_cpu_seconds_total");
        assertThat(scrape).contains("process_open_fds");
    }
}

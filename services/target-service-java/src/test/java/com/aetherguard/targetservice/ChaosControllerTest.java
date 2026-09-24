package com.aetherguard.targetservice;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/** Exercises the chaos endpoints and their validation. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureObservability
class ChaosControllerTest {

    @Autowired
    private TestRestTemplate rest;

    private static final ParameterizedTypeReference<Map<String, Object>> MAP =
            new ParameterizedTypeReference<>() {
            };

    @Test
    void memleakAccumulatesAndGaugeRises() {
        rest.postForEntity("/chaos/reset", null, Map.class);

        ResponseEntity<Map<String, Object>> first =
                rest.exchange("/chaos/memleak?mb=2", HttpMethod.POST, null, MAP);
        assertThat(first.getStatusCode()).isEqualTo(HttpStatus.OK);
        long afterFirst = ((Number) first.getBody().get("total_leaked_bytes")).longValue();
        assertThat(afterFirst).isEqualTo(2L * 1024 * 1024);

        ResponseEntity<Map<String, Object>> second =
                rest.exchange("/chaos/memleak?mb=3", HttpMethod.POST, null, MAP);
        long afterSecond = ((Number) second.getBody().get("total_leaked_bytes")).longValue();
        assertThat(afterSecond).isEqualTo(5L * 1024 * 1024);
        assertThat(afterSecond).isGreaterThan(afterFirst);

        // The saturation gauge in the scrape reflects the running total.
        String scrape = rest.getForObject("/metrics", String.class);
        double gauge = gaugeValue(scrape, "aether_guard_chaos_memleak_bytes_allocated");
        assertThat(gauge).isGreaterThanOrEqualTo(afterSecond);

        rest.postForEntity("/chaos/reset", null, Map.class);
    }

    @Test
    void memleakAboveMaxRejectedWith400() {
        ResponseEntity<String> resp =
                rest.exchange("/chaos/memleak?mb=4097", HttpMethod.POST, null, String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void errorRateOneAlwaysReturns500AndZeroReturns200() {
        ResponseEntity<String> always =
                rest.exchange("/chaos/error?rate=1.0", HttpMethod.GET, null, String.class);
        assertThat(always.getStatusCode()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);

        ResponseEntity<String> never =
                rest.exchange("/chaos/error?rate=0.0", HttpMethod.GET, null, String.class);
        assertThat(never.getStatusCode()).isEqualTo(HttpStatus.OK);
    }

    @Test
    void errorRateOutOfRangeRejectedWith400() {
        ResponseEntity<String> resp =
                rest.exchange("/chaos/error?rate=1.5", HttpMethod.GET, null, String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    }

    @Test
    void threadLeakRaisesActiveCountAndResetBringsItBack() throws InterruptedException {
        rest.postForEntity("/chaos/reset", null, Map.class);
        int before = Thread.activeCount();

        ResponseEntity<Map<String, Object>> leak =
                rest.exchange("/chaos/thread-leak?count=30&duration=0&hard=false",
                        HttpMethod.POST, null, MAP);
        assertThat(leak.getStatusCode()).isEqualTo(HttpStatus.OK);

        // Give the freshly-spawned threads a moment to register as active.
        int after = 0;
        for (int i = 0; i < 20; i++) {
            after = Thread.activeCount();
            if (after > before) {
                break;
            }
            Thread.sleep(50);
        }
        assertThat(after).isGreaterThan(before);

        // reset() interrupts the interruptible (soft) leaked threads and joins
        // them, so the count comes back down.
        ResponseEntity<Map<String, Object>> reset =
                rest.exchange("/chaos/reset", HttpMethod.POST, null, MAP);
        assertThat(reset.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(((Number) reset.getBody().get("soft_threads_interrupted")).intValue())
                .isGreaterThanOrEqualTo(30);
        assertThat(((Number) reset.getBody().get("hard_threads_still_leaked")).intValue())
                .isEqualTo(0);

        int settled = Thread.activeCount();
        for (int i = 0; i < 40 && settled > before + 5; i++) {
            Thread.sleep(50);
            settled = Thread.activeCount();
        }
        assertThat(settled).isLessThanOrEqualTo(before + 5);
    }

    @Test
    void statusReportsChaosState() {
        ResponseEntity<Map<String, Object>> resp =
                rest.exchange("/chaos/status", HttpMethod.GET, null, MAP);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody()).containsKeys(
                "memory_leaked_bytes", "cpu_cores_active", "threads_leaked", "gc_pressure_active");
    }

    /** Parses a simple (unlabeled) gauge value out of a Prometheus scrape. */
    private static double gaugeValue(String scrape, String metric) {
        for (String line : scrape.split("\n")) {
            if (line.startsWith(metric + " ")) {
                return Double.parseDouble(line.substring(line.lastIndexOf(' ') + 1).trim());
            }
        }
        return -1;
    }
}

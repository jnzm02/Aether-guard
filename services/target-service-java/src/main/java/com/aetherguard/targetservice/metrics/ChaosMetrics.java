package com.aetherguard.targetservice.metrics;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicLong;

/**
 * Owns the chaos-specific Prometheus meters and exposes the exact contract
 * names. Gauges are registered eagerly at startup so they appear in the scrape
 * even before any chaos has been injected (a value of 0), matching the Go
 * service where {@code promauto} pre-registers everything.
 */
@Component
public class ChaosMetrics {

    private final MeterRegistry registry;

    /** Backing value for {@code aether_guard_chaos_memleak_bytes_allocated}. */
    private final AtomicLong memleakBytes = new AtomicLong(0);
    /** Backing value for {@code aether_guard_chaos_cpu_cores_active}. */
    private final AtomicLong cpuCoresActive = new AtomicLong(0);

    public ChaosMetrics(MeterRegistry registry) {
        this.registry = registry;
    }

    @PostConstruct
    void registerGauges() {
        // aether_guard_chaos_memleak_bytes_allocated (no labels).
        Gauge.builder("aether_guard.chaos.memleak.bytes.allocated", memleakBytes, AtomicLong::doubleValue)
                .description("Total bytes intentionally leaked by the chaos memory-leak endpoint.")
                .register(registry);

        // aether_guard_chaos_cpu_cores_active (no labels).
        Gauge.builder("aether_guard.chaos.cpu.cores.active", cpuCoresActive, AtomicLong::doubleValue)
                .description("Number of threads currently burning CPU via the chaos/cpu endpoint.")
                .register(registry);
    }

    /** Sets the memory-leak saturation gauge to an absolute byte total. */
    public void setMemleakBytes(long total) {
        memleakBytes.set(total);
    }

    public long memleakBytes() {
        return memleakBytes.get();
    }

    /** Sets the active-CPU-cores gauge. */
    public void setCpuCoresActive(long cores) {
        cpuCoresActive.set(cores);
    }

    public long cpuCoresActive() {
        return cpuCoresActive.get();
    }

    /** Increments {@code aether_guard_chaos_errors_injected_total{type=...}}. */
    public void incErrorInjected(String type) {
        registry.counter("aether_guard.chaos.errors.injected", "type", type).increment();
    }

    /** Records an observation into {@code aether_guard_chaos_latency_injected_seconds}. */
    public void recordInjectedLatency(long millis) {
        Timer.builder(MetricsConfig.CHAOS_LATENCY_INJECTED)
                .description("Distribution of artificial latency injected by the chaos latency endpoint.")
                .register(registry)
                .record(java.time.Duration.ofMillis(millis));
    }
}

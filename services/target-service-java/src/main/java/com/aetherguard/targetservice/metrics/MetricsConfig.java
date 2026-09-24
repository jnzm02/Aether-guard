package com.aetherguard.targetservice.metrics;

import com.sun.management.OperatingSystemMXBean;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.config.MeterFilter;
import io.micrometer.core.instrument.distribution.DistributionStatisticConfig;
import io.micrometer.core.instrument.Meter;
import org.springframework.boot.actuate.autoconfigure.metrics.MeterRegistryCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.lang.management.ManagementFactory;
import java.time.Duration;

/**
 * Central Micrometer configuration that makes the Prometheus scrape match the
 * Aether-Guard metric contract EXACTLY.
 *
 * <p>Micrometer maps dotted meter names to Prometheus snake_case and adds
 * suffixes: counters get {@code _total}, timers get {@code _seconds} plus
 * {@code _bucket}/{@code _count}/{@code _sum}. The dotted names chosen here are
 * therefore the pre-image of the required Prometheus names, e.g.
 * {@code aether_guard.http.request.duration} → {@code aether_guard_http_request_duration_seconds}.
 */
@Configuration
public class MetricsConfig {

    /** Meter name for the HTTP request-duration histogram. */
    public static final String HTTP_REQUEST_DURATION = "aether_guard.http.request.duration";
    /** Meter name for the chaos latency-injection histogram. */
    public static final String CHAOS_LATENCY_INJECTED = "aether_guard.chaos.latency.injected";

    /**
     * Explicit histogram buckets for {@code aether_guard_http_request_duration_seconds}.
     * Values are in SECONDS and must match the Go service's histogram exactly.
     */
    private static final double[] HTTP_SLO_SECONDS = {
            0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.5, 5.0, 10.0
    };

    /** Chaos latency buckets, mirroring the Go service for parity. */
    private static final double[] CHAOS_LATENCY_SLO_SECONDS = {
            0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0
    };

    /**
     * Applies fixed histogram buckets to the two timers that need them. Using a
     * {@link MeterFilter} means every tag combination of the timer inherits the
     * same buckets, and Micrometer emits {@code _bucket{le=...}} lines with the
     * exact boundaries above (in seconds, since the timer base unit is seconds).
     */
    @Bean
    public MeterRegistryCustomizer<MeterRegistry> histogramBuckets() {
        return registry -> registry.config().meterFilter(new MeterFilter() {
            @Override
            public DistributionStatisticConfig configure(Meter.Id id, DistributionStatisticConfig config) {
                if (HTTP_REQUEST_DURATION.equals(id.getName())) {
                    return DistributionStatisticConfig.builder()
                            .serviceLevelObjectives(toNanos(HTTP_SLO_SECONDS))
                            .build()
                            .merge(config);
                }
                if (CHAOS_LATENCY_INJECTED.equals(id.getName())) {
                    return DistributionStatisticConfig.builder()
                            .serviceLevelObjectives(toNanos(CHAOS_LATENCY_SLO_SECONDS))
                            .build()
                            .merge(config);
                }
                return config;
            }
        });
    }

    /**
     * Registers {@code process_cpu_seconds_total} and {@code process_open_fds}
     * explicitly.
     *
     * <p>Micrometer's own binders emit {@code process_cpu_usage} (a gauge), not
     * the Prometheus-standard {@code process_cpu_seconds_total} counter, and file
     * descriptor coverage varies by client version. Publishing these two meters
     * ourselves — sourced from the JVM {@link OperatingSystemMXBean} — guarantees
     * the contract names appear in the scrape on every platform.
     */
    @Bean
    public MeterBinderRegistrar processMetrics(MeterRegistry registry) {
        OperatingSystemMXBean os =
                (OperatingSystemMXBean) ManagementFactory.getOperatingSystemMXBean();

        // process_cpu_seconds_total: monotonically increasing CPU seconds.
        // FunctionCounter with dotted name -> Prometheus "<name>_total".
        io.micrometer.core.instrument.FunctionCounter
                .builder("process.cpu.seconds", os,
                        b -> {
                            long ns = b.getProcessCpuTime();
                            return ns < 0 ? 0.0 : ns / 1_000_000_000.0;
                        })
                .description("Total user and system CPU time spent in seconds.")
                .register(registry);

        // process_open_fds: current open file descriptor count (Unix only).
        Gauge.builder("process.open.fds", os, MetricsConfig::openFileDescriptors)
                .description("Number of open file descriptors.")
                .register(registry);

        return new MeterBinderRegistrar();
    }

    private static double openFileDescriptors(OperatingSystemMXBean os) {
        if (os instanceof com.sun.management.UnixOperatingSystemMXBean unix) {
            long fds = unix.getOpenFileDescriptorCount();
            return fds < 0 ? Double.NaN : (double) fds;
        }
        return Double.NaN;
    }

    private static double[] toNanos(double[] seconds) {
        double[] nanos = new double[seconds.length];
        for (int i = 0; i < seconds.length; i++) {
            nanos[i] = seconds[i] * 1_000_000_000.0;
        }
        return nanos;
    }

    /** Marker bean so the {@code processMetrics} registration is eager. */
    public static final class MeterBinderRegistrar {
    }
}

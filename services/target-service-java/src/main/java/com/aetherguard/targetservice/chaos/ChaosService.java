package com.aetherguard.targetservice.chaos;

import com.aetherguard.targetservice.metrics.ChaosMetrics;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Holds all chaos state and performs the failure-mode injections. This is the
 * JVM analog of the Go service's {@code internal/chaos} package.
 *
 * <p>Two deliberate JVM-specific behaviours vs. Go:
 * <ul>
 *   <li>{@code /chaos/memleak} caps at 4096 MB (Go caps at 500) so a single call
 *       can exceed the agent's 1&nbsp;GB MEMORY_LEAK gate.</li>
 *   <li>{@code /chaos/thread-leak} distinguishes interruptible ("soft") threads,
 *       which {@code /chaos/reset} can actually reclaim, from "hard" threads that
 *       ignore interrupts and survive until a restart (preserving RESTART-recovery
 *       semantics).</li>
 * </ul>
 */
@Service
public class ChaosService {

    private final ChaosMetrics metrics;

    // ── Memory leak state ────────────────────────────────────────────────────
    private final List<byte[]> leakStore = new ArrayList<>();
    private final AtomicLong totalLeakedBytes = new AtomicLong(0);
    private final Object leakLock = new Object();

    // ── CPU spike state ──────────────────────────────────────────────────────
    private final List<Thread> cpuThreads = new CopyOnWriteArrayList<>();
    private volatile boolean cpuStop = false;

    // ── Thread leak state ────────────────────────────────────────────────────
    private final List<Thread> softLeakedThreads = new CopyOnWriteArrayList<>();
    private final AtomicInteger hardLeakedThreads = new AtomicInteger(0);

    // ── GC pressure state ────────────────────────────────────────────────────
    private final AtomicBoolean gcPressureActive = new AtomicBoolean(false);

    public ChaosService(ChaosMetrics metrics) {
        this.metrics = metrics;
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Memory leak
    // ──────────────────────────────────────────────────────────────────────────

    public Map<String, Object> memleak(int mb) {
        byte[] chunk = new byte[mb * 1024 * 1024];
        // Touch every page so physical memory is actually committed.
        for (int i = 0; i < chunk.length; i += 4096) {
            chunk[i] = (byte) i;
        }
        long total;
        synchronized (leakLock) {
            leakStore.add(chunk);
            total = totalLeakedBytes.addAndGet((long) mb * 1024 * 1024);
        }
        metrics.setMemleakBytes(total);
        metrics.incErrorInjected("memleak");

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("event", "leak_injected");
        body.put("mb_this_call", mb);
        body.put("total_leaked_bytes", total);
        body.put("total_leaked_mb", total / (1024 * 1024));
        return body;
    }

    // ──────────────────────────────────────────────────────────────────────────
    // CPU spike
    // ──────────────────────────────────────────────────────────────────────────

    public Map<String, Object> cpuSpike(int cores, int ms) {
        stopCpu(); // cancel any running spike first
        cpuStop = false;
        for (int i = 0; i < cores; i++) {
            Thread t = new Thread(() -> burnCpu(ms), "chaos-cpu-" + i);
            t.setDaemon(true);
            cpuThreads.add(t);
            t.start();
        }
        metrics.setCpuCoresActive(cores);
        metrics.incErrorInjected("cpu_spike");

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("event", "cpu_spike_injected");
        body.put("cores", cores);
        body.put("duration_ms", ms);
        return body;
    }

    private void burnCpu(int ms) {
        long deadline = System.currentTimeMillis() + ms;
        long sink = 0;
        try {
            while (!cpuStop && System.currentTimeMillis() < deadline) {
                for (int i = 0; i < 1_000_000; i++) {
                    sink ^= i * 6364136223846793005L + 1442695040888963407L;
                }
                if (sink == Long.MIN_VALUE) {
                    System.out.print(""); // defeat dead-code elimination
                }
            }
        } finally {
            // Last thread out clears the gauge.
            cpuThreads.removeIf(t -> t == Thread.currentThread());
            if (cpuThreads.isEmpty()) {
                metrics.setCpuCoresActive(0);
            }
        }
    }

    private void stopCpu() {
        cpuStop = true;
        for (Thread t : cpuThreads) {
            t.interrupt();
        }
        cpuThreads.clear();
        metrics.setCpuCoresActive(0);
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Latency
    // ──────────────────────────────────────────────────────────────────────────

    public Map<String, Object> latency(int ms) {
        long start = System.currentTimeMillis();
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        long actual = System.currentTimeMillis() - start;
        metrics.recordInjectedLatency(actual);
        metrics.incErrorInjected("latency_spike");

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("event", "latency_injected");
        body.put("delay_ms", ms);
        body.put("actual_ms", actual);
        return body;
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Error injection
    // ──────────────────────────────────────────────────────────────────────────

    private static final String[] ERROR_MESSAGES = {
            "database connection pool exhausted after 30s wait",
            "upstream payment-service: context deadline exceeded (timeout=5s)",
            "NullPointerException in OrderProcessor.commit()",
            "redis cluster: CLUSTERDOWN — hash slot not served",
            "OutOfMemoryError: Java heap space",
            "org.postgresql.util.PSQLException: too many connections for role 'app_user'",
            "io.grpc.StatusRuntimeException: UNAVAILABLE: transport is closing",
    };

    /** @return true when this call should return HTTP 500. */
    public boolean shouldInjectError(double rate) {
        if (ThreadLocalRandom.current().nextDouble() < rate) {
            metrics.incErrorInjected("http_500");
            return true;
        }
        return false;
    }

    public Map<String, Object> errorBody() {
        String msg = ERROR_MESSAGES[ThreadLocalRandom.current().nextInt(ERROR_MESSAGES.length)];
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", msg);
        body.put("code", 500);
        body.put("service", "aether-guard/target-service-java");
        return body;
    }

    public Map<String, Object> noErrorBody(double rate) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "no_error_this_time");
        body.put("configured_rate", rate);
        return body;
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Thread leak
    // ──────────────────────────────────────────────────────────────────────────

    public Map<String, Object> threadLeak(int count, int durationSeconds, boolean hard) {
        for (int i = 0; i < count; i++) {
            if (hard) {
                Thread t = new Thread(this::hardLeakLoop, "chaos-hardleak-" + i);
                t.setDaemon(false); // survive until JVM restart
                hardLeakedThreads.incrementAndGet();
                t.start();
            } else {
                Thread t = new Thread(() -> softLeakLoop(durationSeconds), "chaos-softleak-" + i);
                t.setDaemon(true);
                softLeakedThreads.add(t);
                t.start();
            }
        }
        metrics.incErrorInjected("thread_leak");

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("event", "thread_leak_injected");
        body.put("count", count);
        body.put("hard", hard);
        body.put("duration_seconds", durationSeconds);
        body.put("soft_threads_leaked", softLeakedThreads.size());
        body.put("hard_threads_leaked", hardLeakedThreads.get());
        return body;
    }

    private void hardLeakLoop() {
        // Uninterruptible: swallow interrupts so only a restart clears this.
        while (true) {
            try {
                Thread.sleep(60_000);
            } catch (InterruptedException e) {
                // Intentionally ignored — this thread does not honour interrupt.
            }
        }
    }

    private void softLeakLoop(int durationSeconds) {
        try {
            if (durationSeconds <= 0) {
                // Park until interrupted (by /chaos/reset).
                while (!Thread.currentThread().isInterrupted()) {
                    Thread.sleep(60_000);
                }
            } else {
                Thread.sleep((long) durationSeconds * 1000);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } finally {
            softLeakedThreads.remove(Thread.currentThread());
        }
    }

    // ──────────────────────────────────────────────────────────────────────────
    // GC pressure
    // ──────────────────────────────────────────────────────────────────────────

    public Map<String, Object> gcPressure(int durationSeconds) {
        gcPressureActive.set(true);
        Thread t = new Thread(() -> {
            long deadline = System.currentTimeMillis() + (long) durationSeconds * 1000;
            try {
                java.util.List<byte[]> churn = new java.util.ArrayList<>();
                while (System.currentTimeMillis() < deadline) {
                    // Allocate ~1MB and discard rapidly to drive GC activity up.
                    churn.add(new byte[1024 * 1024]);
                    if (churn.size() > 200) {
                        churn.clear();
                    }
                }
            } finally {
                gcPressureActive.set(false);
            }
        }, "chaos-gc-pressure");
        t.setDaemon(true);
        t.start();
        metrics.incErrorInjected("gc_pressure");

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("event", "gc_pressure_injected");
        body.put("duration_seconds", durationSeconds);
        return body;
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Status & reset
    // ──────────────────────────────────────────────────────────────────────────

    public Map<String, Object> status() {
        long bytes = totalLeakedBytes.get();
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("memory_leak_active", bytes > 0);
        body.put("memory_leaked_bytes", bytes);
        body.put("memory_leaked_mb", bytes / (1024 * 1024));
        body.put("cpu_cores_active", metrics.cpuCoresActive());
        body.put("threads_leaked", softLeakedThreads.size() + hardLeakedThreads.get());
        body.put("soft_threads_leaked", softLeakedThreads.size());
        body.put("hard_threads_leaked", hardLeakedThreads.get());
        body.put("gc_pressure_active", gcPressureActive.get());
        return body;
    }

    public Map<String, Object> reset() {
        long freed;
        synchronized (leakLock) {
            freed = totalLeakedBytes.get();
            leakStore.clear(); // drop references → eligible for GC
            totalLeakedBytes.set(0);
        }
        metrics.setMemleakBytes(0);

        stopCpu();

        // Interrupt the soft (interruptible) leaked threads — the JVM improvement
        // over Go, where leaked goroutines could only be cleared by a restart.
        List<Thread> soft = new ArrayList<>(softLeakedThreads);
        for (Thread t : soft) {
            t.interrupt();
        }
        for (Thread t : soft) {
            try {
                t.join(2000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        int softInterrupted = soft.size();
        int hardStill = hardLeakedThreads.get();

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "reset");
        body.put("freed_bytes", freed);
        body.put("freed_mb", freed / (1024 * 1024));
        body.put("soft_threads_interrupted", softInterrupted);
        body.put("hard_threads_still_leaked", hardStill);
        return body;
    }
}

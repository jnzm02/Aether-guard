package com.aetherguard.targetservice.health;

/**
 * A health-checkable external dependency (Postgres, Redis, ...). Mirrors the Go
 * service's {@code DependencyPinger} interface so tests can inject a failing
 * dependency and assert the health endpoint flips to 503, while an absent
 * (null / unconfigured) dependency is never pinged.
 */
public interface DependencyPinger {

    /** A short, stable name used as the key in the health {@code dependencies} map. */
    String name();

    /**
     * Pings the dependency.
     *
     * @throws Exception if the dependency is unreachable / unhealthy
     */
    void ping() throws Exception;
}

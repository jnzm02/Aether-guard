package com.aetherguard.targetservice.health;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.ArrayList;
import java.util.List;

/**
 * Builds the list of {@link DependencyPinger}s the health endpoint should check.
 *
 * <p>Postgres and Redis are optional. They are only added when a non-blank URL
 * is configured (via {@code dependencies.postgres.url} /
 * {@code dependencies.redis.url}). By default nothing is configured, so the list
 * is empty and {@code /health} returns 200 with an empty dependency map — and,
 * crucially, no ping is ever attempted on an unconfigured dependency.
 */
@Configuration
public class DependencyConfig {

    @Bean
    @ConfigurationProperties(prefix = "dependencies")
    public DependencyProperties dependencyProperties() {
        return new DependencyProperties();
    }

    @Bean
    public List<DependencyPinger> dependencyPingers(DependencyProperties props) {
        List<DependencyPinger> pingers = new ArrayList<>();
        if (props.getPostgres() != null && isConfigured(props.getPostgres().getUrl())) {
            pingers.add(new TcpDependencyPinger("postgres", props.getPostgres().getUrl()));
        }
        if (props.getRedis() != null && isConfigured(props.getRedis().getUrl())) {
            pingers.add(new TcpDependencyPinger("redis", props.getRedis().getUrl()));
        }
        return pingers;
    }

    private static boolean isConfigured(String url) {
        return url != null && !url.isBlank();
    }

    /** Bound from the {@code dependencies.*} config tree. */
    public static class DependencyProperties {
        private Endpoint postgres = new Endpoint();
        private Endpoint redis = new Endpoint();

        public Endpoint getPostgres() {
            return postgres;
        }

        public void setPostgres(Endpoint postgres) {
            this.postgres = postgres;
        }

        public Endpoint getRedis() {
            return redis;
        }

        public void setRedis(Endpoint redis) {
            this.redis = redis;
        }

        public static class Endpoint {
            private String url = "";

            public String getUrl() {
                return url;
            }

            public void setUrl(String url) {
                this.url = url;
            }
        }
    }
}

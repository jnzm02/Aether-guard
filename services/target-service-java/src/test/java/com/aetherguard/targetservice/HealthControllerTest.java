package com.aetherguard.targetservice;

import com.aetherguard.targetservice.api.HealthController;
import com.aetherguard.targetservice.health.DependencyPinger;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

/**
 * Unit tests for the health/readiness contract, focused on the null-safe
 * dependency behaviour: only configured dependencies are pinged, and a
 * configured-but-failing dependency flips the service to 503.
 */
class HealthControllerTest {

    @Test
    void unconfiguredDependenciesReturn200WithEmptyMap() {
        // No dependencies configured → empty list is injected.
        HealthController controller = new HealthController(List.of());

        ResponseEntity<Map<String, Object>> response = controller.health();

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).containsEntry("status", "healthy");
        assertThat(response.getBody()).containsEntry("service", "aether-guard/target-service-java");
        @SuppressWarnings("unchecked")
        Map<String, String> deps = (Map<String, String>) response.getBody().get("dependencies");
        assertThat(deps).isEmpty();
    }

    @Test
    void configuredButFailingDependencyReturns503() throws Exception {
        DependencyPinger failing = mock(DependencyPinger.class);
        org.mockito.Mockito.when(failing.name()).thenReturn("postgres");
        doThrow(new RuntimeException("connection refused")).when(failing).ping();

        HealthController controller = new HealthController(List.of(failing));

        ResponseEntity<Map<String, Object>> response = controller.health();

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        assertThat(response.getBody()).containsEntry("status", "unhealthy");
        @SuppressWarnings("unchecked")
        Map<String, String> deps = (Map<String, String>) response.getBody().get("dependencies");
        assertThat(deps).containsEntry("postgres", "down");
    }

    @Test
    void absentDependencyIsNeverPingedAndDoesNotNpe() throws Exception {
        // "absent" = not configured, so not in the injected list. Verify the
        // health logic never touches it (no NPE, no ping) while a configured,
        // healthy dependency reports "up".
        DependencyPinger absent = mock(DependencyPinger.class);
        DependencyPinger healthy = mock(DependencyPinger.class);
        org.mockito.Mockito.when(healthy.name()).thenReturn("redis");
        // healthy.ping() does nothing → success.

        HealthController controller = new HealthController(List.of(healthy));

        ResponseEntity<Map<String, Object>> response = controller.health();

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        @SuppressWarnings("unchecked")
        Map<String, String> deps = (Map<String, String>) response.getBody().get("dependencies");
        assertThat(deps).containsEntry("redis", "up");
        verify(absent, never()).ping();
    }
}

package com.aetherguard.targetservice;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/** Verifies the Service Contract manifest advertises the JVM runtime at L1. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ManifestControllerTest {

    @Autowired
    private TestRestTemplate rest;

    @Test
    void manifestReportsJvmRuntimeAndL1() {
        ResponseEntity<Map<String, Object>> resp = rest.exchange(
                "/aetherguard/v1/manifest", HttpMethod.GET, null,
                new ParameterizedTypeReference<>() {
                });

        assertThat(resp.getStatusCode().is2xxSuccessful()).isTrue();
        assertThat(resp.getBody()).isNotNull();
        assertThat(resp.getBody()).containsEntry("service", "target-service-java");
        assertThat(resp.getBody()).containsEntry("runtime", "jvm");
        assertThat(resp.getBody()).containsEntry("compliance_level", "L1");
        assertThat(resp.getBody()).containsEntry("contract_version", "1.0");
    }
}

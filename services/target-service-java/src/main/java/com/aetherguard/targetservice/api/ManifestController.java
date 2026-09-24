package com.aetherguard.targetservice.api;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Service Contract v1.0 manifest. L1 compliance: declares observability, no
 * remediation actions. Adds {@code "runtime":"jvm"} so the agent can select
 * JVM-aware RCA patterns.
 */
@RestController
public class ManifestController {

    @GetMapping("/aetherguard/v1/manifest")
    public Map<String, Object> manifest() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("service", "target-service-java");
        body.put("version", "1.0.0");
        body.put("contract_version", "1.0");
        body.put("compliance_level", "L1");
        body.put("owner", "aether-guard");
        body.put("runbook", "");
        body.put("actions", List.of());
        body.put("runtime", "jvm");
        return body;
    }
}

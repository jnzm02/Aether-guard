package com.aetherguard.targetservice.api.model;

import com.fasterxml.jackson.annotation.JsonProperty;

/** A seeded demo order. Field names are snake_case in JSON for parity with Go. */
public record Order(
        int id,
        @JsonProperty("user_id") int userId,
        @JsonProperty("user_name") String userName,
        String product,
        double total,
        String status) {
}

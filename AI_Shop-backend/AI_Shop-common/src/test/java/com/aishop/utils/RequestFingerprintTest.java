package com.aishop.utils;

import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

class RequestFingerprintTest {

    @Test
    void objectKeyOrderDoesNotChangeFingerprint() {
        Map<String, Object> first = new LinkedHashMap<>();
        first.put("addressId", "a1");
        first.put("items", List.of(Map.of("productId", "p1", "count", 2)));

        Map<String, Object> second = new LinkedHashMap<>();
        second.put("items", List.of(Map.of("count", 2, "productId", "p1")));
        second.put("addressId", "a1");

        assertEquals(RequestFingerprint.sha256(first), RequestFingerprint.sha256(second));
    }

    @Test
    void changedPayloadChangesFingerprint() {
        assertNotEquals(
                RequestFingerprint.sha256(Map.of("count", 1)),
                RequestFingerprint.sha256(Map.of("count", 2)));
    }
}

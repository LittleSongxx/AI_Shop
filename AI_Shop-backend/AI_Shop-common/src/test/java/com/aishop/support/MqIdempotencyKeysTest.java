package com.aishop.support;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class MqIdempotencyKeysTest {

    @Test
    void repeatedBrowseEventsUseTheirEventTime() {
        assertNotEquals(
                MqIdempotencyKeys.browseRecord("u1", "p1", 1_000L),
                MqIdempotencyKeys.browseRecord("u1", "p1", 2_000L));
    }

    @Test
    void browseEventRequiresAValidTime() {
        assertThrows(
                IllegalArgumentException.class,
                () -> MqIdempotencyKeys.browseRecord("u1", "p1", 0L));
    }
}

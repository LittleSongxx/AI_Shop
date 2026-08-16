package com.aishop.redis;

import org.junit.jupiter.api.Test;
import org.redisson.config.Config;
import org.redisson.config.SingleServerConfig;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class RedissionConfigTest {

    @Test
    void appliesAclDatabaseAndTlsSettings() {
        RedissionConfig configuration = new RedissionConfig(
                "redis.internal",
                6380,
                "aishop",
                "p@ss:/?#[]",
                3,
                true);

        Config config = configuration.buildConfig();
        SingleServerConfig server = config.useSingleServer();

        assertEquals("rediss://redis.internal:6380", server.getAddress());
        assertEquals("aishop", config.getUsername());
        assertEquals("p@ss:/?#[]", config.getPassword());
        assertEquals(3, server.getDatabase());
    }

    @Test
    void leavesOptionalCredentialsUnsetForLegacyLocalRedis() {
        RedissionConfig configuration = new RedissionConfig(
                "127.0.0.1",
                6380,
                "",
                "",
                0,
                false);

        Config config = configuration.buildConfig();
        SingleServerConfig server = config.useSingleServer();

        assertEquals("redis://127.0.0.1:6380", server.getAddress());
        assertNull(config.getUsername());
        assertNull(config.getPassword());
        assertEquals(0, server.getDatabase());
    }
}

package com.aishop.cloud.gateway.filter;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.aishop.cloud.gateway.config.GatewayAuthProperties;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.data.redis.core.ReactiveValueOperations;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AuthGlobalFilterTest {

    @Mock
    private ReactiveStringRedisTemplate redisTemplate;
    @Mock
    private ReactiveValueOperations<String, String> valueOperations;

    private GatewayAuthProperties properties;
    private AuthGlobalFilter filter;

    @BeforeEach
    void setUp() {
        properties = new GatewayAuthProperties();
        properties.setEnabled(true);
        filter = new AuthGlobalFilter(properties, redisTemplate, new ObjectMapper());
    }

    @Test
    void authenticatedRequestOverwritesSpoofedUserIdentityHeaders() {
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.get("mall:token:web:valid-token"))
                .thenReturn(Mono.just("{\"userId\":\"trusted-user\"}"));
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/orders")
                        .header("token", "valid-token")
                        .header("X-User-Id", "attacker")
                        .header("X-User-Token-Verified", "forged")
                        .header("X-Admin-Token-Verified", "forged")
                        .build());
        AtomicReference<ServerWebExchange> forwarded = new AtomicReference<>();

        filter.filter(exchange, capture(forwarded)).block();

        assertEquals("trusted-user", forwarded.get().getRequest().getHeaders().getFirst("X-User-Id"));
        assertEquals(List.of("1"), forwarded.get().getRequest().getHeaders().get("X-User-Token-Verified"));
        assertNull(forwarded.get().getRequest().getHeaders().getFirst("X-Admin-Token-Verified"));
    }

    @Test
    void exactWebSocketPathIsStillSessionAuthenticated() {
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.get("mall:token:web:valid-token"))
                .thenReturn(Mono.just("{\"userId\":\"trusted-user\"}"));
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/ws")
                        .header("token", "valid-token")
                        .build());
        AtomicReference<ServerWebExchange> forwarded = new AtomicReference<>();

        filter.filter(exchange, capture(forwarded)).block();

        assertEquals("trusted-user", forwarded.get().getRequest().getHeaders().getFirst("X-User-Id"));
        assertEquals(List.of("1"), forwarded.get().getRequest().getHeaders().get("X-User-Token-Verified"));
    }

    @Test
    void publicEndpointStillDropsClientSuppliedIdentityHeaders() {
        properties.setWebExcludePaths(List.of("/api/public/**"));
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/public/catalog")
                        .header("X-User-Id", "attacker")
                        .header("X-User-Token-Verified", "forged")
                        .build());
        AtomicReference<ServerWebExchange> forwarded = new AtomicReference<>();

        filter.filter(exchange, capture(forwarded)).block();

        assertNull(forwarded.get().getRequest().getHeaders().getFirst("X-User-Id"));
        assertNull(forwarded.get().getRequest().getHeaders().getFirst("X-User-Token-Verified"));
    }

    private static GatewayFilterChain capture(AtomicReference<ServerWebExchange> forwarded) {
        return exchange -> {
            forwarded.set(exchange);
            return Mono.empty();
        };
    }
}

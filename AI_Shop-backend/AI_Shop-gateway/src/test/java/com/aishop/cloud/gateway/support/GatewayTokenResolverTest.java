package com.aishop.cloud.gateway.support;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpCookie;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class GatewayTokenResolverTest {

    @Test
    void queryStringTokenIsNeverAccepted() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/orders?token=leaked-token").build());

        assertNull(GatewayTokenResolver.resolveWebToken(exchange));
    }

    @Test
    void headerTakesPrecedenceOverCookie() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/orders")
                        .header("token", " header-token ")
                        .cookie(new HttpCookie("token", "cookie-token"))
                        .build());

        assertEquals("header-token", GatewayTokenResolver.resolveWebToken(exchange));
    }
}

package com.aishop.integration;

import com.aishop.entity.po.ProductItem;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClient;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RecommendationAttributionClientTest {

    private HttpServer server;

    @AfterEach
    void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void appliesOnlyCanonicalServerFields() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/internal/attribution/validateBatch", exchange -> {
            assertEquals("internal-test", exchange.getRequestHeaders().getFirst("X-Internal-Token"));
            String requestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            assertTrue(requestBody.contains("\"userId\":\"u1\""));
            assertTrue(requestBody.contains("\"requestId\":\"request-1\""));
            respond(exchange, """
                    {"status":"success","code":200,"data":[{
                      "requestId":"request-1","productId":"p1","position":2,
                      "source":"hybrid","occurredAt":"2026-08-06T09:00:00.123"
                    }]}
                    """);
        });
        server.start();

        ProductItem item = candidate();
        item.setAiSource("forged-source");
        item.setAiAttributedAt(new Date(1));
        client(server.getAddress().getPort()).validateAndApply("u1", List.of(item));

        assertEquals("request-1", item.getAiRequestId());
        assertEquals(2, item.getAiPosition());
        assertEquals("hybrid", item.getAiSource());
        assertNotNull(item.getAiAttributedAt());
    }

    @Test
    void unavailableAgentClearsOptionalAttributionWithoutThrowing() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        int unusedPort = server.getAddress().getPort();
        server.stop(0);
        server = null;

        ProductItem item = candidate();
        item.setAiSource("forged-source");
        item.setAiAttributedAt(new Date());

        client(unusedPort).validateAndApply("u1", List.of(item));

        assertNull(item.getAiRequestId());
        assertNull(item.getAiPosition());
        assertNull(item.getAiSource());
        assertNull(item.getAiAttributedAt());
    }

    private static ProductItem candidate() {
        ProductItem item = new ProductItem();
        item.setProductId("p1");
        item.setAiRequestId("request-1");
        item.setAiPosition(2);
        return item;
    }

    private static RecommendationAttributionClient client(int port) {
        return new RecommendationAttributionClient(
                RestClient.builder(),
                "http://127.0.0.1:" + port,
                "internal-test",
                100,
                200);
    }

    private static void respond(HttpExchange exchange, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(200, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }
}

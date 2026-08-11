package com.aishop.integration;

import com.aishop.entity.po.ProductItem;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionSynchronizationUtils;
import org.springframework.web.client.RestClient;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CommerceOutcomeClientTest {

    private HttpServer server;

    @AfterEach
    void tearDown() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void emitsCanonicalOutcomeOnlyAfterCommit() throws Exception {
        AtomicInteger requests = new AtomicInteger();
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/internal/commerce-outcomes/ingestBatch", exchange -> {
            assertEquals("internal-test", exchange.getRequestHeaders().getFirst("X-Internal-Token"));
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            assertTrue(body.contains("\"eventType\":\"ADD_TO_CART\""));
            assertTrue(body.contains("\"requestId\":\"request-1\""));
            assertTrue(body.contains("\"quantity\":2"));
            requests.incrementAndGet();
            respond(exchange, "{\"status\":\"success\",\"code\":200,\"data\":[]}");
        });
        server.start();

        ProductItem item = new ProductItem();
        item.setProductId("p1");
        item.setAiRequestId("request-1");
        item.setAiPosition(2);
        CommerceOutcomeClient.OutcomeEvent event = CommerceOutcomeClient.fromVerifiedCarrier(
                "event-1", "CART", "cart-1", "ADD_TO_CART", "u1", item,
                "sku-1", null, Map.of("quantity", 2), new Date());

        TransactionSynchronizationManager.initSynchronization();
        client(server.getAddress().getPort()).recordAfterCommit(event);

        assertEquals(0, requests.get());
        TransactionSynchronizationUtils.invokeAfterCommit(
                TransactionSynchronizationManager.getSynchronizations());
        assertEquals(1, requests.get());
    }

    @Test
    void incompleteTouchpointIsReportedAsUnattributed() {
        ProductItem item = new ProductItem();
        item.setProductId("p1");
        item.setAiRequestId("request-1");

        CommerceOutcomeClient.OutcomeEvent event = CommerceOutcomeClient.fromVerifiedCarrier(
                "event-1", "CART", "cart-1", "ADD_TO_CART", "u1", item,
                "sku-1", null, Map.of(), new Date());

        assertNull(event.requestId());
        assertNull(event.position());
        assertFalse(event.payload().containsKey("address"));
    }

    @Test
    void unavailableAgentNeverEscapesToBusinessCaller() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        int unusedPort = server.getAddress().getPort();
        server.stop(0);
        server = null;

        client(unusedPort).recordAfterCommit(new CommerceOutcomeClient.OutcomeEvent(
                "event-1", "CART", "cart-1", "ADD_TO_CART", "u1", null,
                "p1", "sku-1", null, null, Map.of("quantity", 1),
                java.time.Instant.now().toString()));
    }

    private static CommerceOutcomeClient client(int port) {
        return new CommerceOutcomeClient(
                RestClient.builder(),
                Runnable::run,
                "http://127.0.0.1:" + port,
                "internal-test",
                100,
                200,
                true);
    }

    private static void respond(HttpExchange exchange, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(200, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }
}

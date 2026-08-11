package com.aishop.integration;

import com.aishop.constants.InternalApiHeaders;
import com.aishop.entity.dto.RecommendationAttributionCarrier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.client.RestClient;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.Collections;
import java.util.Date;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executor;

/**
 * Best-effort projection of committed commerce facts into the Agent outcome ledger.
 *
 * The HTTP projection is deliberately outside the business transaction. It has a
 * dedicated short timeout and no retry, so an Agent outage cannot fail checkout.
 * The immutable business identifiers make a future outbox/MQ transport a transport
 * change rather than a data-contract change.
 */
@Component
public class CommerceOutcomeClient {

    private static final Logger log = LoggerFactory.getLogger(CommerceOutcomeClient.class);
    private static final int MAX_BATCH_SIZE = 100;

    private final RestClient client;
    private final String internalToken;
    private final Executor executor;
    private final boolean enabled;

    public CommerceOutcomeClient(
            RestClient.Builder builder,
            @Qualifier("mqAsyncExecutor") Executor executor,
            @Value("${aishop.agent.base-url:http://127.0.0.1:7050}") String agentBaseUrl,
            @Value("${aishop.internal.token:your-token}") String internalToken,
            @Value("${aishop.agent.outcome-connect-timeout-ms:200}") int connectTimeoutMs,
            @Value("${aishop.agent.outcome-read-timeout-ms:500}") int readTimeoutMs,
            @Value("${aishop.agent.outcome-enabled:true}") boolean enabled) {
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofMillis(Math.max(connectTimeoutMs, 50)));
        requestFactory.setReadTimeout(Duration.ofMillis(Math.max(readTimeoutMs, 50)));
        this.client = builder.clone()
                .baseUrl(agentBaseUrl.replaceAll("/+$", ""))
                .requestFactory(requestFactory)
                .build();
        this.internalToken = internalToken;
        this.executor = executor;
        this.enabled = enabled;
    }

    public void recordAfterCommit(OutcomeEvent event) {
        if (event != null) {
            recordBatchAfterCommit(List.of(event));
        }
    }

    public void recordBatchAfterCommit(List<OutcomeEvent> events) {
        if (!enabled || events == null || events.isEmpty()) {
            return;
        }
        List<OutcomeEvent> snapshot = List.copyOf(events);
        Runnable dispatch = () -> dispatch(snapshot);
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(
                    new TransactionSynchronization() {
                        @Override
                        public void afterCommit() {
                            dispatch.run();
                        }
                    });
            return;
        }
        dispatch.run();
    }

    private void dispatch(List<OutcomeEvent> events) {
        try {
            executor.execute(() -> sendInBatches(events));
        } catch (java.util.concurrent.RejectedExecutionException ex) {
            log.warn("Commerce outcome executor saturated; dropping {} projection(s)", events.size());
        }
    }

    private void sendInBatches(List<OutcomeEvent> events) {
        for (int start = 0; start < events.size(); start += MAX_BATCH_SIZE) {
            int end = Math.min(events.size(), start + MAX_BATCH_SIZE);
            send(events.subList(start, end));
        }
    }

    private void send(List<OutcomeEvent> events) {
        try {
            client.post()
                    .uri("/internal/commerce-outcomes/ingestBatch")
                    .contentType(MediaType.APPLICATION_JSON)
                    .header(InternalApiHeaders.INTERNAL_TOKEN, internalToken)
                    .body(Map.of("events", events))
                    .retrieve()
                    .toBodilessEntity();
        } catch (Exception ex) {
            log.warn("Commerce outcome projection unavailable; dropping {} event(s)", events.size());
        }
    }

    public static OutcomeEvent fromVerifiedCarrier(
            String eventId,
            String source,
            String idempotencyKey,
            String eventType,
            String userId,
            RecommendationAttributionCarrier carrier,
            String skuKey,
            String orderId,
            Map<String, Object> payload,
            Date occurredAt) {
        String requestId = trim(carrier == null ? null : carrier.getAiRequestId());
        Integer position = carrier == null ? null : carrier.getAiPosition();
        if (requestId == null || position == null || position < 1 || position > 20) {
            requestId = null;
            position = null;
        }
        return new OutcomeEvent(
                eventId,
                source,
                idempotencyKey,
                eventType,
                userId,
                requestId,
                carrier == null ? null : trim(carrier.getProductId()),
                trim(skuKey),
                trim(orderId),
                position,
                payload,
                (occurredAt == null ? Instant.now() : occurredAt.toInstant()).toString());
    }

    public static String stableEventId(String namespace, Object... facts) {
        return stableIdentifier("outcome", namespace, facts);
    }

    public static String stableIdempotencyKey(String namespace, Object... facts) {
        return stableIdentifier("business", namespace, facts);
    }

    private static String stableIdentifier(String prefix, String namespace, Object... facts) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(String.valueOf(namespace).getBytes(StandardCharsets.UTF_8));
            for (Object fact : facts) {
                digest.update((byte) 0);
                digest.update(String.valueOf(fact).getBytes(StandardCharsets.UTF_8));
            }
            return prefix + "_" + HexFormat.of().formatHex(digest.digest(), 0, 24);
        } catch (Exception impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }

    private static String trim(String value) {
        if (value == null || value.trim().isEmpty()) {
            return null;
        }
        return value.trim();
    }

    public record OutcomeEvent(
            String eventId,
            String source,
            String idempotencyKey,
            String eventType,
            String userId,
            String requestId,
            String productId,
            String skuKey,
            String orderId,
            Integer position,
            Map<String, Object> payload,
            String occurredAt) {

        public OutcomeEvent {
            Map<String, Object> copy = payload == null
                    ? Map.of()
                    : new LinkedHashMap<>(payload);
            payload = Collections.unmodifiableMap(copy);
        }
    }
}

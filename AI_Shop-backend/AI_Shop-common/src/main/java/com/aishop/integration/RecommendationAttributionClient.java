package com.aishop.integration;

import com.aishop.constants.InternalApiHeaders;
import com.aishop.entity.dto.RecommendationAttributionCarrier;
import com.aishop.entity.vo.ResponseVO;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Best-effort, read-only validation of recommendation touchpoints.
 *
 * This client intentionally has a dedicated short timeout and no retry. A slow or
 * unavailable Agent must only remove optional attribution, never delay or fail a
 * cart/order transaction.
 */
@Component
public class RecommendationAttributionClient {

    private static final Logger log = LoggerFactory.getLogger(RecommendationAttributionClient.class);

    private final RestClient client;
    private final String internalToken;

    public RecommendationAttributionClient(
            RestClient.Builder builder,
            @Value("${aishop.agent.base-url:http://127.0.0.1:7050}") String agentBaseUrl,
            @Value("${aishop.internal.token:your-token}") String internalToken,
            @Value("${aishop.agent.attribution-connect-timeout-ms:200}") int connectTimeoutMs,
            @Value("${aishop.agent.attribution-read-timeout-ms:500}") int readTimeoutMs) {
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofMillis(Math.max(connectTimeoutMs, 50)));
        requestFactory.setReadTimeout(Duration.ofMillis(Math.max(readTimeoutMs, 50)));
        this.client = builder.clone()
                .baseUrl(agentBaseUrl.replaceAll("/+$", ""))
                .requestFactory(requestFactory)
                .build();
        this.internalToken = internalToken;
    }

    public void validateAndApply(
            String userId,
            List<? extends RecommendationAttributionCarrier> carriers) {
        if (carriers == null || carriers.isEmpty()) {
            return;
        }

        Map<String, RecommendationAttributionCarrier> requested = new LinkedHashMap<>();
        List<AttributionItem> items = new ArrayList<>();
        for (RecommendationAttributionCarrier carrier : carriers) {
            if (carrier == null) {
                continue;
            }
            String requestId = trim(carrier.getAiRequestId());
            String productId = trim(carrier.getProductId());
            Integer position = carrier.getAiPosition();
            carrier.clearRecommendationAttribution();
            if (requestId == null || requestId.length() > 128
                    || productId == null || productId.length() > 64
                    || position == null || position < 1 || position > 20) {
                continue;
            }
            AttributionItem item = new AttributionItem(requestId, productId, position);
            String key = key(requestId, productId, position);
            requested.put(key, carrier);
            items.add(item);
        }
        if (trim(userId) == null || items.isEmpty()) {
            return;
        }

        List<ValidatedAttribution> validated = validateBatch(userId, items);
        for (ValidatedAttribution attribution : validated) {
            RecommendationAttributionCarrier carrier = requested.get(
                    key(attribution.requestId(), attribution.productId(), attribution.position()));
            Date occurredAt = parseOccurredAt(attribution.occurredAt());
            String source = trim(attribution.source());
            if (carrier == null || occurredAt == null || source == null || source.length() > 40) {
                continue;
            }
            carrier.setAiRequestId(attribution.requestId());
            carrier.setAiPosition(attribution.position());
            carrier.setAiSource(source);
            carrier.setAiAttributedAt(occurredAt);
        }
    }

    private List<ValidatedAttribution> validateBatch(String userId, List<AttributionItem> items) {
        try {
            Map<String, Object> body = new HashMap<>();
            body.put("userId", userId);
            body.put("items", items);
            ResponseVO<List<ValidatedAttribution>> response = client.post()
                    .uri("/internal/attribution/validateBatch")
                    .contentType(MediaType.APPLICATION_JSON)
                    .header(InternalApiHeaders.INTERNAL_TOKEN, internalToken)
                    .body(body)
                    .retrieve()
                    .body(new ParameterizedTypeReference<ResponseVO<List<ValidatedAttribution>>>() {});
            if (response == null || !"success".equalsIgnoreCase(response.getStatus())
                    || response.getData() == null) {
                return Collections.emptyList();
            }
            return response.getData();
        } catch (Exception ex) {
            log.warn("Recommendation attribution validation unavailable; dropping {} candidate(s)",
                    items.size());
            return Collections.emptyList();
        }
    }

    private static Date parseOccurredAt(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            return Date.from(OffsetDateTime.parse(value).toInstant());
        } catch (Exception ignored) {
            try {
                return Date.from(LocalDateTime.parse(value)
                        .atZone(ZoneId.systemDefault()).toInstant());
            } catch (Exception invalid) {
                return null;
            }
        }
    }

    private static String trim(String value) {
        if (value == null || value.trim().isEmpty()) {
            return null;
        }
        return value.trim();
    }

    private static String key(String requestId, String productId, Integer position) {
        return requestId + '\0' + productId + '\0' + position;
    }

    private record AttributionItem(String requestId, String productId, Integer position) {
    }

    private record ValidatedAttribution(
            String requestId,
            String productId,
            Integer position,
            String source,
            String occurredAt) {
    }
}

package com.aishop.integration;

import com.aishop.constants.InternalApiHeaders;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import com.aishop.utils.JsonUtils;
import com.aishop.utils.RequestFingerprint;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;

@Component
public class PrivacyAgentClient {

    private static final ParameterizedTypeReference<ResponseVO<Object>> OBJECT_RESPONSE =
            new ParameterizedTypeReference<>() { };

    private final RestClient client;
    private final String internalToken;

    public PrivacyAgentClient(
            RestClient.Builder builder,
            @Value("${aishop.agent.base-url:http://127.0.0.1:7050}") String baseUrl,
            @Value("${aishop.internal.token:your-token}") String internalToken,
            @Value("${aishop.agent.privacy-connect-timeout-ms:1000}") int connectTimeoutMs,
            @Value("${aishop.agent.privacy-read-timeout-ms:10000}") int readTimeoutMs) {
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofMillis(Math.max(100, connectTimeoutMs)));
        requestFactory.setReadTimeout(Duration.ofMillis(Math.max(1000, readTimeoutMs)));
        this.client = builder.clone()
                .baseUrl(baseUrl.replaceAll("/+$", ""))
                .requestFactory(requestFactory)
                .build();
        this.internalToken = internalToken;
    }

    public Object createJob(String userId, String jobType, String idempotencyKey) {
        Map<String, Object> fingerprintPayload = Map.of(
                "jobType", jobType,
                "scope", "AI_DOMAIN_V1");
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("userId", userId);
        body.put("jobType", jobType);
        body.put("idempotencyKey", idempotencyKey);
        body.put("requestFingerprint", RequestFingerprint.sha256(fingerprintPayload));
        return post("/internal/privacy/jobs/create", body, OBJECT_RESPONSE);
    }

    public Object listJobs(String userId, int limit) {
        return post(
                "/internal/privacy/jobs/list",
                Map.of("userId", userId, "limit", limit),
                OBJECT_RESPONSE);
    }

    public Object getJob(String userId, String jobId) {
        return post(
                "/internal/privacy/jobs/detail",
                Map.of("userId", userId, "jobId", jobId),
                OBJECT_RESPONSE);
    }

    public Object retryJob(String userId, String jobId) {
        return post(
                "/internal/privacy/jobs/retry",
                Map.of("userId", userId, "jobId", jobId),
                OBJECT_RESPONSE);
    }

    public byte[] downloadExport(String userId, String jobId) {
        try {
            byte[] body = client.post()
                    .uri("/internal/privacy/jobs/download")
                    .contentType(MediaType.APPLICATION_JSON)
                    .header(InternalApiHeaders.INTERNAL_TOKEN, internalToken)
                    .body(Map.of("userId", userId, "jobId", jobId))
                    .retrieve()
                    .body(byte[].class);
            if (body == null) {
                throw new BusinessException(605, "AI 数据导出文件为空");
            }
            return body;
        } catch (RestClientResponseException exception) {
            throw remoteFailure(exception);
        }
    }

    private <T> T post(
            String path,
            Object body,
            ParameterizedTypeReference<ResponseVO<T>> responseType) {
        try {
            ResponseVO<T> response = client.post()
                    .uri(path)
                    .contentType(MediaType.APPLICATION_JSON)
                    .header(InternalApiHeaders.INTERNAL_TOKEN, internalToken)
                    .body(body)
                    .retrieve()
                    .body(responseType);
            if (response == null) {
                throw new BusinessException(605, "Agent 隐私服务无响应");
            }
            if (!"success".equalsIgnoreCase(String.valueOf(response.getStatus()))) {
                throw new BusinessException(
                        response.getCode() == null ? 605 : response.getCode(),
                        response.getInfo() == null ? "Agent 隐私服务调用失败" : response.getInfo());
            }
            return response.getData();
        } catch (RestClientResponseException exception) {
            throw remoteFailure(exception);
        }
    }

    private BusinessException remoteFailure(RestClientResponseException exception) {
        int status = exception.getStatusCode().value();
        String message = "Agent 隐私服务调用失败";
        try {
            Object detail = JsonUtils.mapper()
                    .readTree(exception.getResponseBodyAsByteArray())
                    .get("detail");
            if (detail != null && !String.valueOf(detail).isBlank()) {
                message = String.valueOf(detail).replaceAll("^\"|\"$", "");
            }
        } catch (Exception ignored) {
            // Keep a stable public message when the remote body is not JSON.
        }
        int businessCode = switch (status) {
            case 400, 401, 403, 404, 409, 410 -> status;
            default -> 605;
        };
        return new BusinessException(businessCode, message);
    }
}

package com.aishop.cloud.gateway.filter;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.aishop.cloud.gateway.config.GatewayAuthProperties;
import com.aishop.cloud.gateway.support.GatewayTokenResolver;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.util.AntPathMatcher;
import org.springframework.util.StringUtils;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Component
public class AuthGlobalFilter implements GlobalFilter, Ordered {

    private static final String REDIS_KEY_TOKEN_WEB = "mall:token:web:";
    private static final String REDIS_KEY_TOKEN_ADMIN = "mall:token:admin:";
    private static final int CODE_LOGIN_TIMEOUT = 901;

    private final GatewayAuthProperties authProperties;
    private final ReactiveStringRedisTemplate reactiveStringRedisTemplate;
    private final ObjectMapper objectMapper;
    private final AntPathMatcher pathMatcher = new AntPathMatcher();

    public AuthGlobalFilter(GatewayAuthProperties authProperties,
                            ReactiveStringRedisTemplate reactiveStringRedisTemplate,
                            ObjectMapper objectMapper) {
        this.authProperties = authProperties;
        this.reactiveStringRedisTemplate = reactiveStringRedisTemplate;
        this.objectMapper = objectMapper;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        if (!authProperties.isEnabled()) {
            return chain.filter(exchange);
        }
        ServerHttpRequest request = exchange.getRequest();
        if (HttpMethod.OPTIONS.equals(request.getMethod())) {
            return chain.filter(exchange);
        }
        String path = request.getURI().getPath();
        if (isInfraPath(path) || isInternalPath(path)) {
            return chain.filter(exchange);
        }

        if (path.startsWith("/admin-api/")) {
            if (matchAny(path, authProperties.getAdminExcludePaths())) {
                return chain.filter(exchange);
            }
            String adminToken = GatewayTokenResolver.resolveAdminToken(exchange);
            if (!StringUtils.hasText(adminToken)) {
                return unauthorized(exchange, "登录超时");
            }
            return reactiveStringRedisTemplate.hasKey(REDIS_KEY_TOKEN_ADMIN + adminToken)
                    .flatMap(exists -> {
                        if (Boolean.TRUE.equals(exists)) {
                            ServerHttpRequest mutated = request.mutate()
                                    .header("X-Admin-Token-Verified", "1")
                                    .build();
                            return chain.filter(exchange.mutate().request(mutated).build());
                        }
                        return unauthorized(exchange, "登录超时");
                    });
        }

        if (path.startsWith("/api/") || path.startsWith("/ws/")) {
            if (path.startsWith("/api/") && matchAny(path, authProperties.getWebExcludePaths())) {
                return chain.filter(exchange);
            }
            String token = GatewayTokenResolver.resolveWebToken(exchange);
            if (!StringUtils.hasText(token)) {
                return unauthorized(exchange, "登录超时");
            }
            return reactiveStringRedisTemplate.opsForValue().get(REDIS_KEY_TOKEN_WEB + token)
                    .flatMap(sessionJson -> {
                        if (!StringUtils.hasText(sessionJson)) {
                            return unauthorized(exchange, "登录超时");
                        }
                        String userId = extractUserId(sessionJson);
                        ServerHttpRequest.Builder builder = request.mutate()
                                .header("X-User-Token-Verified", "1");
                        if (StringUtils.hasText(userId)) {
                            builder.header("X-User-Id", userId);
                        }
                        return chain.filter(exchange.mutate().request(builder.build()).build());
                    })
                    .switchIfEmpty(unauthorized(exchange, "登录超时"));
        }

        return chain.filter(exchange);
    }

    private boolean isInfraPath(String path) {
        // 仅放行健康检查，避免整站 actuator 暴露业务细节
        return "/actuator/health".equals(path)
                || "/actuator/health/liveness".equals(path)
                || "/actuator/health/readiness".equals(path)
                || "/favicon.ico".equals(path);
    }

    /** Session auth is skipped; {@link InternalTokenGlobalFilter} validates the token. */
    private boolean isInternalPath(String path) {
        return path != null && path.startsWith("/internal/");
    }

    private boolean matchAny(String path, List<String> patterns) {
        if (patterns == null || patterns.isEmpty()) {
            return false;
        }
        for (String pattern : patterns) {
            if (!StringUtils.hasText(pattern)) {
                continue;
            }
            if (pathMatcher.match(pattern.trim(), path)) {
                return true;
            }
        }
        return false;
    }

    private String extractUserId(String sessionJson) {
        try {
            JsonNode node = objectMapper.readTree(sessionJson);
            JsonNode userId = node.get("userId");
            return userId == null || userId.isNull() ? null : userId.asText();
        } catch (Exception ex) {
            return null;
        }
    }

    private Mono<Void> unauthorized(ServerWebExchange exchange, String msg) {
        exchange.getResponse().setStatusCode(HttpStatus.OK);
        exchange.getResponse().getHeaders().setContentType(MediaType.APPLICATION_JSON);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "error");
        body.put("code", CODE_LOGIN_TIMEOUT);
        body.put("info", msg);
        body.put("data", null);
        byte[] bytes;
        try {
            bytes = objectMapper.writeValueAsBytes(body);
        } catch (JsonProcessingException e) {
            bytes = ("{\"code\":901,\"info\":\"" + msg + "\"}").getBytes(StandardCharsets.UTF_8);
        }
        DataBuffer buffer = exchange.getResponse().bufferFactory().wrap(bytes);
        return exchange.getResponse().writeWith(Mono.just(buffer));
    }

    @Override
    public int getOrder() {
        return -100;
    }
}

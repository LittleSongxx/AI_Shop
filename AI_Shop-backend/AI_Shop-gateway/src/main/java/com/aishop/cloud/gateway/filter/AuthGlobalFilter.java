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
    private static final String USER_ID_HEADER = "X-User-Id";
    private static final String USER_VERIFIED_HEADER = "X-User-Token-Verified";
    private static final String ADMIN_VERIFIED_HEADER = "X-Admin-Token-Verified";
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
        ServerWebExchange sanitizedExchange = removeClientSuppliedIdentityHeaders(exchange);
        if (!authProperties.isEnabled()) {
            return chain.filter(sanitizedExchange);
        }
        ServerHttpRequest request = sanitizedExchange.getRequest();
        if (HttpMethod.OPTIONS.equals(request.getMethod())) {
            return chain.filter(sanitizedExchange);
        }
        String path = request.getURI().getPath();
        if (isInfraPath(path) || isInternalPath(path)) {
            return chain.filter(sanitizedExchange);
        }

        if (path.startsWith("/admin-api/")) {
            if (matchAny(path, authProperties.getAdminExcludePaths())) {
                return chain.filter(sanitizedExchange);
            }
            String adminToken = GatewayTokenResolver.resolveAdminToken(exchange);
            if (!StringUtils.hasText(adminToken)) {
                return unauthorized(sanitizedExchange, "登录超时");
            }
            return reactiveStringRedisTemplate.hasKey(REDIS_KEY_TOKEN_ADMIN + adminToken)
                    .flatMap(exists -> {
                        if (Boolean.TRUE.equals(exists)) {
                            ServerHttpRequest mutated = request.mutate()
                                    .headers(headers -> headers.set(ADMIN_VERIFIED_HEADER, "1"))
                                    .build();
                            return chain.filter(sanitizedExchange.mutate().request(mutated).build());
                        }
                        return unauthorized(sanitizedExchange, "登录超时");
                    });
        }

        // Keep the exact /ws endpoint under the same session gate as /ws/.
        // A Path=/ws/** route does not match the no-slash form.
        if (path.startsWith("/api/") || "/ws".equals(path) || path.startsWith("/ws/")) {
            if (path.startsWith("/api/") && matchAny(path, authProperties.getWebExcludePaths())) {
                return chain.filter(sanitizedExchange);
            }
            String token = GatewayTokenResolver.resolveWebToken(exchange);
            if (!StringUtils.hasText(token)) {
                return unauthorized(sanitizedExchange, "登录超时");
            }
            return reactiveStringRedisTemplate.opsForValue().get(REDIS_KEY_TOKEN_WEB + token)
                    .flatMap(sessionJson -> {
                        if (!StringUtils.hasText(sessionJson)) {
                            return unauthorized(sanitizedExchange, "登录超时");
                        }
                        String userId = extractUserId(sessionJson);
                        ServerHttpRequest.Builder builder = request.mutate()
                                .headers(headers -> headers.set(USER_VERIFIED_HEADER, "1"));
                        if (StringUtils.hasText(userId)) {
                            builder.headers(headers -> headers.set(USER_ID_HEADER, userId));
                        }
                        return chain.filter(sanitizedExchange.mutate().request(builder.build()).build());
                    })
                    .switchIfEmpty(unauthorized(sanitizedExchange, "登录超时"));
        }

        return chain.filter(sanitizedExchange);
    }

    private ServerWebExchange removeClientSuppliedIdentityHeaders(ServerWebExchange exchange) {
        ServerHttpRequest sanitized = exchange.getRequest().mutate()
                .headers(headers -> {
                    headers.remove(USER_ID_HEADER);
                    headers.remove(USER_VERIFIED_HEADER);
                    headers.remove(ADMIN_VERIFIED_HEADER);
                })
                .build();
        return exchange.mutate().request(sanitized).build();
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
        exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
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

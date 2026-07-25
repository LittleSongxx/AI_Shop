package com.aishop.web;

import com.aishop.constants.InternalApiHeaders;
import com.aishop.entity.enums.ResponseCodeEnum;
import com.aishop.utils.StringTools;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 20)
public class InternalApiAuthFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(InternalApiAuthFilter.class);

    @Value("${aishop.internal.token:your-token}")
    private String expectedToken;

    @Value("${aishop.internal.auth-enabled:true}")
    private boolean authEnabled;

    @Override
    protected void initFilterBean() {
        if (authEnabled && StringTools.isEmpty(expectedToken)) {
            log.error("aishop.internal.token 未配置，/internal/ 接口将全部拒绝；"
                    + "如需临时关闭校验请显式设置 aishop.internal.auth-enabled=false");
        }
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        if (!authEnabled) {
            return true;
        }
        String uri = request.getRequestURI();
        return uri == null || !uri.contains("/internal/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        String token = request.getHeader(InternalApiHeaders.INTERNAL_TOKEN);
        if (tokenMatches(token)) {
            filterChain.doFilter(request, response);
            return;
        }
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write("{\"status\":\"error\",\"code\":"
                + ResponseCodeEnum.CODE_901.getCode()
                + ",\"info\":\"非法内部调用\",\"data\":null}");
    }

    /**
     * 校验内部调用令牌。
     * <p>未配置令牌时一律拒绝：内部接口可绕过用户鉴权，配置缺失应当关闭入口而不是放开入口。
     * 需要临时免鉴权时请显式设置 aishop.internal.auth-enabled=false。
     * <p>使用常量时间比较，避免通过响应耗时逐字节猜测令牌。
     */
    private boolean tokenMatches(String token) {
        if (StringTools.isEmpty(expectedToken) || StringTools.isEmpty(token)) {
            return false;
        }
        return MessageDigest.isEqual(
                expectedToken.getBytes(StandardCharsets.UTF_8),
                token.getBytes(StandardCharsets.UTF_8));
    }
}

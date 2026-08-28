package com.aishop.interceptor;

import com.aishop.component.RedisComponent;
import com.aishop.constants.AdminPermissions;
import com.aishop.entity.dto.AdminPrincipalDTO;
import com.aishop.entity.enums.ResponseCodeEnum;
import com.aishop.exception.HttpBusinessException;
import com.aishop.security.AdminSecurityContext;
import com.aishop.security.RequireAdminPermission;
import com.aishop.utils.AuthCookieHelper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;

import java.util.Arrays;
import java.util.Map;
import java.util.UUID;

@Component
@ConditionalOnProperty(name = "aishop.security.admin-interceptor", havingValue = "true")
public class AppInterceptor implements HandlerInterceptor {

    @Resource
    private RedisComponent redisComponent;

    @Resource
    private AuthCookieHelper authCookieHelper;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        if (null == handler) {
            return false;
        }
        if (!(handler instanceof HandlerMethod)) {
            return true;
        }
        String token = authCookieHelper.resolveAdminToken(request);
        if (StringTools.isEmpty(token)) {
            throw new HttpBusinessException(401, ResponseCodeEnum.CODE_901.getMsg());
        }
        AdminPrincipalDTO principal = redisComponent.getAdminPrincipal(token);
        if (principal == null) {
            throw new HttpBusinessException(401, ResponseCodeEnum.CODE_901.getMsg());
        }
        request.setAttribute(AdminSecurityContext.REQUEST_ATTRIBUTE, principal);
        AdminSecurityContext.set(principal);
        try {
            enforcePermission((HandlerMethod) handler, principal, request);
            return true;
        } catch (RuntimeException e) {
            AdminSecurityContext.clear();
            throw e;
        }
    }

    @Override
    public void afterCompletion(
            HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) {
        AdminSecurityContext.clear();
    }

    private void enforcePermission(
            HandlerMethod handler, AdminPrincipalDTO principal, HttpServletRequest request) {
        if (principal.hasRole(AdminPermissions.SUPER_ADMIN_ROLE)) {
            return;
        }
        RequireAdminPermission requirement = handler.getMethodAnnotation(RequireAdminPermission.class);
        if (requirement == null) {
            requirement = handler.getBeanType().getAnnotation(RequireAdminPermission.class);
        }
        if (requirement == null) {
            if (!principal.hasPermission(AdminPermissions.ADMIN_LEGACY)) {
                throw new HttpBusinessException(403, ResponseCodeEnum.CODE_403.getMsg());
            }
            return;
        }
        String[] required = requirement.value();
        if (required.length == 0) {
            return;
        }
        boolean allowed = requirement.requireAll()
                ? Arrays.stream(required).allMatch(principal::hasPermission)
                : Arrays.stream(required).anyMatch(principal::hasPermission);
        if (!allowed) {
            boolean analyticsExport = Arrays.stream(required)
                    .anyMatch(AdminPermissions.ANALYTICS_EXPORT::equals);
            boolean analyticsRead = Arrays.stream(required)
                    .anyMatch(AdminPermissions.ANALYTICS_READ::equals);
            if (analyticsExport || analyticsRead) {
                String reasonCode = analyticsExport
                        ? "ANALYTICS_EXPORT_REQUIRED"
                        : "ANALYTICS_READ_REQUIRED";
                String requestId = request.getHeader("X-Request-ID");
                if (StringTools.isEmpty(requestId)) {
                    requestId = UUID.randomUUID().toString().replace("-", "");
                }
                throw new HttpBusinessException(
                        403,
                        ResponseCodeEnum.CODE_403.getMsg(),
                        Map.of(
                                "outcome", "DENY",
                                "completion", "NOT_APPLICABLE",
                                "status", reasonCode,
                                "reasonCode", reasonCode,
                                "requestId", requestId));
            }
            throw new HttpBusinessException(403, ResponseCodeEnum.CODE_403.getMsg());
        }
    }
}

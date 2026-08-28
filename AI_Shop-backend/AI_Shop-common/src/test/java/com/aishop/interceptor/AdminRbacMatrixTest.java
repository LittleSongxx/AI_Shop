package com.aishop.interceptor;

import com.aishop.component.RedisComponent;
import com.aishop.constants.AdminPermissions;
import com.aishop.entity.dto.AdminPrincipalDTO;
import com.aishop.exception.HttpBusinessException;
import com.aishop.security.AdminSecurityContext;
import com.aishop.security.RequireAdminPermission;
import com.aishop.utils.AuthCookieHelper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.method.HandlerMethod;

import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class AdminRbacMatrixTest {

    private static final Map<String, Set<String>> ROLE_PERMISSIONS = rolePermissions();

    @AfterEach
    void clearContext() {
        AdminSecurityContext.clear();
    }

    @Test
    void fiveRolesEnforceTheAllowAndDenyMatrix() throws Exception {
        Map<String, String> endpoints = Map.ofEntries(
                Map.entry("manage", AdminPermissions.ADMIN_MANAGE),
                Map.entry("aiConfig", AdminPermissions.AI_CONFIG),
                Map.entry("aiEvaluate", AdminPermissions.AI_EVALUATE),
                Map.entry("aiPilot", AdminPermissions.AI_PILOT),
                Map.entry("supportRead", AdminPermissions.SUPPORT_READ),
                Map.entry("supportWrite", AdminPermissions.SUPPORT_WRITE),
                Map.entry("analyticsRead", AdminPermissions.ANALYTICS_READ),
                Map.entry("analyticsExport", AdminPermissions.ANALYTICS_EXPORT),
                Map.entry("auditRead", AdminPermissions.AUDIT_READ));

        for (Map.Entry<String, Set<String>> role : ROLE_PERMISSIONS.entrySet()) {
            for (Map.Entry<String, String> endpoint : endpoints.entrySet()) {
                boolean expected = AdminPermissions.SUPER_ADMIN_ROLE.equals(role.getKey())
                        || role.getValue().contains(endpoint.getValue());
                assertPermission(role.getKey(), role.getValue(), endpoint.getKey(), expected);
            }
        }
    }

    @Test
    void anyOfPermissionAllowsAuditorButStillDeniesUnrelatedRole() throws Exception {
        assertPermission("AUDITOR", ROLE_PERMISSIONS.get("AUDITOR"), "supportOrAudit", true);
        assertPermission(
                "DATA_ANALYST",
                ROLE_PERMISSIONS.get("DATA_ANALYST"),
                "supportOrAudit",
                false);
    }

    @Test
    void missingOrExpiredSessionReturnsStandardUnauthorizedStatus() throws Exception {
        Fixture missingToken = fixture(null, null);
        HttpBusinessException missing = assertThrows(
                HttpBusinessException.class,
                () -> missingToken.interceptor().preHandle(
                        missingToken.request(), missingToken.response(), handler("aiConfig")));
        assertEquals(401, missing.getHttpStatus());
        assertEquals(401, missing.getCode());

        Fixture expired = fixture("expired-token", null);
        HttpBusinessException invalid = assertThrows(
                HttpBusinessException.class,
                () -> expired.interceptor().preHandle(
                        expired.request(), expired.response(), handler("aiConfig")));
        assertEquals(401, invalid.getHttpStatus());
        assertEquals(401, invalid.getCode());
    }

    @Test
    void analyticsPermissionDenialCarriesStructuredOutcomeAndRequestId() throws Exception {
        AdminPrincipalDTO principal = new AdminPrincipalDTO();
        principal.setAdminId("admin-support");
        principal.setAccount("support");
        principal.setRoles(Set.of("SUPPORT_AGENT"));
        principal.setPermissions(ROLE_PERMISSIONS.get("SUPPORT_AGENT"));
        principal.setSessionVersion(1L);
        Fixture fixture = fixture("valid-token", principal);
        when(fixture.request().getHeader("X-Request-ID")).thenReturn("request-analytics-denied");

        HttpBusinessException denied = assertThrows(
                HttpBusinessException.class,
                () -> fixture.interceptor().preHandle(
                        fixture.request(), fixture.response(), handler("analyticsRead")));

        assertEquals(403, denied.getHttpStatus());
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) denied.getData();
        assertEquals("DENY", data.get("outcome"));
        assertEquals("NOT_APPLICABLE", data.get("completion"));
        assertEquals("ANALYTICS_READ_REQUIRED", data.get("reasonCode"));
        assertEquals("request-analytics-denied", data.get("requestId"));
    }

    private static void assertPermission(
            String role,
            Set<String> permissions,
            String method,
            boolean expected) throws Exception {
        AdminPrincipalDTO principal = new AdminPrincipalDTO();
        principal.setAdminId("admin-" + role.toLowerCase());
        principal.setAccount(role.toLowerCase());
        principal.setRoles(Set.of(role));
        principal.setPermissions(permissions);
        principal.setSessionVersion(1L);

        Fixture fixture = fixture("valid-token", principal);
        if (expected) {
            assertTrue(fixture.interceptor().preHandle(
                    fixture.request(), fixture.response(), handler(method)),
                    () -> role + " should be allowed to call " + method);
            assertEquals(principal, AdminSecurityContext.requirePrincipal());
            fixture.interceptor().afterCompletion(
                    fixture.request(), fixture.response(), handler(method), null);
            return;
        }

        HttpBusinessException denied = assertThrows(
                HttpBusinessException.class,
                () -> fixture.interceptor().preHandle(
                        fixture.request(), fixture.response(), handler(method)),
                () -> role + " should be denied from " + method);
        assertEquals(403, denied.getHttpStatus());
        assertEquals(403, denied.getCode());
    }

    private static Fixture fixture(String token, AdminPrincipalDTO principal) {
        RedisComponent redis = mock(RedisComponent.class);
        AuthCookieHelper cookies = mock(AuthCookieHelper.class);
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mock(HttpServletResponse.class);
        when(cookies.resolveAdminToken(request)).thenReturn(token);
        if (token != null) {
            when(redis.getAdminPrincipal(token)).thenReturn(principal);
        }

        AppInterceptor interceptor = new AppInterceptor();
        ReflectionTestUtils.setField(interceptor, "redisComponent", redis);
        ReflectionTestUtils.setField(interceptor, "authCookieHelper", cookies);
        return new Fixture(interceptor, request, response);
    }

    private static HandlerMethod handler(String methodName) throws Exception {
        Method method = PermissionEndpoints.class.getDeclaredMethod(methodName);
        return new HandlerMethod(new PermissionEndpoints(), method);
    }

    private static Map<String, Set<String>> rolePermissions() {
        Map<String, Set<String>> roles = new LinkedHashMap<>();
        roles.put(AdminPermissions.SUPER_ADMIN_ROLE, Set.of());
        roles.put("AI_OPERATOR", Set.of(
                AdminPermissions.AI_CONFIG,
                AdminPermissions.AI_EVALUATE,
                AdminPermissions.AI_PILOT,
                AdminPermissions.ANALYTICS_READ));
        roles.put("SUPPORT_AGENT", Set.of(
                AdminPermissions.SUPPORT_READ,
                AdminPermissions.SUPPORT_WRITE));
        roles.put("DATA_ANALYST", Set.of(
                AdminPermissions.ANALYTICS_READ,
                AdminPermissions.ANALYTICS_EXPORT));
        roles.put("AUDITOR", Set.of(AdminPermissions.AUDIT_READ));
        return roles;
    }

    private record Fixture(
            AppInterceptor interceptor,
            HttpServletRequest request,
            HttpServletResponse response) {
    }

    private static final class PermissionEndpoints {
        @RequireAdminPermission(AdminPermissions.ADMIN_MANAGE)
        void manage() {
        }

        @RequireAdminPermission(AdminPermissions.AI_CONFIG)
        void aiConfig() {
        }

        @RequireAdminPermission(AdminPermissions.AI_EVALUATE)
        void aiEvaluate() {
        }

        @RequireAdminPermission(AdminPermissions.AI_PILOT)
        void aiPilot() {
        }

        @RequireAdminPermission(AdminPermissions.SUPPORT_READ)
        void supportRead() {
        }

        @RequireAdminPermission(AdminPermissions.SUPPORT_WRITE)
        void supportWrite() {
        }

        @RequireAdminPermission(AdminPermissions.ANALYTICS_READ)
        void analyticsRead() {
        }

        @RequireAdminPermission(AdminPermissions.ANALYTICS_EXPORT)
        void analyticsExport() {
        }

        @RequireAdminPermission(AdminPermissions.AUDIT_READ)
        void auditRead() {
        }

        @RequireAdminPermission(
                value = {AdminPermissions.SUPPORT_READ, AdminPermissions.AUDIT_READ},
                requireAll = false)
        void supportOrAudit() {
        }
    }
}

package com.aishop.security;

import com.aishop.entity.dto.AdminPrincipalDTO;
import com.aishop.exception.HttpBusinessException;

public final class AdminSecurityContext {

    public static final String REQUEST_ATTRIBUTE = AdminSecurityContext.class.getName() + ".principal";

    private static final ThreadLocal<AdminPrincipalDTO> CURRENT = new ThreadLocal<>();

    public static void set(AdminPrincipalDTO principal) {
        CURRENT.set(principal);
    }

    public static AdminPrincipalDTO current() {
        return CURRENT.get();
    }

    public static AdminPrincipalDTO requirePrincipal() {
        AdminPrincipalDTO principal = current();
        if (principal == null) {
            throw new HttpBusinessException(401, "管理员登录已失效");
        }
        return principal;
    }

    public static void clear() {
        CURRENT.remove();
    }

    private AdminSecurityContext() {
    }
}

package com.aishop.controller.admin;

import com.aishop.component.AdminLoginLockService;
import com.aishop.component.RedisComponent;
import com.aishop.biz.AdminIdentityService;
import com.aishop.constants.AdminPermissions;
import com.aishop.entity.dto.AdminPrincipalDTO;
import com.aishop.entity.vo.CheckCodeVO;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import com.aishop.security.AdminSecurityContext;
import com.aishop.security.RequireAdminPermission;
import com.aishop.utils.AuthCookieHelper;
import com.aishop.utils.CheckCodeGenerator;
import com.aishop.utils.IpUtils;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.constraints.NotEmpty;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

@RestController("adminAccountController")
@RequestMapping("/admin/account")
@Validated
public class AccountController extends com.aishop.controller.admin.ABaseController {
    @Resource
    private RedisComponent redisComponent;

    @Resource
    private AuthCookieHelper authCookieHelper;

    @Resource
    private AdminLoginLockService adminLoginLockService;

    @Resource
    private AdminIdentityService adminIdentityService;

    @PostMapping("/checkCode")
    public ResponseVO checkCode(){
        CheckCodeVO checkCodeVO = CheckCodeGenerator.generate(redisComponent);
        return getSuccessResponseVO(checkCodeVO);
    }

    @PostMapping("/login")
    public ResponseVO login(@NotEmpty String account,
                            @NotEmpty String password,
                            @NotEmpty String checkCode,
                            @NotEmpty String checkCodeKey){
        HttpServletRequest request = currentRequest();
        String ip = IpUtils.resolveClientIp(request);
        adminLoginLockService.ensureNotLocked(ip);
        try {
            if (!checkCode.equalsIgnoreCase(redisComponent.getCheckCode(checkCodeKey))){
                throw new BusinessException("验证码错误！");
            }
            AdminPrincipalDTO principal = adminIdentityService.authenticate(account, password);
            adminLoginLockService.clearFailures(ip);
            String token = redisComponent.saveToken4Admin(principal);
            HttpServletResponse response = currentResponse();
            authCookieHelper.writeAdminTokenCookie(request, response, token);
            return getSuccessResponseVO(null);
        } catch (BusinessException e) {
            adminLoginLockService.recordFailure(ip);
            throw e;
        } finally {
            redisComponent.cleanCheckCode(checkCodeKey);
        }
    }

    @PostMapping("/logout")
    public ResponseVO logout(){
        HttpServletRequest request = currentRequest();
        HttpServletResponse response = currentResponse();
        String token = authCookieHelper.resolveAdminToken(request);
        if (!StringTools.isEmpty(token)) {
            redisComponent.cleanToken4Admin(token);
        }
        authCookieHelper.clearAdminTokenCookie(request, response);
        return getSuccessResponseVO(null);
    }

    @GetMapping("/me")
    public ResponseVO me() {
        return getSuccessResponseVO(AdminSecurityContext.requirePrincipal());
    }

    @GetMapping("/roles")
    @RequireAdminPermission(AdminPermissions.ADMIN_MANAGE)
    public ResponseVO roles() {
        return getSuccessResponseVO(adminIdentityService.listRoles());
    }

    @GetMapping("/administrators")
    @RequireAdminPermission(AdminPermissions.ADMIN_MANAGE)
    public ResponseVO administrators() {
        return getSuccessResponseVO(adminIdentityService.listAdministrators());
    }

    @PostMapping("/administrators")
    @RequireAdminPermission(AdminPermissions.ADMIN_MANAGE)
    public ResponseVO createAdministrator(@RequestBody Map<String, Object> body) {
        AdminPrincipalDTO actor = AdminSecurityContext.requirePrincipal();
        return getSuccessResponseVO(adminIdentityService.createAdministrator(
                Long.parseLong(actor.getAdminId()),
                string(body.get("account")),
                string(body.get("password")),
                string(body.get("displayName")),
                roles(body.get("roles"))));
    }

    @PutMapping("/administrators/{adminId}/roles")
    @RequireAdminPermission(AdminPermissions.ADMIN_MANAGE)
    public ResponseVO updateRoles(
            @PathVariable long adminId, @RequestBody Map<String, Object> body) {
        AdminPrincipalDTO actor = AdminSecurityContext.requirePrincipal();
        return getSuccessResponseVO(adminIdentityService.updateRoles(
                Long.parseLong(actor.getAdminId()), adminId, roles(body.get("roles"))));
    }

    @PutMapping("/administrators/{adminId}/status")
    @RequireAdminPermission(AdminPermissions.ADMIN_MANAGE)
    public ResponseVO updateStatus(
            @PathVariable long adminId, @RequestBody Map<String, Object> body) {
        Object enabled = body.get("enabled");
        if (!(enabled instanceof Boolean)) {
            throw new BusinessException("enabled 必须为布尔值");
        }
        AdminPrincipalDTO actor = AdminSecurityContext.requirePrincipal();
        adminIdentityService.updateStatus(
                Long.parseLong(actor.getAdminId()), adminId, (Boolean) enabled);
        return getSuccessResponseVO(null);
    }

    private Set<String> roles(Object value) {
        if (!(value instanceof List<?> list)) {
            throw new BusinessException("roles 必须为数组");
        }
        Set<String> roles = new LinkedHashSet<>();
        for (Object item : list) {
            if (item != null && !StringTools.isEmpty(String.valueOf(item))) {
                roles.add(String.valueOf(item));
            }
        }
        return roles;
    }

    private String string(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private HttpServletRequest currentRequest() {
        return ((ServletRequestAttributes) RequestContextHolder.getRequestAttributes()).getRequest();
    }

    private HttpServletResponse currentResponse() {
        return ((ServletRequestAttributes) RequestContextHolder.getRequestAttributes()).getResponse();
    }
}

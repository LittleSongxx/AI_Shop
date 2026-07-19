package com.simlect.aspect;

import com.simlect.annotation.AdminSensitiveConfirm;
import com.simlect.entity.config.AppConfig;
import com.simlect.exception.BusinessException;
import com.simlect.service.PasswordService;
import com.simlect.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

@Aspect
@Component
public class AdminSensitiveConfirmAspect {

    public static final String CONFIRM_PWD_HEADER = "X-Admin-Confirm-Pwd";

    @Resource
    private AppConfig appConfig;

    @Resource
    private PasswordService passwordService;

    @Before("@annotation(com.simlect.annotation.AdminSensitiveConfirm)")
    public void beforeSensitive() {
        HttpServletRequest request = currentRequest();
        if (request == null) {
            throw new BusinessException("请求无效");
        }
        String confirmPwd = request.getHeader(CONFIRM_PWD_HEADER);
        if (StringTools.isEmpty(confirmPwd)) {
            throw new BusinessException("敏感操作需二次确认，请输入管理员密码");
        }
        if (!passwordService.matches(confirmPwd, appConfig.getAdminPasswordHash())) {
            throw new BusinessException("管理员密码确认失败");
        }
    }

    private HttpServletRequest currentRequest() {
        ServletRequestAttributes attrs = (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
        return attrs == null ? null : attrs.getRequest();
    }
}

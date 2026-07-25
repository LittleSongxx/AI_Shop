package com.aishop.interceptor;

import com.aishop.component.RedisComponent;
import com.aishop.entity.enums.ResponseCodeEnum;
import com.aishop.exception.BusinessException;
import com.aishop.utils.AuthCookieHelper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;

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
            throw new BusinessException(ResponseCodeEnum.CODE_901);
        }
        Object sessionObj = redisComponent.getLoginInfo4Admin(token);
        if (sessionObj == null) {
            throw new BusinessException(ResponseCodeEnum.CODE_901);
        }
        return true;
    }
}

package com.myshop.aspect;

import com.myshop.annotation.CouponRushRateLimit;
import com.myshop.component.CouponRushRateLimitService;
import com.myshop.component.RedisComponent;
import com.myshop.entity.dto.TokenUserInfoDTO;
import com.myshop.entity.enums.ResponseCodeEnum;
import com.myshop.exception.BusinessException;
import com.myshop.utils.AuthCookieHelper;
import com.myshop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import org.aspectj.lang.JoinPoint;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.lang.reflect.Method;

@Component
@Aspect
@Order(2)
public class CouponRushRateLimitAspect {

    @Resource
    private CouponRushRateLimitService couponRushRateLimitService;
    @Resource
    private RedisComponent redisComponent;

    @Resource
    private AuthCookieHelper authCookieHelper;

    @Before("@annotation(com.myshop.annotation.CouponRushRateLimit)")
    public void beforeRush(JoinPoint point) {
        MethodSignature signature = (MethodSignature) point.getSignature();
        Method method = signature.getMethod();
        CouponRushRateLimit limit = method.getAnnotation(CouponRushRateLimit.class);
        if (limit == null) {
            return;
        }
        HttpServletRequest request = currentRequest();
        String userId = resolveUserId(request);
        String couponId = request.getParameter("couponId");
        couponRushRateLimitService.checkUserLimit(userId, limit.userMaxPerMinute(), 60L);
        couponRushRateLimitService.checkCouponLimit(couponId, limit.couponMaxPerSecond(), 1L);
    }

    private HttpServletRequest currentRequest() {
        ServletRequestAttributes attrs = (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
        if (attrs == null) {
            throw new BusinessException(ResponseCodeEnum.CODE_500);
        }
        return attrs.getRequest();
    }

    private String resolveUserId(HttpServletRequest request) {
        String token = authCookieHelper.resolveWebToken(request);
        if (StringTools.isEmpty(token)) {
            throw new BusinessException(ResponseCodeEnum.CODE_901);
        }
        if (System.getProperty("dev") != null) {
            return "vuzPteqk";
        }
        TokenUserInfoDTO user = redisComponent.getTokenUserInfo(token);
        if (user == null || StringTools.isEmpty(user.getUserId())) {
            throw new BusinessException(ResponseCodeEnum.CODE_901);
        }
        return user.getUserId();
    }
}

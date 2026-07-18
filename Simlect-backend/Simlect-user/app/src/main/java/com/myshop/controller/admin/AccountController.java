package com.myshop.controller.admin;

import com.myshop.component.AdminLoginLockService;
import com.myshop.component.RedisComponent;
import com.myshop.entity.config.AppConfig;
import com.myshop.entity.vo.CheckCodeVO;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.exception.BusinessException;
import com.myshop.service.PasswordService;
import com.myshop.utils.AuthCookieHelper;
import com.myshop.utils.CheckCodeGenerator;
import com.myshop.utils.IpUtils;
import com.myshop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.constraints.NotEmpty;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

@RestController("adminAccountController")
@RequestMapping("/admin/account")
@Validated
public class AccountController extends com.myshop.controller.admin.ABaseController {
    @Resource
    private AppConfig appConfig;

    @Resource
    private RedisComponent redisComponent;

    @Resource
    private AuthCookieHelper authCookieHelper;

    @Resource
    private AdminLoginLockService adminLoginLockService;

    @Resource
    private PasswordService passwordService;

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
            if(!account.equals(appConfig.getAdminAccount()) || !passwordService.matches(password, appConfig.getAdminPasswordHash())){
                throw new BusinessException("账号或密码错误！");
            }
            adminLoginLockService.clearFailures(ip);
            String token = redisComponent.saveToken4Admin(account);
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

    private HttpServletRequest currentRequest() {
        return ((ServletRequestAttributes) RequestContextHolder.getRequestAttributes()).getRequest();
    }

    private HttpServletResponse currentResponse() {
        return ((ServletRequestAttributes) RequestContextHolder.getRequestAttributes()).getResponse();
    }
}

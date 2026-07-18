package com.myshop.config;

import com.myshop.captcha.CaptchaCacheServiceRedisImpl;
import com.xingyuv.captcha.service.CaptchaCacheService;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@ConditionalOnProperty(prefix = "aj.captcha", name = "cache-type", havingValue = "redis")
public class CaptchaRedisConfiguration {

    @Bean(name = "AjCaptchaCacheService")
    public CaptchaCacheService captchaCacheService() {
        return new CaptchaCacheServiceRedisImpl();
    }
}

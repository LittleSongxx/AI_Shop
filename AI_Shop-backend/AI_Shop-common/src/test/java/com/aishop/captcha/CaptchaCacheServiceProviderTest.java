package com.aishop.captcha;

import com.xingyuv.captcha.service.CaptchaCacheService;
import org.junit.jupiter.api.Test;

import java.util.ServiceLoader;

import static org.junit.jupiter.api.Assertions.assertTrue;

class CaptchaCacheServiceProviderTest {

    @Test
    void serviceProviderPointsToTheRedisImplementation() {
        boolean found = ServiceLoader.load(CaptchaCacheService.class)
                .stream()
                .map(ServiceLoader.Provider::type)
                .anyMatch(CaptchaCacheServiceRedisImpl.class::equals);

        assertTrue(found, "Captcha Redis SPI provider must be loadable");
    }
}

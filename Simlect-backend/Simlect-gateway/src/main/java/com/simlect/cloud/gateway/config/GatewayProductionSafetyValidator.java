package com.simlect.cloud.gateway.config;

import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

@Component
public class GatewayProductionSafetyValidator {

    private static final Logger log = LoggerFactory.getLogger(GatewayProductionSafetyValidator.class);
    private static final String WEAK_INTERNAL = "your-token";

    @Value("${simlect.production-ready:false}")
    private boolean productionReady;

    @Value("${simlect.internal.token:your-token}")
    private String internalToken;

    @PostConstruct
    public void validate() {
        if (!productionReady) {
            log.warn("simlect.production-ready=false（Gateway）：上线前请设为 true 并配置强内部 Token");
            return;
        }
        if (!StringUtils.hasText(internalToken) || WEAK_INTERNAL.equals(internalToken)) {
            throw new IllegalStateException("生产就绪校验失败: 请设置强 SIMLECT_INTERNAL_TOKEN");
        }
        log.info("Gateway 生产就绪校验通过");
    }
}

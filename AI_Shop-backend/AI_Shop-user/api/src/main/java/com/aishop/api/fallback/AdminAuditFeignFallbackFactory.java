package com.aishop.api.fallback;

import com.aishop.api.AdminAuditFeignClient;
import com.aishop.api.dto.AdminAuditLogDTO;
import com.aishop.api.support.FeignFallbackResponses;
import com.aishop.entity.vo.ResponseVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.cloud.openfeign.FallbackFactory;
import org.springframework.stereotype.Component;

@Slf4j
@Component
public class AdminAuditFeignFallbackFactory implements FallbackFactory<AdminAuditFeignClient> {

    @Override
    public AdminAuditFeignClient create(Throwable cause) {
        log.warn("Admin audit Feign fallback: {}", cause == null ? "unknown" : cause.toString());
        return dto -> FeignFallbackResponses.unavailable("管理服务");
    }
}

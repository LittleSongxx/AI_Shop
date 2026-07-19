package com.simlect.api.support;

import com.simlect.api.AdminAuditFeignClient;
import com.simlect.api.dto.AdminAuditLogDTO;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

@Slf4j
@Component
public class AdminAuditFeignSupport {

    @Resource
    private AdminAuditFeignClient adminAuditFeignClient;

    public void log(String operator, String action, String targetUserId, String detail) {
        try {
            adminAuditFeignClient.log(new AdminAuditLogDTO(operator, action, targetUserId, detail));
        } catch (Exception e) {
            log.warn("写入管理端审计日志失败 action={}, operator={}", action, operator, e);
        }
    }
}

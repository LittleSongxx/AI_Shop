package com.aishop.api;

import com.aishop.api.dto.AdminAuditLogDTO;
import com.aishop.api.fallback.AdminAuditFeignFallbackFactory;
import com.aishop.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "aishop-admin", contextId = "adminAuditFeignClient", path = "/internal/admin/audit",
        fallbackFactory = AdminAuditFeignFallbackFactory.class)
public interface AdminAuditFeignClient {

    @PostMapping("/log")
    ResponseVO<Void> log(@RequestBody AdminAuditLogDTO dto);
}

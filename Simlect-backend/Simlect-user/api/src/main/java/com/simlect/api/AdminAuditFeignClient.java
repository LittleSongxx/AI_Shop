package com.simlect.api;

import com.simlect.api.dto.AdminAuditLogDTO;
import com.simlect.api.fallback.AdminAuditFeignFallbackFactory;
import com.simlect.entity.vo.ResponseVO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "simlect-admin", contextId = "adminAuditFeignClient", path = "/internal/admin/audit",
        fallbackFactory = AdminAuditFeignFallbackFactory.class)
public interface AdminAuditFeignClient {

    @PostMapping("/log")
    ResponseVO<Void> log(@RequestBody AdminAuditLogDTO dto);
}

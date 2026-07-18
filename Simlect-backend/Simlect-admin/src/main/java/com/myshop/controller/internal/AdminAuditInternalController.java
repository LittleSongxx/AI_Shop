package com.myshop.controller.internal;

import com.myshop.biz.AdminAuditLogService;
import com.myshop.controller.ABaseController;
import com.myshop.entity.dto.AdminAuditLogDTO;
import com.myshop.entity.vo.ResponseVO;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/admin/audit")
public class AdminAuditInternalController extends ABaseController {

    @Resource
    private AdminAuditLogService adminAuditLogService;

    @PostMapping("/log")
    public ResponseVO<Void> log(@RequestBody AdminAuditLogDTO dto) {
        if (dto != null) {
            adminAuditLogService.log(dto.getOperator(), dto.getAction(), dto.getTargetUserId(), dto.getDetail());
        }
        return getSuccessResponseVO(null);
    }
}

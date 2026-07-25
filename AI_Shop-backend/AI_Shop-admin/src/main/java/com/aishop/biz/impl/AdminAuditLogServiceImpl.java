package com.aishop.biz.impl;

import com.aishop.biz.AdminAuditLogService;
import com.aishop.entity.po.AdminAuditLog;
import com.aishop.entity.query.AdminAuditLogQuery;
import com.aishop.mappers.AdminAuditLogMapper;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.Date;

@Service
@Slf4j
public class AdminAuditLogServiceImpl implements AdminAuditLogService {

    @Resource
    private AdminAuditLogMapper<AdminAuditLog, AdminAuditLogQuery> adminAuditLogMapper;

    @Override
    public void log(String operator, String action, String targetUserId, String detail) {
        if (StringTools.isEmpty(action)) {
            return;
        }
        try {
            AdminAuditLog row = new AdminAuditLog();
            row.setOperator(StringTools.isEmpty(operator) ? "unknown" : operator);
            row.setAction(action);
            row.setTargetUserId(targetUserId);
            row.setDetail(detail);
            row.setCreateTime(new Date());
            adminAuditLogMapper.insert(row);
        } catch (Exception e) {
            log.error("写入 admin_audit_log 失败 action={}, operator={}", action, operator, e);
        }
    }
}

package com.aishop.biz;

public interface AdminAuditLogService {

    void log(String operator, String action, String targetUserId, String detail);
}

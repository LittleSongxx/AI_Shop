package com.myshop.biz;

public interface AdminAuditLogService {

    void log(String operator, String action, String targetUserId, String detail);
}

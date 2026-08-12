package com.aishop.controller.admin;

import com.aishop.constants.AdminPermissions;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.api.vo.SignDateSyncResultVO;
import com.aishop.api.vo.SignRecordSyncResultVO;
import com.aishop.exception.BusinessException;
import com.aishop.biz.SignCalendarCacheService;
import com.aishop.biz.SignRecordSyncService;
import com.aishop.security.AdminSecurityContext;
import com.aishop.security.RequireAdminPermission;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/admin/signRecord")
public class SignRecordAdminController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private SignRecordSyncService signRecordSyncService;
    @Resource
    private SignCalendarCacheService signCalendarCacheService;

    @PostMapping("/syncAllFromDb")
    @RequireAdminPermission(AdminPermissions.ADMIN_LEGACY)
    public ResponseVO syncAllFromDb(Boolean force) {
        boolean overwrite = force == null || force;
        SignRecordSyncResultVO result = signRecordSyncService.syncAllFromDb(overwrite);
        return getSuccessResponseVO(result);
    }

    @PostMapping("/syncUserFromDb")
    @RequireAdminPermission(AdminPermissions.ADMIN_LEGACY)
    public ResponseVO syncUserFromDb(String userId, Boolean force) {
        if (StringTools.isEmpty(userId)) {
            throw new BusinessException("userId 不能为空");
        }
        boolean overwrite = force != null && force;
        boolean synced = signRecordSyncService.syncUserFromDb(userId.trim(), overwrite);
        if (!synced) {
            throw new BusinessException("DB 无该用户签到记录，或 Redis 已有且未勾选强制覆盖");
        }
        return getSuccessResponseVO(null);
    }

    @PostMapping("/syncSignDatesFromDb")
    @RequireAdminPermission(AdminPermissions.ADMIN_LEGACY)
    public ResponseVO syncSignDatesFromDb(String syncEndDate, String userId) {
        SignDateSyncResultVO result;
        if (StringTools.isEmpty(userId)) {
            result = signCalendarCacheService.syncAllSignDatesFromDb(syncEndDate, false);
        } else {
            result = signCalendarCacheService.syncSignDatesFromDb(userId.trim(), syncEndDate, false);
        }
        return getSuccessResponseVO(result);
    }

    @PostMapping("/forceRebuildToday")
    @RequireAdminPermission(AdminPermissions.ADMIN_LEGACY)
    public ResponseVO forceRebuildToday(String userId) {
        String operator = AdminSecurityContext.requirePrincipal().getAccount();
        SignDateSyncResultVO result = signCalendarCacheService.forceRebuildToday(userId, operator);
        return getSuccessResponseVO(result);
    }
}
